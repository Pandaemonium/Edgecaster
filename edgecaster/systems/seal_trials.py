"""
Sealing rune trials.

This module owns:
- Building a target pattern from a generator chain.
- Removing a "missing chunk" (stored as removed edges).
- Scoring the player's pattern against the missing region (blurred overlap).
- Temporary ability grants while inside a trial zone.
- Sealing logic using a coherence crystal.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

from edgecaster.content.sealing_runes import SealPatternStep, SealTrialDef, get_seal_trial
from edgecaster.patterns import builder
from edgecaster.state.patterns import Edge, Pattern, Segment
from edgecaster.systems import inventory as inventory_system

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState


@dataclass
class SealTrialState:
    trial_id: str
    name: str
    target_pattern: Pattern
    target_anchor: Tuple[int, int]
    root_tile: Tuple[int, int]
    terminus_tile: Tuple[int, int]
    missing_center: Tuple[float, float]
    missing_radius: float
    missing_edge_keys: Set[Tuple[int, int]]
    missing_edges: List[Edge]
    snap_radius: int
    score_threshold: float
    blur_radius: int
    required_generators: List[str] = field(default_factory=list)
    param_overrides: Dict[str, Dict[str, object]] = field(default_factory=dict)
    ready_to_seal: bool = False
    sealed: bool = False
    last_score: float = 0.0
    announced_ready: bool = False
    seal_prompted: bool = False
    grants_applied: bool = False
    granted_actions: List[str] = field(default_factory=list)
    check_interval: int = 5
    last_debug_tick: int = -9999


def attach_trial_to_level(game: "Game", level: "LevelState", trial_id: str) -> None:
    """Attach a sealing rune trial to a level (from POI structure)."""
    trial_def = get_seal_trial(trial_id)
    if trial_def is None:
        if hasattr(game, "_debug"):
            game._debug(f"[seal_trials] Unknown trial id {trial_id!r}")
        return

    norm_steps, overrides = _normalize_trial_chain(game, trial_def)
    expanded_steps = _expand_chain_steps(norm_steps)

    # Cache the *baseline* place range before trials start adjusting it.
    try:
        base_place_range = float(getattr(game, "place_range", 0.0))
    except Exception:
        base_place_range = float(getattr(game.cfg, "place_range", 0.0))

    if trial_def.full_span_ratio is not None:
        full_root, full_term = _resolve_full_rune_line(
            game,
            level,
            trial_def,
            expanded_steps,
        )
    else:
        full_root = _resolve_root_tile(level, trial_def.root_offset)
        full_root, full_term = _resolve_terminus_tile(level, full_root, trial_def.terminus_offset)

    target_pattern = _build_target_pattern(game, full_root, full_term, norm_steps)

    # Defaults: missing chunk defined by the legacy center/radius (fallback path).
    repair_root = full_root
    repair_term = full_term
    missing_center = trial_def.missing_center
    missing_radius = trial_def.missing_radius
    missing_edges, missing_keys = _pick_missing_edges(
        target_pattern,
        missing_center,
        missing_radius,
    )

    required_generators = list(trial_def.required_generators) or _unique_generators(norm_steps)

    # New path: pick a repairable chunk from the full rune that the player can rebuild.
    repair_info = _pick_repairable_chunk(
        game,
        level,
        trial_def,
        target_pattern,
        full_root,
        full_term,
        expanded_steps,
        base_place_range,
    )
    if repair_info is not None:
        repair_root = repair_info["root_tile"]
        repair_term = repair_info["terminus_tile"]
        missing_center = repair_info["missing_center"]
        missing_radius = repair_info["missing_radius"]
        missing_edges = repair_info["missing_edges"]
        missing_keys = repair_info["missing_keys"]
        if repair_info.get("required_generators"):
            required_generators = list(repair_info["required_generators"])

    level.seal_trial = SealTrialState(
        trial_id=trial_def.id,
        name=trial_def.name,
        target_pattern=target_pattern,
        target_anchor=full_root,
        root_tile=repair_root,
        terminus_tile=repair_term,
        missing_center=missing_center,
        missing_radius=missing_radius,
        missing_edge_keys=missing_keys,
        missing_edges=missing_edges,
        snap_radius=trial_def.snap_radius,
        score_threshold=trial_def.score_threshold,
        blur_radius=trial_def.blur_radius,
        required_generators=required_generators,
        param_overrides=overrides,
    )
    if hasattr(game, "_debug"):
        game._debug(
            "[seal_trials] attached "
            f"id={trial_def.id} full_root={full_root} full_term={full_term} "
            f"repair_root={repair_root} repair_term={repair_term} "
            f"edges={len(target_pattern.edges)} missing={len(missing_edges)} "
            f"chain={[s.gen for s in norm_steps]}"
        )


def sync_zone_trial(game: "Game", level: "LevelState", coord: Tuple[int, int, int]) -> None:
    """Apply or revoke temporary trial grants when entering/leaving a trial zone."""
    trial = getattr(level, "seal_trial", None)

    player = game._player() if hasattr(game, "_player") else None
    if player is None:
        return

    tags = getattr(player, "tags", {}) or {}
    active_zone = tags.get("trial_zone_coord")

    # If we are entering a new trial zone, revoke any previous grants first.
    if active_zone is not None and active_zone != list(coord):
        revoke_trial_grants(game, player.id)
        tags = getattr(player, "tags", {}) or {}
        tags.pop("trial_zone_coord", None)
        try:
            player.tags = tags
        except Exception:
            pass

    if trial is None or trial.sealed:
        # No trial here; ensure grants are removed if we were in one.
        if active_zone is not None:
            revoke_trial_grants(game, player.id)
            tags = getattr(player, "tags", {}) or {}
            tags.pop("trial_zone_coord", None)
            try:
                player.tags = tags
            except Exception:
                pass
        return

    # Apply grants for the active trial zone.
    apply_trial_grants(game, player.id, trial)
    tags = getattr(player, "tags", {}) or {}
    tags["trial_zone_coord"] = list(coord)
    try:
        player.tags = tags
    except Exception:
        pass


def apply_trial_grants(game: "Game", actor_id: str, trial: SealTrialState) -> None:
    """Grant generators + Seal Rune action while in a trial zone."""
    if trial.sealed:
        return
    if trial.grants_applied:
        _apply_trial_bar_layout(game, trial)
        _apply_trial_params(game, actor_id, trial)
        _apply_trial_place_range(game, trial)
        return

    level = game._level()
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    tags = getattr(actor, "tags", {}) or {}
    backup = tags.get("trial_actions_backup")
    if not isinstance(backup, list):
        backup = list(tags.get("intrinsic_actions") or list(getattr(actor, "actions", ()) or []))
        tags["trial_actions_backup"] = list(backup)

    grant_actions = _trial_grant_actions(trial)

    intrinsic = list(backup)
    for name in grant_actions:
        if name not in intrinsic:
            intrinsic.append(name)

    tags["intrinsic_actions"] = list(intrinsic)
    tags["trial_granted_actions"] = list(grant_actions)
    try:
        actor.tags = tags
    except Exception:
        pass

    trial.grants_applied = True
    trial.granted_actions = list(grant_actions)
    game.refresh_actor_actions(actor.id)
    _apply_trial_params(game, actor_id, trial)
    _apply_trial_place_range(game, trial)
    _apply_trial_bar_layout(game, trial)
    game.log.add("You feel borrowed knowledge settle into place.")


def revoke_trial_grants(game: "Game", actor_id: str) -> None:
    """Restore intrinsic actions after leaving a trial zone or sealing."""
    level = game._level()
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    tags = getattr(actor, "tags", {}) or {}
    backup = tags.get("trial_actions_backup")
    if not isinstance(backup, list):
        return

    tags["intrinsic_actions"] = list(backup)
    tags.pop("trial_actions_backup", None)
    tags.pop("trial_granted_actions", None)
    try:
        actor.tags = tags
    except Exception:
        pass

    game.refresh_actor_actions(actor.id)

    # Clear flags on the active level's trial if present.
    trial = getattr(level, "seal_trial", None)
    if trial is not None:
        trial.grants_applied = False
        trial.granted_actions = []

    _restore_trial_params(game, actor_id)
    _restore_trial_place_range(game)
    _restore_trial_bar_layout(game)


def update_trial(game: "Game", level: "LevelState") -> None:
    """Tick hook: evaluate the player's pattern against the missing chunk."""
    trial = getattr(level, "seal_trial", None)
    if trial is None or trial.sealed:
        return
    _apply_trial_bar_layout(game, trial)
    _apply_trial_place_range(game, trial)

    # Light throttle to keep the match check cheap.
    if level.current_tick % trial.check_interval != 0:
        return

    log_debug = False
    if hasattr(game, "_debug"):
        if level.current_tick - trial.last_debug_tick >= 50:
            log_debug = True
            trial.last_debug_tick = level.current_tick

    score = compute_match_score(game, level, trial, debug=log_debug)
    trial.last_score = score

    if score < trial.score_threshold:
        if trial.ready_to_seal:
            trial.ready_to_seal = False
            trial.announced_ready = False
            trial.seal_prompted = False
        return

    if not trial.ready_to_seal:
        trial.ready_to_seal = True
        trial.announced_ready = False
        trial.seal_prompted = False

    if not trial.announced_ready:
        trial.announced_ready = True
        game.log.add("The seal aligns. A coherence crystal can bind it.")

    if not trial.seal_prompted and hasattr(game, "set_urgent"):
        trial.seal_prompted = True

        def _on_choice(choice_idx: int, g: "Game") -> None:
            if choice_idx != 0:
                # Allow re-prompt after the player breaks alignment.
                return
            seal_rune(g, g.player_id)
            cur_trial = getattr(g._level(), "seal_trial", None)
            if cur_trial is trial and not trial.sealed:
                # Failed to seal (missing crystal) -> allow another prompt later.
                trial.seal_prompted = False

        game.set_urgent(
            "The seal aligns. Bind it with your Coherence Crystal now?",
            title="Seal the Rune?",
            choices=["Seal it", "Not yet"],
            on_choice_effect=_on_choice,
        )


def compute_match_score(
    game: "Game",
    level: "LevelState",
    trial: SealTrialState,
    debug: bool = False,
) -> float:
    """Score how well the player's pattern overlaps the missing region."""
    if not level.pattern.vertices or level.pattern_anchor is None:
        return 0.0

    if debug and hasattr(game, "_debug"):
        # Helpful for diagnosing misalignment: the trial expects the pattern
        # to be anchored at the *repair* root tile (not necessarily the full rune root).
        anchor = level.pattern_anchor
        if anchor != trial.root_tile:
            dx = anchor[0] - trial.root_tile[0]
            dy = anchor[1] - trial.root_tile[1]
            game._debug(
                "[seal_trials] anchor mismatch "
                f"player_anchor={anchor} expected={trial.root_tile} "
                f"delta=({dx},{dy})"
            )

    mask, total_weight = _build_missing_mask(level, trial)
    mask_score = 0.0
    outside = 0

    if total_weight > 0.0:
        player_tiles = _edge_tiles_for_pattern(level.pattern, level.pattern_anchor, level.world)
        if player_tiles:
            hit = 0.0
            for (tx, ty) in player_tiles:
                w = mask[ty][tx]
                hit += w
                if w < 0.05:
                    outside += 1
            mask_score = hit / total_weight
            penalty = outside / max(1, len(player_tiles))
            mask_score = max(0.0, min(1.0, mask_score - penalty * 0.15))

    # Secondary score: exact edge overlap in world-space (tolerant rounding).
    edge_score = _edge_overlap_score(level, trial)

    score = max(mask_score, edge_score)

    if debug and hasattr(game, "_debug"):
        game._debug(
            "[seal_trials] "
            f"score={score:.3f} (mask={mask_score:.3f} edge={edge_score:.3f}) "
            f"thresh={trial.score_threshold:.3f} "
            f"total={total_weight:.3f} outside={outside} "
            f"missing_edges={len(trial.missing_edges)} "
            f"pattern_edges={len(trial.target_pattern.edges)} "
            f"root={trial.root_tile} term={trial.terminus_tile}"
        )
    return score


def seal_rune(game: "Game", actor_id: str) -> None:
    """Attempt to seal the rune by consuming a coherence crystal."""
    level = game._level()
    trial = getattr(level, "seal_trial", None)
    if trial is None:
        game.log.add("There is no seal to bind here.")
        return
    if trial.sealed:
        game.log.add("This seal is already bound.")
        return
    if not trial.ready_to_seal:
        game.log.add("The seal is still misaligned.")
        if hasattr(game, "_debug"):
            game._debug(
                "[seal_trials] seal_rune denied "
                f"score={trial.last_score:.3f} "
                f"threshold={trial.score_threshold:.3f}"
            )
        return

    if not _consume_coherence_crystal(game, actor_id):
        game.log.add("You need a coherence crystal to bind the seal.")
        return

    trial.sealed = True
    trial.ready_to_seal = True
    revoke_trial_grants(game, actor_id)
    game.log.add("The rune locks into place, and the corruption recedes.")
    try:
        from edgecaster.systems import quests as quest_system

        messages = quest_system.update_quest_progress(
            game,
            "seal_rune",
            trial_id=trial.trial_id,
        )
        for msg in messages:
            game.log.add(msg)
    except Exception:
        pass


def _consume_coherence_crystal(game: "Game", actor_id: str) -> bool:
    """Consume one coherence crystal from an inventory if present."""
    inv = inventory_system.get_inventory(game, actor_id)
    for idx, item in enumerate(list(inv)):
        tags = getattr(item, "tags", {}) or {}
        if tags.get("item_type") != "coherence_crystal":
            continue
        qty = inventory_system.get_quantity(item)
        if qty > 1:
            inventory_system.set_quantity(item, qty - 1)
        else:
            inv.pop(idx)
        game.refresh_actor_actions(actor_id)
        return True
    return False


def _trial_grant_actions(trial: SealTrialState) -> List[str]:
    """Build the ordered list of temporary actions to grant."""
    actions = ["place", "reset"]
    for name in trial.required_generators:
        if name not in actions:
            actions.append(name)
    return actions


def _normalize_trial_chain(
    game: "Game",
    trial_def: SealTrialDef,
) -> Tuple[List[SealPatternStep], Dict[str, Dict[str, object]]]:
    """Snap trial params to values the player can actually select."""
    overrides: Dict[str, Dict[str, object]] = {}
    steps: List[SealPatternStep] = []

    for step in trial_def.pattern_chain:
        if not step.gen:
            continue
        params = dict(step.params or {})
        defs = getattr(game, "param_defs", {}).get(step.gen, {})
        changed = False

        for key, val in list(params.items()):
            spec = defs.get(key)
            if not spec:
                continue
            values = list(spec.get("values", []))
            if not values:
                continue
            snapped = _snap_param_value(values, val)
            if snapped != val:
                changed = True
            params[key] = snapped

        if changed and hasattr(game, "_debug"):
            game._debug(
                f"[seal_trials] snapped params for {step.gen}: {step.params} -> {params}"
            )

        if params:
            cur = overrides.get(step.gen, {})
            cur.update(params)
            overrides[step.gen] = cur

        steps.append(
            SealPatternStep(gen=step.gen, times=step.times, params=params)
        )

    return steps, overrides


def _snap_param_value(values: List[object], val: object) -> object:
    """Pick the closest legal param value from a list."""
    if not values:
        return val
    if all(isinstance(v, bool) for v in values):
        return bool(val)
    try:
        target = float(val)  # type: ignore[arg-type]
    except Exception:
        return values[0]
    return min(values, key=lambda v: abs(float(v) - target))


def _apply_trial_params(game: "Game", actor_id: str, trial: SealTrialState) -> None:
    """Force generator parameters to match the trial's required pattern."""
    overrides = trial.param_overrides
    if not overrides:
        return

    level = game._level()
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    tags = getattr(actor, "tags", {}) or {}
    backup = tags.get("trial_param_backup")
    if not isinstance(backup, list):
        backup_list: List[Tuple[str, str, int]] = []
        for action, params in overrides.items():
            for key in params:
                backup_list.append(
                    (str(action), str(key), int(game.param_state.get((action, key), 0)))
                )
        tags["trial_param_backup"] = list(backup_list)
        try:
            actor.tags = tags
        except Exception:
            pass

    for action, params in overrides.items():
        defs = game.param_defs.get(action, {})
        for key, val in params.items():
            spec = defs.get(key)
            if not spec:
                continue
            values = list(spec.get("values", []))
            if not values:
                continue
            # Pick closest value if exact match isn't present.
            try:
                idx = values.index(val)
            except ValueError:
                idx = min(range(len(values)), key=lambda i: abs(float(values[i]) - float(val)))
            game.param_state[(action, key)] = int(idx)


def _restore_trial_params(game: "Game", actor_id: str) -> None:
    """Restore generator parameters after leaving a trial zone."""
    level = game._level()
    actor = level.actors.get(actor_id)
    if actor is None:
        return
    tags = getattr(actor, "tags", {}) or {}
    backup = tags.get("trial_param_backup")
    if not isinstance(backup, list):
        return
    for entry in backup:
        if not isinstance(entry, (list, tuple)) or len(entry) != 3:
            continue
        action, param, idx = entry
        game.param_state[(str(action), str(param))] = int(idx)
    tags.pop("trial_param_backup", None)
    try:
        actor.tags = tags
    except Exception:
        pass


def _apply_trial_place_range(game: "Game", trial: SealTrialState) -> None:
    """Ensure the player can place a terminus long enough for the trial rune."""
    try:
        backup = getattr(game, "_trial_place_range_backup", None)
    except Exception:
        backup = None
    if backup is None:
        try:
            game._trial_place_range_backup = float(getattr(game, "place_range", 0.0))
        except Exception:
            game._trial_place_range_backup = None

    rx, ry = trial.root_tile
    tx, ty = trial.terminus_tile
    dist = math.hypot(tx - rx, ty - ry)
    # Add a small margin so snapped placement always succeeds.
    needed = dist + 0.5
    try:
        current = float(getattr(game, "place_range", 0.0))
    except Exception:
        current = 0.0
    if needed > current:
        try:
            game.place_range = needed
        except Exception:
            pass


def _restore_trial_place_range(game: "Game") -> None:
    """Restore placement range after leaving a trial."""
    backup = getattr(game, "_trial_place_range_backup", None)
    if backup is None:
        return
    try:
        game.place_range = float(backup)
    except Exception:
        pass
    try:
        game._trial_place_range_backup = None
    except Exception:
        pass


def _resolve_root_tile(level: "LevelState", offset: Tuple[int, int]) -> Tuple[int, int]:
    """Pick a walkable root tile near the level entry plus offset."""
    entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
    rx = entry[0] + int(offset[0])
    ry = entry[1] + int(offset[1])

    rx = max(0, min(level.world.width - 1, rx))
    ry = max(0, min(level.world.height - 1, ry))

    if level.world.is_walkable(rx, ry):
        return (rx, ry)

    # Simple local search for walkable tile.
    for r in range(1, 8):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                tx, ty = rx + dx, ry + dy
                if not level.world.in_bounds(tx, ty):
                    continue
                if level.world.is_walkable(tx, ty):
                    return (tx, ty)
    return (rx, ry)


def _resolve_terminus_tile(
    level: "LevelState",
    root_tile: Tuple[int, int],
    terminus_offset: Tuple[int, int],
) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """Clamp the terminus so both endpoints stay inside the level."""
    w = level.world.width
    h = level.world.height
    rx, ry = root_tile
    dx, dy = int(terminus_offset[0]), int(terminus_offset[1])
    tx = rx + dx
    ty = ry + dy

    # Shift root/terminus together if the terminus falls out of bounds.
    if tx < 0:
        rx += -tx
        tx = 0
    elif tx >= w:
        shift = tx - (w - 1)
        rx -= shift
        tx = w - 1

    if ty < 0:
        ry += -ty
        ty = 0
    elif ty >= h:
        shift = ty - (h - 1)
        ry -= shift
        ty = h - 1

    rx = max(0, min(w - 1, rx))
    ry = max(0, min(h - 1, ry))
    return (rx, ry), (tx, ty)


def _expand_chain_steps(steps: List[SealPatternStep]) -> List[SealPatternStep]:
    """Expand pattern steps so each entry represents a single generator pass."""
    expanded: List[SealPatternStep] = []
    for step in steps:
        count = max(1, int(step.times))
        for _ in range(count):
            expanded.append(SealPatternStep(gen=step.gen, times=1, params=dict(step.params or {})))
    return expanded


def _unique_generators(steps: List[SealPatternStep]) -> List[str]:
    """Return the generators in order without duplicates (preserves chain order)."""
    out: List[str] = []
    for step in steps:
        if step.gen and step.gen not in out:
            out.append(step.gen)
    return out


def _resolve_full_rune_line(
    game: "Game",
    level: "LevelState",
    trial_def: SealTrialDef,
    expanded_steps: List[SealPatternStep],
) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """Compute the large, screen-filling rune baseline (root + terminus).

    The line is centered in the zone (optionally jittered), then rotated
    by full_angle_deg. We clamp the span to the map bounds and optionally
    quantize the length so subdivide segments land on integer tiles.
    """
    w = level.world.width
    h = level.world.height

    margin = max(0, int(trial_def.full_margin_tiles))
    min_x = margin
    min_y = margin
    max_x = max(min_x, w - 1 - margin)
    max_y = max(min_y, h - 1 - margin)

    # Center in the zone with optional jitter so we can vary the rune slightly.
    cx = (w - 1) * 0.5
    cy = (h - 1) * 0.5
    jitter = int(trial_def.center_jitter_tiles)
    if jitter > 0:
        try:
            cx += float(game.rng.randint(-jitter, jitter))
            cy += float(game.rng.randint(-jitter, jitter))
        except Exception:
            pass
    cx = max(min_x, min(max_x, cx))
    cy = max(min_y, min(max_y, cy))

    # Angle (degrees) -> direction unit vector.
    angle_deg = float(trial_def.full_angle_deg)
    angle_jitter = float(trial_def.full_angle_jitter_deg)
    if angle_jitter > 0:
        try:
            angle_deg += float(game.rng.uniform(-angle_jitter, angle_jitter))
        except Exception:
            pass
    angle = math.radians(angle_deg)
    ux = math.cos(angle)
    uy = math.sin(angle)
    if abs(ux) < 1e-6 and abs(uy) < 1e-6:
        ux, uy = 1.0, 0.0

    # Maximum half-span that stays inside the bounds.
    max_half = _max_half_length(cx, cy, ux, uy, min_x, max_x, min_y, max_y)
    if max_half <= 0:
        # Fallback to a short horizontal line if bounds are degenerate.
        root = (int(round(cx)), int(round(cy)))
        term = (min(max_x, root[0] + 1), root[1])
        return root, term

    span_ratio = 0.75 if trial_def.full_span_ratio is None else float(trial_def.full_span_ratio)
    span_ratio = max(0.05, min(1.0, span_ratio))
    half_len = max_half * span_ratio

    # If we are axis-aligned and the chain begins with subdivides, quantize the
    # length so the repair segments land on integer tiles (better snapping).
    axis_aligned = abs(ux) < 1e-6 or abs(uy) < 1e-6
    divisor = 1
    if axis_aligned and trial_def.repair_start_step >= 0:
        divisor = _prefix_subdivide_divisor(expanded_steps, trial_def.repair_start_step)

    if axis_aligned and divisor > 1:
        max_full = int(math.floor((max_half * 2.0) / divisor)) * divisor
        max_full = max(divisor, max_full)
        full_len = int(round(half_len * 2.0))
        full_len = max(divisor, int(round(full_len / divisor)) * divisor)
        full_len = min(full_len, max_full)
        half_len = full_len / 2.0

    rx = cx - ux * half_len
    ry = cy - uy * half_len
    tx = cx + ux * half_len
    ty = cy + uy * half_len

    root = (int(round(rx)), int(round(ry)))
    term = (int(round(tx)), int(round(ty)))

    # Final clamp (rounding can push us one tile outside).
    root = (max(min_x, min(max_x, root[0])), max(min_y, min(max_y, root[1])))
    term = (max(min_x, min(max_x, term[0])), max(min_y, min(max_y, term[1])))
    return root, term


def _max_half_length(
    cx: float,
    cy: float,
    ux: float,
    uy: float,
    min_x: int,
    max_x: int,
    min_y: int,
    max_y: int,
) -> float:
    """Return the maximum distance we can travel from center along +/- direction."""
    return min(
        _max_t_to_bounds(cx, cy, ux, uy, min_x, max_x, min_y, max_y),
        _max_t_to_bounds(cx, cy, -ux, -uy, min_x, max_x, min_y, max_y),
    )


def _max_t_to_bounds(
    cx: float,
    cy: float,
    ux: float,
    uy: float,
    min_x: int,
    max_x: int,
    min_y: int,
    max_y: int,
) -> float:
    """Return max positive t where (cx, cy) + t*(ux, uy) stays inside bounds."""
    t_vals: List[float] = []
    if abs(ux) < 1e-9:
        t_vals.append(float("inf"))
    elif ux > 0:
        t_vals.append((max_x - cx) / ux)
    else:
        t_vals.append((min_x - cx) / ux)

    if abs(uy) < 1e-9:
        t_vals.append(float("inf"))
    elif uy > 0:
        t_vals.append((max_y - cy) / uy)
    else:
        t_vals.append((min_y - cy) / uy)

    return max(0.0, min(t_vals))


def _prefix_subdivide_divisor(steps: List[SealPatternStep], stop_idx: int) -> int:
    """Product of subdivide `parts` in the prefix (used to keep integer endpoints)."""
    divisor = 1
    for step in steps[: max(0, int(stop_idx))]:
        if step.gen != "subdivide":
            continue
        try:
            parts = int(step.params.get("parts", 3))
        except Exception:
            parts = 3
        divisor *= max(2, parts)
    return max(1, divisor)


def _build_target_pattern(
    game: "Game",
    root_tile: Tuple[int, int],
    terminus_tile: Tuple[int, int],
    steps: List[SealPatternStep],
) -> Pattern:
    """Build the target pattern from the normalized generator chain."""
    dx = float(terminus_tile[0] - root_tile[0])
    dy = float(terminus_tile[1] - root_tile[1])

    base = builder.line_pattern((0.0, 0.0), (dx, dy))
    gen_steps = []
    for step in steps:
        gen = _make_generator(step.gen, step.params)
        gen_steps.append((gen, max(1, int(step.times))))

    max_segments = getattr(game.cfg, "max_vertices", 20000)
    return builder.apply_chain(base, gen_steps, max_segments=max_segments, dedup=True)


def _make_generator(name: str, params: dict) -> builder.GeneratorBase:
    """Factory for pattern generators used by seal trials."""
    if name == "subdivide":
        parts = int(params.get("parts", 3))
        return builder.SubdivideGenerator(parts=parts)
    if name == "koch":
        height = float(params.get("height", 0.25))
        flip = bool(params.get("flip", False))
        return builder.KochGenerator(height_factor=height, flip=flip)
    if name == "branch":
        angle = float(params.get("angle", 30.0))
        count = int(params.get("count", 2))
        length = float(params.get("length", 0.6))
        return builder.BranchGenerator(angle_deg=angle, length_factor=length, branch_count=count)
    if name == "zigzag":
        parts = int(params.get("parts", 6))
        amp = float(params.get("amp", params.get("amplitude", 0.2)))
        return builder.ZigzagGenerator(parts=parts, amplitude_factor=amp)
    if name == "extend":
        return builder.ExtendGenerator()
    # Fallback: default to subdivide so we always return something.
    return builder.SubdivideGenerator(parts=3)


def _apply_steps_to_pattern(
    game: "Game",
    base: Pattern,
    steps: List[SealPatternStep],
) -> Pattern:
    """Apply a flat list of steps (each step == one generator pass)."""
    gen_steps: List[Tuple[builder.GeneratorBase, int]] = []
    for step in steps:
        gen_steps.append((_make_generator(step.gen, step.params), 1))
    max_segments = getattr(game.cfg, "max_vertices", 20000)
    return builder.apply_chain(base, gen_steps, max_segments=max_segments, dedup=True)


def _pick_repairable_chunk(
    game: "Game",
    level: "LevelState",
    trial_def: SealTrialDef,
    target_pattern: Pattern,
    full_root: Tuple[int, int],
    full_term: Tuple[int, int],
    expanded_steps: List[SealPatternStep],
    base_place_range: float,
) -> Optional[Dict[str, object]]:
    """Pick a removable chunk that can be rebuilt with the tail of the chain.

    This selects a segment from an intermediate pattern (after repair_start_step),
    then applies the *remaining* steps to that segment to form the missing chunk.
    """
    start_step = int(trial_def.repair_start_step)
    if start_step < 0 or start_step >= len(expanded_steps):
        return None

    # Base line for the full rune (pattern-space coordinates).
    dx = float(full_term[0] - full_root[0])
    dy = float(full_term[1] - full_root[1])
    base_pattern = builder.line_pattern((0.0, 0.0), (dx, dy))

    # Build the intermediate pattern where we will select the repair segment.
    prefix_steps = expanded_steps[:start_step]
    if prefix_steps:
        intermediate = _apply_steps_to_pattern(game, base_pattern, prefix_steps)
    else:
        intermediate = base_pattern

    seg = _select_repair_segment(
        trial_def,
        intermediate,
        base_place_range,
        (dx * 0.5, dy * 0.5),
    )
    if seg is None:
        if hasattr(game, "_debug"):
            game._debug("[seal_trials] no repairable segment found; using fallback chunk.")
        return None

    # Apply the remainder of the chain to the chosen segment.
    suffix_steps = expanded_steps[start_step:]
    missing_pattern = _apply_steps_to_pattern(
        game,
        Pattern.from_segments([seg]),
        suffix_steps,
    )

    missing_edges, missing_keys = _match_missing_edges(target_pattern, missing_pattern)
    if not missing_edges:
        if hasattr(game, "_debug"):
            game._debug("[seal_trials] missing chunk did not map onto target pattern.")
        return None

    # World-space root/terminus for the repair chunk (snaps to the segment endpoints).
    root_tile = (
        int(round(full_root[0] + seg.a[0])),
        int(round(full_root[1] + seg.a[1])),
    )
    term_tile = (
        int(round(full_root[0] + seg.b[0])),
        int(round(full_root[1] + seg.b[1])),
    )

    # Clamp to bounds just in case rounding nudges outside.
    root_tile = (
        max(0, min(level.world.width - 1, root_tile[0])),
        max(0, min(level.world.height - 1, root_tile[1])),
    )
    term_tile = (
        max(0, min(level.world.width - 1, term_tile[0])),
        max(0, min(level.world.height - 1, term_tile[1])),
    )

    seg_len = _segment_length(seg)
    missing_center = ((seg.a[0] + seg.b[0]) * 0.5, (seg.a[1] + seg.b[1]) * 0.5)
    missing_radius = max(1.0, seg_len * 0.6)

    required_generators = _unique_generators(suffix_steps)

    return {
        "root_tile": root_tile,
        "terminus_tile": term_tile,
        "missing_center": missing_center,
        "missing_radius": missing_radius,
        "missing_edges": missing_edges,
        "missing_keys": missing_keys,
        "required_generators": required_generators,
    }


def _select_repair_segment(
    trial_def: SealTrialDef,
    pattern: Pattern,
    base_place_range: float,
    default_hint: Tuple[float, float],
) -> Optional[Segment]:
    """Choose a candidate segment to remove based on length + a hint location."""
    segments = pattern.to_segments()
    if not segments:
        return None

    target_len = max(0.1, float(base_place_range) * float(trial_def.repair_target_scale))
    len_tol = max(0.0, float(trial_def.repair_len_tolerance))

    hint = trial_def.missing_center
    if hint == (0.0, 0.0):
        # If the designer hasn't specified a hint, favor the center of the rune.
        hint = default_hint

    candidates: List[Tuple[float, float, Segment]] = []
    for seg in segments:
        seg_len = _segment_length(seg)
        if seg_len <= 0.0:
            continue
        if len_tol >= 0.0:
            if abs(seg_len - target_len) > target_len * len_tol:
                continue
        # Require near-integer endpoints so snapping doesn't distort the repair.
        if not _segment_endpoints_integer(seg, tol=0.01):
            continue
        mid = ((seg.a[0] + seg.b[0]) * 0.5, (seg.a[1] + seg.b[1]) * 0.5)
        dist2 = (mid[0] - hint[0]) ** 2 + (mid[1] - hint[1]) ** 2
        len_diff = abs(seg_len - target_len)
        candidates.append((dist2, len_diff, seg))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _segment_length(seg: Segment) -> float:
    dx = seg.b[0] - seg.a[0]
    dy = seg.b[1] - seg.a[1]
    return math.hypot(dx, dy)


def _segment_endpoints_integer(seg: Segment, tol: float = 1e-3) -> bool:
    """Return True if endpoints are already near integer tile centers."""
    ax, ay = seg.a
    bx, by = seg.b
    return (
        abs(round(ax) - ax) <= tol
        and abs(round(ay) - ay) <= tol
        and abs(round(bx) - bx) <= tol
        and abs(round(by) - by) <= tol
    )


def _match_missing_edges(
    target_pattern: Pattern,
    missing_pattern: Pattern,
) -> Tuple[List[Edge], Set[Tuple[int, int]]]:
    """Map missing-pattern edges onto the full target pattern's edge indices."""
    lookup = _build_vertex_lookup(target_pattern)
    missing_keys: Set[Tuple[int, int]] = set()

    for edge in missing_pattern.edges:
        try:
            a = missing_pattern.vertices[edge.a].pos
            b = missing_pattern.vertices[edge.b].pos
        except Exception:
            continue
        ia = lookup.get(_edge_pos_key(a))
        ib = lookup.get(_edge_pos_key(b))
        if ia is None or ib is None:
            continue
        missing_keys.add((min(ia, ib), max(ia, ib)))

    missing_edges: List[Edge] = []
    for edge in target_pattern.edges:
        key = (min(edge.a, edge.b), max(edge.a, edge.b))
        if key in missing_keys:
            missing_edges.append(edge)

    return missing_edges, missing_keys


def _build_vertex_lookup(pattern: Pattern, ndigits: int = 6) -> Dict[Tuple[float, float], int]:
    """Map rounded vertex positions to their indices (for edge remapping)."""
    out: Dict[Tuple[float, float], int] = {}
    for idx, vertex in enumerate(pattern.vertices):
        out[_edge_pos_key(vertex.pos, ndigits=ndigits)] = idx
    return out


def _edge_pos_key(pos: Tuple[float, float], ndigits: int = 6) -> Tuple[float, float]:
    """Round a vertex position for stable matching across patterns."""
    return (round(pos[0], ndigits), round(pos[1], ndigits))


def _pick_missing_edges(
    pattern: Pattern,
    missing_center: Tuple[float, float],
    missing_radius: float,
) -> Tuple[List[Edge], Set[Tuple[int, int]]]:
    """Select a chunk of edges to remove based on midpoint distance."""
    cx, cy = missing_center
    r2 = float(missing_radius) * float(missing_radius)
    missing: List[Edge] = []
    missing_keys: Set[Tuple[int, int]] = set()

    for edge in pattern.edges:
        try:
            a = pattern.vertices[edge.a].pos
            b = pattern.vertices[edge.b].pos
        except Exception:
            continue
        mx = (a[0] + b[0]) * 0.5
        my = (a[1] + b[1]) * 0.5
        if (mx - cx) ** 2 + (my - cy) ** 2 <= r2:
            missing.append(edge)
            missing_keys.add((min(edge.a, edge.b), max(edge.a, edge.b)))

    return missing, missing_keys


def _build_missing_mask(
    level: "LevelState",
    trial: SealTrialState,
) -> Tuple[List[List[float]], float]:
    """Return a blurred mask grid for the missing region."""
    w = level.world.width
    h = level.world.height
    mask: List[List[float]] = [[0.0 for _ in range(w)] for _ in range(h)]
    missing_tiles: Set[Tuple[int, int]] = set()

    # Draw missing edges into the mask in world-space (root anchored).
    root_x, root_y = trial.target_anchor
    for edge in trial.missing_edges:
        try:
            a = trial.target_pattern.vertices[edge.a].pos
            b = trial.target_pattern.vertices[edge.b].pos
        except Exception:
            continue
        x0 = int(round(a[0] + root_x))
        y0 = int(round(a[1] + root_y))
        x1 = int(round(b[0] + root_x))
        y1 = int(round(b[1] + root_y))
        for tx, ty in _line_points(x0, y0, x1, y1):
            if level.world.in_bounds(tx, ty):
                mask[ty][tx] = 1.0
                missing_tiles.add((tx, ty))

    blurred = _blur_mask(mask, trial.blur_radius)
    # Normalize against the missing edge footprint so blur doesn't penalize
    # perfect matches by counting the entire halo area.
    total = 0.0
    for (tx, ty) in missing_tiles:
        total += blurred[ty][tx]
    return blurred, total


def _edge_tiles_for_pattern(
    pattern: Pattern,
    anchor: Tuple[int, int],
    world,
) -> Set[Tuple[int, int]]:
    """Collect all tiles touched by a pattern's edges (world-space)."""
    tiles: Set[Tuple[int, int]] = set()
    ax, ay = anchor
    for edge in pattern.edges:
        try:
            a = pattern.vertices[edge.a].pos
            b = pattern.vertices[edge.b].pos
        except Exception:
            continue
        x0 = int(round(a[0] + ax))
        y0 = int(round(a[1] + ay))
        x1 = int(round(b[0] + ax))
        y1 = int(round(b[1] + ay))
        for tx, ty in _line_points(x0, y0, x1, y1):
            if world.in_bounds(tx, ty):
                tiles.add((tx, ty))
    return tiles


def _edge_overlap_score(level: "LevelState", trial: SealTrialState) -> float:
    """Return overlap ratio based on exact edge endpoints (rounded)."""
    if not level.pattern.vertices or level.pattern_anchor is None:
        return 0.0

    missing_keys = _edge_position_keys(
        trial.target_pattern,
        trial.target_anchor,
        edge_filter=trial.missing_edge_keys,
        ndigits=3,
    )
    if not missing_keys:
        return 0.0

    player_keys = _edge_position_keys(
        level.pattern,
        level.pattern_anchor,
        edge_filter=None,
        ndigits=3,
    )
    if not player_keys:
        return 0.0

    overlap = len(player_keys.intersection(missing_keys))
    return overlap / max(1, len(missing_keys))


def _edge_position_keys(
    pattern: Pattern,
    anchor: Tuple[int, int],
    edge_filter: Optional[Set[Tuple[int, int]]],
    ndigits: int = 3,
) -> Set[Tuple[Tuple[float, float], Tuple[float, float]]]:
    """Build a set of rounded edge endpoint pairs in world-space."""
    out: Set[Tuple[Tuple[float, float], Tuple[float, float]]] = set()
    ax, ay = anchor

    for edge in pattern.edges:
        if edge_filter is not None:
            key = (min(edge.a, edge.b), max(edge.a, edge.b))
            if key not in edge_filter:
                continue
        try:
            a = pattern.vertices[edge.a].pos
            b = pattern.vertices[edge.b].pos
        except Exception:
            continue
        a_world = (round(a[0] + ax, ndigits), round(a[1] + ay, ndigits))
        b_world = (round(b[0] + ax, ndigits), round(b[1] + ay, ndigits))
        if a_world <= b_world:
            out.add((a_world, b_world))
        else:
            out.add((b_world, a_world))

    return out


def _blur_mask(mask: List[List[float]], radius: int) -> List[List[float]]:
    """Repeated 3x3 box blur to approximate a Gaussian."""
    if radius <= 0:
        return mask

    src = mask
    h = len(src)
    w = len(src[0]) if h else 0
    for _ in range(radius):
        dst = [[0.0 for _ in range(w)] for _ in range(h)]
        for y in range(h):
            for x in range(w):
                acc = 0.0
                count = 0
                for oy in (-1, 0, 1):
                    ny = y + oy
                    if ny < 0 or ny >= h:
                        continue
                    row = src[ny]
                    for ox in (-1, 0, 1):
                        nx = x + ox
                        if nx < 0 or nx >= w:
                            continue
                        acc += row[nx]
                        count += 1
                dst[y][x] = acc / max(1, count)
        src = dst
    return src


def _line_points(x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
    """Integer Bresenham line points for tile grids."""
    points: List[Tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    x, y = x0, y0
    while True:
        points.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return points


def _apply_trial_bar_layout(game: "Game", trial: SealTrialState) -> None:
    """Inject a temporary 'Trial' group into the ability bar layout."""
    bar_state = getattr(game, "ability_bar_state", None)
    if bar_state is None:
        return
    if getattr(bar_state, "_trial_layout_backup", None) is not None:
        return

    try:
        from edgecaster.ui.ability_bar import AbilityGroup, AbilitySlot
    except Exception:
        return

    import copy

    # Snapshot current layout so we can restore it cleanly.
    bar_state._trial_layout_backup = (
        copy.deepcopy(bar_state.slots),
        copy.deepcopy(bar_state.groups),
        bar_state.active_action,
        bar_state.selected_index,
        bar_state.page,
    )

    trial_actions = [a for a in _trial_grant_actions(trial) if a]
    if not trial_actions:
        return

    generator_actions = [a for a in trial.required_generators if a]
    utility_actions = [a for a in trial_actions if a in {"place", "reset"}]

    # Remove trial actions from existing slots/groups to avoid duplicates.
    new_slots: List[AbilitySlot] = []
    for slot in bar_state.slots:
        if slot.kind == "action" and slot.action in trial_actions:
            continue
        if slot.kind == "group" and slot.group_id:
            grp = bar_state.groups.get(slot.group_id)
            if grp:
                grp.members = [a for a in grp.members if a not in trial_actions]
                if grp.active not in grp.members:
                    grp.active = grp.members[0] if grp.members else None
                if not grp.members:
                    bar_state.groups.pop(slot.group_id, None)
                    continue
        new_slots.append(slot)

    bar_state.slots = new_slots

    insert_slots: List[AbilitySlot] = []
    for action in utility_actions:
        insert_slots.append(AbilitySlot(kind="action", action=action))

    if generator_actions:
        bar_state.groups["trial"] = AbilityGroup(
            id="trial",
            label="Trial",
            members=generator_actions,
            active=generator_actions[0],
        )
        insert_slots.append(AbilitySlot(kind="group", group_id="trial"))
    else:
        bar_state.groups.pop("trial", None)

    if insert_slots:
        bar_state.slots = insert_slots + bar_state.slots

    if generator_actions:
        bar_state.active_action = generator_actions[0]
    elif utility_actions:
        bar_state.active_action = utility_actions[0]
    else:
        bar_state.active_action = None

    bar_state.selected_index = 0
    bar_state.page = 0
    bar_state.expanded_slot_index = None
    bar_state._layout_dirty = False


def _restore_trial_bar_layout(game: "Game") -> None:
    """Restore ability bar layout after a trial ends."""
    bar_state = getattr(game, "ability_bar_state", None)
    if bar_state is None:
        return
    backup = getattr(bar_state, "_trial_layout_backup", None)
    if not backup:
        return

    slots, groups, active_action, selected_index, page = backup
    bar_state.slots = slots
    bar_state.groups = groups
    bar_state.active_action = active_action
    bar_state.selected_index = selected_index
    bar_state.page = page
    bar_state.expanded_slot_index = None
    bar_state._trial_layout_backup = None
    bar_state._layout_dirty = False
