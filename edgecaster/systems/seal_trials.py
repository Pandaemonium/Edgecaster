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
from edgecaster.patterns.activation import project_vertices
from edgecaster.state.patterns import Edge, Pattern
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

    root_tile = _resolve_root_tile(level, trial_def.root_offset)
    root_tile, terminus_tile = _resolve_terminus_tile(level, root_tile, trial_def.terminus_offset)

    norm_steps, overrides = _normalize_trial_chain(game, trial_def)
    target_pattern = _build_target_pattern(game, root_tile, terminus_tile, norm_steps)
    missing_edges, missing_keys = _pick_missing_edges(
        target_pattern,
        trial_def.missing_center,
        trial_def.missing_radius,
    )

    level.seal_trial = SealTrialState(
        trial_id=trial_def.id,
        name=trial_def.name,
        target_pattern=target_pattern,
        target_anchor=root_tile,
        root_tile=root_tile,
        terminus_tile=terminus_tile,
        missing_center=trial_def.missing_center,
        missing_radius=trial_def.missing_radius,
        missing_edge_keys=missing_keys,
        missing_edges=missing_edges,
        snap_radius=trial_def.snap_radius,
        score_threshold=trial_def.score_threshold,
        blur_radius=trial_def.blur_radius,
        required_generators=list(trial_def.required_generators),
        param_overrides=overrides,
    )
    if hasattr(game, "_debug"):
        game._debug(
            "[seal_trials] attached "
            f"id={trial_def.id} root={root_tile} term={terminus_tile} "
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

    if score >= trial.score_threshold and not trial.ready_to_seal:
        trial.ready_to_seal = True
        if not trial.announced_ready:
            trial.announced_ready = True
            game.log.add("The seal aligns. A coherence crystal can bind it.")


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
        # to be anchored at the canonical root tile.
        anchor = level.pattern_anchor
        if anchor != trial.target_anchor:
            dx = anchor[0] - trial.target_anchor[0]
            dy = anchor[1] - trial.target_anchor[1]
            game._debug(
                "[seal_trials] anchor mismatch "
                f"player_anchor={anchor} expected={trial.target_anchor} "
                f"delta=({dx},{dy})"
            )

    mask, total_weight = _build_missing_mask(level, trial)
    if total_weight <= 0.0:
        if debug and hasattr(game, "_debug"):
            game._debug(
                f"[seal_trials] score=0.0 total_weight=0 "
                f"missing_edges={len(trial.missing_edges)} "
                f"pattern_edges={len(trial.target_pattern.edges)}"
            )
        return 0.0

    player_tiles = _edge_tiles_for_pattern(level.pattern, level.pattern_anchor, level.world)
    if not player_tiles:
        if debug and hasattr(game, "_debug"):
            game._debug(
                "[seal_trials] score=0.0 player_tiles=0 "
                f"total_weight={total_weight:.3f}"
            )
        return 0.0

    hit = 0.0
    outside = 0
    for (tx, ty) in player_tiles:
        w = mask[ty][tx]
        hit += w
        if w < 0.05:
            outside += 1

    score = hit / total_weight
    penalty = outside / max(1, len(player_tiles))
    score = max(0.0, min(1.0, score - penalty * 0.15))
    if debug and hasattr(game, "_debug"):
        game._debug(
            "[seal_trials] "
            f"score={score:.3f} thresh={trial.score_threshold:.3f} "
            f"hit={hit:.3f} total={total_weight:.3f} "
            f"outside={outside}/{len(player_tiles)} "
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
    actions.append("seal_rune")
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

    # Add a dedicated Trial group at the front of the bar.
    bar_state.groups["trial"] = AbilityGroup(
        id="trial",
        label="Trial",
        members=trial_actions,
        active=trial_actions[0],
    )
    bar_state.slots.insert(0, AbilitySlot(kind="group", group_id="trial"))
    bar_state.active_action = trial_actions[0]
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
