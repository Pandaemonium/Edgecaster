"""Rune Anchor Siege runtime.

A siege is a persistent, zone-local encounter where the player repairs a failing
Rune Anchor while enemy pressure escalates over time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING, List, Optional, Tuple

from edgecaster.content.rune_anchor_sieges import RuneAnchorSiegeDef, get_rune_anchor_siege
from edgecaster.systems import inventory as inventory_system
from edgecaster.systems import spawning as spawning_system
from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import footprints as footprints_system

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState


@dataclass
class RuneFractureState:
    idx: int
    pos: Tuple[int, int]
    repaired: bool = False
    progress: int = 0
    required_channels: int = 1
    damage: int = 0


@dataclass
class RuneAnchorSiegeState:
    siege_id: str
    name: str
    anchor_pos: Tuple[int, int]
    fractures: List[RuneFractureState]
    phase: str = "coherence"  # coherence | stabilize | stabilized
    active: bool = False
    intro_announced: bool = False

    stability: float = 72.0
    stability_max: float = 100.0
    stability_decay_per_tick: float = 0.18
    repair_stability_gain: float = 16.0
    stabilize_stability_gain: float = 9.0

    channel_range: int = 1
    coherence_per_channel: int = 1
    stabilize_ticks_total: int = 90
    stabilize_ticks_left: int = 90
    stabilize_action_bonus: int = 4

    enemy_pool: List[str] = field(default_factory=lambda: ["imp"])
    spawn_radius_min: int = 6
    spawn_radius_max: int = 10
    wave_min_interval: int = 8
    wave_max_interval: int = 14
    wave_base_count: int = 2
    wave_pressure_scale: float = 3.0
    sapper_spawn_chance: float = 0.22
    sapper_max_alive: int = 2
    sapper_sabotage_damage: float = 14.0

    wave_counter: int = 0
    total_spawned: int = 0
    backlash_count: int = 0
    last_spawn_tick: int = -9999
    last_tick: int = -1

    pulse_interval_min: int = 18
    pulse_interval_max: int = 30
    pulse_warning_ticks: int = 4
    pulse_damage: int = 5
    pulse_min_tiles: int = 3
    pulse_max_tiles: int = 7
    pulse_count: int = 0
    pulse_warning_left: int = 0
    pulse_tiles: List[Tuple[int, int]] = field(default_factory=list)
    next_pulse_tick: int = -1

    dampening_range_tiles: float = 14.0
    dampening_strength: float = 0.55
    dampening_applied: bool = False
    dampening_poi_id: Optional[str] = None

    reward_bismuth_min: int = 20
    reward_bismuth_max: int = 36
    reward_granted: bool = False

    legacy_trial_id: str = ""
    grants_applied: bool = False
    grants_owner_id: Optional[str] = None
    granted_actions: List[str] = field(default_factory=lambda: ["anchor_channel", "anchor_stabilize", "anchor_purge"])


def _is_tile_open(
    game: "Game",
    level: "LevelState",
    pos: Tuple[int, int],
    *,
    avoid_actors: bool = False,
    avoid_entities: bool = False,
    avoid_blocking: bool = False,
) -> bool:
    x, y = int(pos[0]), int(pos[1])
    rect = footprints_system.tile_rect((x, y))

    try:
        if not footprints_system.world_walkable_for_rect(level.world, rect):
            return False
    except Exception:
        try:
            if not level.world.in_bounds(x, y):
                return False
            if not level.world.is_walkable(x, y):
                return False
        except Exception:
            return False

    if avoid_actors:
        actor = entity_ops_system.actor_at(level, (x, y))
        if actor is not None:
            return False

    if avoid_blocking:
        blocker = entity_ops_system.blocking_entity_at(level, (x, y))
        if blocker is not None:
            return False

    if avoid_entities:
        ent = entity_ops_system.entity_at(level, (x, y))
        if ent is not None:
            return False

    return True


def _target_overlaps_tile_set(target: object, tile_set: set[Tuple[int, int]]) -> bool:
    if not tile_set:
        return False
    try:
        rect = footprints_system.entity_footprint_local(target)
        for tx, ty in footprints_system.iter_tiles_overlapped_by_rect(rect):
            if (int(tx), int(ty)) in tile_set:
                return True
        return False
    except Exception:
        pass
    pos = getattr(target, "pos", None)
    if pos is None:
        return False
    return (int(pos[0]), int(pos[1])) in tile_set


def _distance_sq_point_to_rect(px: float, py: float, rect: Tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = footprints_system.normalize_rect(rect)
    cx = min(max(float(px), x0), x1)
    cy = min(max(float(py), y0), y1)
    dx = float(px) - cx
    dy = float(py) - cy
    return dx * dx + dy * dy


def _target_within_radius_sq(target: object, center: Tuple[int, int], radius_sq: float) -> bool:
    cx = float(int(center[0])) + 0.5
    cy = float(int(center[1])) + 0.5
    try:
        rect = footprints_system.entity_footprint_local(target)
        return _distance_sq_point_to_rect(cx, cy, rect) <= float(radius_sq)
    except Exception:
        pos = getattr(target, "pos", None)
        if pos is None:
            return False
        dx = float(int(pos[0])) + 0.5 - cx
        dy = float(int(pos[1])) + 0.5 - cy
        return (dx * dx + dy * dy) <= float(radius_sq)


def attach_siege_to_level(game: "Game", level: "LevelState", siege_id: str) -> None:
    """Attach a rune-anchor siege to a level."""
    if getattr(level, "rune_anchor_siege", None) is not None:
        return

    siege_def = get_rune_anchor_siege(siege_id)
    if siege_def is None:
        if hasattr(game, "_debug"):
            game._debug(f"[rune_siege] Unknown siege id {siege_id!r}")
        return

    anchor_pos = _resolve_anchor_pos(game, level, siege_def.anchor_offset)
    fractures: List[RuneFractureState] = []
    for idx, off in enumerate(siege_def.fracture_offsets):
        fx = anchor_pos[0] + int(off[0])
        fy = anchor_pos[1] + int(off[1])
        fx = max(0, min(level.world.width - 1, fx))
        fy = max(0, min(level.world.height - 1, fy))
        if not _is_tile_open(game, level, (fx, fy), avoid_actors=False, avoid_entities=False, avoid_blocking=False):
            repl = _nearest_walkable(game, level, (fx, fy), max_radius=6, avoid_actors=False, avoid_entities=False)
            if repl is not None:
                fx, fy = repl
        fractures.append(RuneFractureState(idx=idx, pos=(fx, fy)))

    stability_start = max(1.0, min(siege_def.stability_max, siege_def.stability_start))
    siege = RuneAnchorSiegeState(
        siege_id=siege_def.id,
        name=siege_def.name,
        anchor_pos=anchor_pos,
        fractures=fractures,
        stability=stability_start,
        stability_max=siege_def.stability_max,
        stability_decay_per_tick=siege_def.stability_decay_per_tick,
        repair_stability_gain=siege_def.repair_stability_gain,
        stabilize_stability_gain=siege_def.stabilize_stability_gain,
        channel_range=siege_def.channel_range,
        coherence_per_channel=siege_def.coherence_per_channel,
        stabilize_ticks_total=siege_def.stabilize_ticks,
        stabilize_ticks_left=siege_def.stabilize_ticks,
        stabilize_action_bonus=siege_def.stabilize_action_bonus,
        enemy_pool=list(siege_def.enemy_pool),
        spawn_radius_min=siege_def.spawn_radius_min,
        spawn_radius_max=siege_def.spawn_radius_max,
        wave_min_interval=siege_def.wave_min_interval,
        wave_max_interval=siege_def.wave_max_interval,
        wave_base_count=siege_def.wave_base_count,
        wave_pressure_scale=siege_def.wave_pressure_scale,
        sapper_spawn_chance=siege_def.sapper_spawn_chance,
        sapper_max_alive=siege_def.sapper_max_alive,
        pulse_interval_min=siege_def.pulse_interval_min,
        pulse_interval_max=siege_def.pulse_interval_max,
        pulse_warning_ticks=siege_def.pulse_warning_ticks,
        pulse_damage=siege_def.pulse_damage,
        pulse_min_tiles=siege_def.pulse_min_tiles,
        pulse_max_tiles=siege_def.pulse_max_tiles,
        dampening_range_tiles=siege_def.dampening_range_tiles,
        dampening_strength=siege_def.dampening_strength,
        reward_bismuth_min=siege_def.reward_bismuth_min,
        reward_bismuth_max=siege_def.reward_bismuth_max,
        legacy_trial_id=siege_def.legacy_trial_id,
    )
    level.rune_anchor_siege = siege
    _spawn_anchor_entity(game, level, anchor_pos, siege.siege_id)
    _seed_coherence_crystals(game, level, anchor_pos, count=max(2, len(fractures) - 1))

    if hasattr(game, "_debug"):
        game._debug(
            f"[rune_siege] attached id={siege.siege_id} anchor={anchor_pos} "
            f"fractures={[f.pos for f in fractures]}"
        )


def sync_zone_siege(game: "Game", level: "LevelState", coord: Tuple[int, int, int]) -> None:
    """Auto-start or pause sieges as the player changes zones."""
    siege = getattr(level, "rune_anchor_siege", None)

    player = game._player() if hasattr(game, "_player") else None
    if player is None:
        return

    tags = getattr(player, "tags", {}) or {}
    active_zone_raw = tags.get("rune_siege_zone_coord")
    active_zone: Optional[Tuple[int, int, int]] = None
    if isinstance(active_zone_raw, (list, tuple)) and len(active_zone_raw) == 3:
        try:
            active_zone = (int(active_zone_raw[0]), int(active_zone_raw[1]), int(active_zone_raw[2]))
        except Exception:
            active_zone = None

    if active_zone is not None and active_zone != tuple(coord):
        prev = getattr(game, "levels", {}).get(active_zone)
        if prev is not None:
            prev_siege = getattr(prev, "rune_anchor_siege", None)
            if prev_siege is not None:
                prev_siege.active = False
        revoke_siege_grants(game, player.id)
        tags = getattr(player, "tags", {}) or {}
        tags.pop("rune_siege_zone_coord", None)
        try:
            player.tags = tags
        except Exception:
            pass

    if siege is None or siege.phase == "stabilized":
        if active_zone is not None:
            revoke_siege_grants(game, player.id)
            tags = getattr(player, "tags", {}) or {}
            tags.pop("rune_siege_zone_coord", None)
            try:
                player.tags = tags
            except Exception:
                pass
        return

    siege.active = True
    apply_siege_grants(game, player.id, siege)
    tags = getattr(player, "tags", {}) or {}
    tags["rune_siege_zone_coord"] = list(coord)
    try:
        player.tags = tags
    except Exception:
        pass

    if not siege.intro_announced:
        siege.intro_announced = True
        siege.last_tick = int(getattr(level, "current_tick", 0))
        if siege.next_pulse_tick < 0:
            _schedule_next_pulse(game, level, siege, from_tick=siege.last_tick)
        intro_lines = _intro_lines_for_siege(siege.siege_id)
        if intro_lines:
            for line in intro_lines:
                game.log.add(line)
        else:
            game.log.add("The Rune Anchor shudders. Demons answer the call.")
        game.log.add("Repair the fractures, then hold the anchor stable.")


def apply_siege_grants(game: "Game", actor_id: str, siege: RuneAnchorSiegeState) -> None:
    """Grant encounter actions while the actor is in an active siege zone."""
    if siege.phase == "stabilized":
        return
    if siege.grants_applied and siege.grants_owner_id == actor_id:
        return

    level = game._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return

    tags = getattr(actor, "tags", {}) or {}
    intrinsic = tags.get("intrinsic_actions")
    if not isinstance(intrinsic, list):
        intrinsic = list(getattr(actor, "actions", ()) or [])

    added: List[str] = []
    for action in siege.granted_actions:
        if action not in intrinsic:
            intrinsic.append(action)
            added.append(action)

    tags["intrinsic_actions"] = list(intrinsic)
    tags["rune_siege_added_actions"] = list(added)
    tags["rune_siege_granted_actions"] = list(siege.granted_actions)
    try:
        actor.tags = tags
    except Exception:
        pass

    siege.grants_applied = True
    siege.grants_owner_id = str(actor_id)
    game.refresh_actor_actions(actor.id)


def revoke_siege_grants(game: "Game", actor_id: str) -> None:
    """Remove encounter actions when leaving a siege zone."""
    level = game._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return

    tags = getattr(actor, "tags", {}) or {}
    intrinsic = tags.get("intrinsic_actions")
    if not isinstance(intrinsic, list):
        intrinsic = list(getattr(actor, "actions", ()) or [])

    added = tags.get("rune_siege_added_actions")
    if isinstance(added, list):
        for action in added:
            while action in intrinsic:
                intrinsic.remove(action)

    tags["intrinsic_actions"] = list(intrinsic)
    tags.pop("rune_siege_added_actions", None)
    tags.pop("rune_siege_granted_actions", None)
    try:
        actor.tags = tags
    except Exception:
        pass

    game.refresh_actor_actions(actor.id)

    siege = getattr(level, "rune_anchor_siege", None)
    if siege is not None and str(siege.grants_owner_id or "") == str(actor_id):
        siege.grants_applied = False
        siege.grants_owner_id = None


def channel_fracture(game: "Game", actor_id: str) -> None:
    """Action: spend Coherence Crystal charge to stabilize a nearby fracture."""
    level = game._level()
    siege = getattr(level, "rune_anchor_siege", None)
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    if siege is None or siege.phase == "stabilized":
        game.log.add("No failing anchor answers your touch.")
        return

    fracture = _find_channel_target(actor.pos, siege)
    if fracture is None:
        game.log.add("Stand closer to a fractured node.")
        return

    if not _consume_coherence_crystals(game, actor_id, siege.coherence_per_channel):
        game.log.add("You need Coherence Crystals to stabilize the fracture.")
        return

    fracture.progress += 1
    siege.stability = min(siege.stability_max, siege.stability + siege.repair_stability_gain)

    if fracture.progress >= fracture.required_channels:
        fracture.repaired = True
        game.log.add(
            f"Fracture {fracture.idx + 1} locks into phase ({fracture.progress}/{fracture.required_channels})."
        )
    else:
        game.log.add(
            f"Fracture {fracture.idx + 1} resists ({fracture.progress}/{fracture.required_channels})."
        )

    if _all_fractures_repaired(siege) and siege.phase == "coherence":
        siege.phase = "stabilize"
        siege.stabilize_ticks_left = siege.stabilize_ticks_total
        game.log.add("All fractures sealed. Hold the anchor together!")
        game.log.add("Use Stabilize Anchor at the core while under siege.")

    _spawn_wave(game, level, siege, bonus_count=1, force=True)


def stabilize_anchor(game: "Game", actor_id: str) -> None:
    """Action: reinforce the anchor core during the stabilize phase."""
    level = game._level()
    siege = getattr(level, "rune_anchor_siege", None)
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    if siege is None or siege.phase == "stabilized":
        game.log.add("There is no active anchor to stabilize.")
        return
    if siege.phase != "stabilize":
        game.log.add("Seal all fractures first.")
        return
    if not _all_fractures_repaired(siege):
        game.log.add("A fracture reopened. Seal it before stabilizing.")
        return

    dx = int(actor.pos[0]) - int(siege.anchor_pos[0])
    dy = int(actor.pos[1]) - int(siege.anchor_pos[1])
    if dx * dx + dy * dy > 2 * 2:
        game.log.add("Stand at the anchor core to stabilize it.")
        return

    siege.stability = min(siege.stability_max, siege.stability + siege.stabilize_stability_gain)
    siege.stabilize_ticks_left = max(0, siege.stabilize_ticks_left - siege.stabilize_action_bonus)
    game.log.add("You force the anchor back into harmonic lock.")

    if siege.stabilize_ticks_left <= 0 and _all_fractures_repaired(siege):
        _complete_siege(game, level, siege, actor_id)
        return

    if getattr(game, "rng", None) is not None:
        try:
            if game.rng.random() < 0.35:
                _spawn_wave(game, level, siege, bonus_count=1, force=True)
        except Exception:
            pass


def purge_anchor(game: "Game", actor_id: str) -> None:
    """Action: spend coherence to blast hostiles and steady the anchor."""
    level = game._level()
    siege = getattr(level, "rune_anchor_siege", None)
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    if siege is None or siege.phase == "stabilized":
        game.log.add("There is no active anchor to purge.")
        return

    if not _target_within_radius_sq(actor, siege.anchor_pos, 2.0 * 2.0):
        game.log.add("Stand at the anchor core to fire a purge.")
        return

    crystal_cost = max(2, int(siege.coherence_per_channel) + 1)
    if not _consume_coherence_crystals(game, actor_id, crystal_cost):
        game.log.add(f"Anchor Purge needs {crystal_cost} Coherence Crystals.")
        return

    base_damage = max(3, int(siege.pulse_damage) + 2)
    radius2 = 3 * 3
    hits = 0
    kills = 0
    for target in list(entity_ops_system.iter_actors(level)):
        if target is None or target.id == actor_id:
            continue
        if not getattr(target, "alive", True):
            continue
        if not _is_hostile(actor, target, game):
            continue
        if not _target_within_radius_sq(target, siege.anchor_pos, float(radius2)):
            continue

        damage = base_damage + min(4, int(siege.backlash_count))
        if _apply_actor_damage(game, level, target, damage, source_label="Anchor Purge"):
            hits += 1
            if not getattr(target, "alive", True):
                kills += 1

    siege.stability = min(siege.stability_max, siege.stability + 12.0)
    if siege.phase == "stabilize" and siege.stabilize_ticks_left > 0:
        siege.stabilize_ticks_left = max(0, siege.stabilize_ticks_left - 2)
    # Purge also clears any active catastrophe telegraph once.
    siege.pulse_tiles = []
    siege.pulse_warning_left = 0
    _schedule_next_pulse(game, level, siege)

    if hits > 0:
        game.log.add(f"The anchor detonates in a white harmonic flare ({hits} hit, {kills} down).")
    else:
        game.log.add("The purge wave erupts, but no demon is caught in it.")

    _spawn_wave(game, level, siege, bonus_count=1, force=True)


def update_siege(game: "Game", level: "LevelState") -> None:
    """Tick hook: pressure, decay, backlash, and wave spawning."""
    siege = getattr(level, "rune_anchor_siege", None)
    if siege is None or siege.phase == "stabilized" or not siege.active:
        return

    current_tick = int(getattr(level, "current_tick", 0))
    if siege.last_tick < 0:
        siege.last_tick = current_tick
        return

    elapsed = max(1, current_tick - siege.last_tick)
    start = siege.last_tick + 1
    siege.last_tick = current_tick

    for t in range(start, start + elapsed):
        _tick_once(game, level, siege, t)
        if siege.phase == "stabilized":
            break


def build_siege_status_lines(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState) -> List[str]:
    """Build concise HUD text for the active rune-anchor siege."""
    lines: List[str] = []
    lines.append(f"Rune Anchor Siege: {siege.name}")

    repaired = sum(1 for f in siege.fractures if f.repaired)
    total = max(1, len(siege.fractures))
    stability_pct = int(round((max(0.0, siege.stability) / max(1.0, siege.stability_max)) * 100.0))
    lines.append(f"Stability: {stability_pct}%")
    lines.append(f"Fractures: {repaired}/{total}")
    player_id = str(getattr(game, "player_id", "") or "")
    if player_id:
        lines.append(f"Coherence Crystals: {_count_coherence_crystals(game, player_id)}")

    if siege.phase == "coherence":
        lines.append("Phase: Seal fractures with Coherence Crystals")
    elif siege.phase == "stabilize":
        lines.append(f"Phase: Hold for {max(0, siege.stabilize_ticks_left)} ticks")
    else:
        lines.append("Phase: Stabilized")

    lines.append(f"Waves: {siege.wave_counter}  Spawned: {siege.total_spawned}")
    if siege.pulse_warning_left > 0:
        lines.append(f"Catastrophe Pulse: DETONATES IN {siege.pulse_warning_left}")
    elif siege.next_pulse_tick >= 0:
        now = int(getattr(level, "current_tick", 0))
        lines.append(f"Catastrophe Pulse: {max(0, siege.next_pulse_tick - now)} ticks")
    sappers_alive = _count_alive_sappers(level, siege)
    if sappers_alive > 0:
        lines.append(f"Sappers Active: {sappers_alive}")
    if siege.backlash_count > 0:
        lines.append(f"Backlashes: {siege.backlash_count}")

    return lines


def _tick_once(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState, tick: int) -> None:
    _tick_catastrophe_pulse(game, level, siege, tick)

    progress = _progress_ratio(siege)
    decay_mult = 1.0 + 0.16 * float(siege.backlash_count)
    decay_mult += 0.35 * progress
    if siege.phase == "stabilize":
        decay_mult += 0.25
    decay = siege.stability_decay_per_tick * decay_mult
    siege.stability = max(0.0, siege.stability - decay)

    interval_span = max(0, siege.wave_max_interval - siege.wave_min_interval)
    interval = siege.wave_max_interval - int(round(progress * interval_span))
    interval = max(siege.wave_min_interval, interval - min(3, siege.backlash_count))
    if tick - siege.last_spawn_tick >= interval:
        _spawn_wave(game, level, siege, bonus_count=0, force=True)
        siege.last_spawn_tick = tick

    if siege.stability <= 0.0:
        _trigger_backlash(game, siege)

    _tick_sapper_pressure(game, level, siege, tick)

    if siege.phase == "stabilize":
        if _all_fractures_repaired(siege):
            siege.stabilize_ticks_left = max(0, siege.stabilize_ticks_left - 1)
            if siege.stabilize_ticks_left <= 0:
                _complete_siege(game, level, siege, getattr(game, "player_id", ""))
        else:
            siege.stabilize_ticks_left = min(siege.stabilize_ticks_total, siege.stabilize_ticks_left + 2)


def _trigger_backlash(game: "Game", siege: RuneAnchorSiegeState) -> None:
    siege.backlash_count += 1
    siege.stability = max(12.0, siege.stability_max * 0.35)

    repaired = [f for f in siege.fractures if f.repaired]
    if repaired:
        fracture = _pick_random(game, repaired)
        fracture.repaired = False
        fracture.progress = 0
        fracture.required_channels = min(4, fracture.required_channels + 1)
        fracture.damage += 1
        game.log.add(
            f"Backlash tears open fracture {fracture.idx + 1}! "
            f"(needs {fracture.required_channels} channels)"
        )
    else:
        fracture = _pick_random(game, siege.fractures)
        if fracture is not None:
            fracture.required_channels = min(4, fracture.required_channels + 1)
            fracture.damage += 1
            game.log.add(
                f"The anchor destabilizes. Fracture {fracture.idx + 1} hardens "
                f"(needs {fracture.required_channels} channels)."
            )

    if siege.phase == "stabilize":
        siege.stabilize_ticks_left = min(siege.stabilize_ticks_total, siege.stabilize_ticks_left + 8)


def _spawn_wave(
    game: "Game",
    level: "LevelState",
    siege: RuneAnchorSiegeState,
    *,
    bonus_count: int = 0,
    force: bool = False,
) -> None:
    if not force and (level.current_tick - siege.last_spawn_tick) < siege.wave_min_interval:
        return

    if not siege.enemy_pool:
        return

    progress = _progress_ratio(siege)
    count = siege.wave_base_count + int(round(progress * siege.wave_pressure_scale)) + bonus_count
    if siege.phase == "stabilize":
        count += 1
    count += siege.backlash_count // 3
    count = max(1, min(8, count))

    spawned = 0
    for _ in range(count):
        pos = _find_spawn_tile(game, level, siege)
        if pos is None:
            continue
        enemy_id = _pick_random(game, siege.enemy_pool)
        if not enemy_id:
            continue
        try:
            mob = spawning_system.spawn_enemy_with_pack(game, level, str(enemy_id), pos, schedule_ai=True)
            _maybe_mark_sapper(game, level, siege, mob)
            spawned += 1
        except Exception:
            continue

    if spawned <= 0:
        return

    siege.wave_counter += 1
    siege.total_spawned += spawned
    siege.last_spawn_tick = int(getattr(level, "current_tick", 0))
    if siege.wave_counter % 2 == 1:
        game.log.add(f"Demonic pressure surges (+{spawned}).")


def _complete_siege(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState, actor_id: str) -> None:
    if siege.phase == "stabilized":
        return
    siege.phase = "stabilized"
    siege.active = False
    siege.stabilize_ticks_left = 0
    siege.pulse_warning_left = 0
    siege.pulse_tiles = []

    revoke_siege_grants(game, actor_id)
    _apply_corruption_dampening(game, level, siege)
    _award_completion_loot(game, level, siege)

    game.log.add("The anchor erupts into a harmonic pulse.")
    game.log.add("Nearby corruption recoils from the restored geometry.")

    try:
        from edgecaster.systems import quests as quest_system

        trial_id = siege.legacy_trial_id or siege.siege_id
        messages = quest_system.update_quest_progress(game, "seal_rune", trial_id=trial_id)
        for msg in messages:
            game.log.add(msg)
    except Exception:
        pass


def _apply_corruption_dampening(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState) -> None:
    if siege.dampening_applied:
        return
    if int(level.coord[2]) != 0:
        siege.dampening_applied = True
        return

    try:
        game._ensure_overmap_ready()
    except Exception:
        pass
    if getattr(game, "tile_julia_grid", None) is None:
        try:
            game.build_tile_julia_grid()
        except Exception:
            return
    if getattr(game, "tile_julia_grid", None) is None:
        return

    zx, zy, _depth = level.coord
    wx = int(zx) * int(game.cfg.world_width) + int(siege.anchor_pos[0])
    wy = int(zy) * int(game.cfg.world_height) + int(siege.anchor_pos[1])

    try:
        jx = float(game.tile_julia_grid["x"][wx])  # type: ignore[index]
        jy = float(game.tile_julia_grid["y"][wy])  # type: ignore[index]
    except Exception:
        return

    mean_step = None
    try:
        step_x = float(game.tile_julia_grid.get("step_x", 0.0))  # type: ignore[union-attr]
        step_y = float(game.tile_julia_grid.get("step_y", 0.0))  # type: ignore[union-attr]
        if abs(step_x) > 1e-12 and abs(step_y) > 1e-12:
            mean_step = 0.5 * (abs(step_x) + abs(step_y))
    except Exception:
        mean_step = None

    step = mean_step or 0.01
    sigma = max(0.01, float(siege.dampening_range_tiles) * float(step))
    pid = game.add_corruption_anchor(
        jx,
        jy,
        sigma=sigma,
        strength=float(siege.dampening_strength),
        coord=tuple(level.coord),
        spawn_pos=tuple(siege.anchor_pos),
    )
    siege.dampening_applied = True
    siege.dampening_poi_id = str(pid) if pid else None


def _award_completion_loot(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState) -> None:
    if siege.reward_granted:
        return
    siege.reward_granted = True

    amount = siege.reward_bismuth_min
    if siege.reward_bismuth_max > siege.reward_bismuth_min and getattr(game, "rng", None) is not None:
        try:
            amount = int(game.rng.randint(siege.reward_bismuth_min, siege.reward_bismuth_max))
        except Exception:
            amount = siege.reward_bismuth_min

    drop_pos = _nearest_walkable(
        game,
        level,
        siege.anchor_pos,
        max_radius=4,
        avoid_actors=True,
        avoid_entities=True,
    ) or siege.anchor_pos
    try:
        ent = game._spawn_entity_from_template(
            "bismuth_pile",
            drop_pos,
            overrides={"tags": {"amount": int(max(1, amount))}},
        )
        level.entities[ent.id] = ent
        game.log.add(f"Bismuth crystallizes at the core (+{int(max(1, amount))}).")
    except Exception:
        pass


def _tick_catastrophe_pulse(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState, tick: int) -> None:
    """Manage telegraph + detonation cadence for catastrophe pulses."""
    if siege.phase == "stabilized":
        return
    if siege.next_pulse_tick < 0:
        _schedule_next_pulse(game, level, siege, from_tick=tick)

    if siege.pulse_warning_left > 0:
        siege.pulse_warning_left = max(0, siege.pulse_warning_left - 1)
        if siege.pulse_warning_left <= 0:
            _resolve_catastrophe_pulse(game, level, siege, tick=tick)
        return

    if tick >= siege.next_pulse_tick:
        _begin_catastrophe_telegraph(game, level, siege, tick=tick)


def _schedule_next_pulse(
    game: "Game",
    level: "LevelState",
    siege: RuneAnchorSiegeState,
    *,
    from_tick: Optional[int] = None,
) -> None:
    imin = int(min(siege.pulse_interval_min, siege.pulse_interval_max))
    imax = int(max(siege.pulse_interval_min, siege.pulse_interval_max))
    base = imax
    try:
        base = int(game.rng.randint(imin, imax))
    except Exception:
        base = imax
    pressure_cut = int(round(_progress_ratio(siege) * 4.0)) + min(4, int(siege.backlash_count // 2))
    interval = max(6, base - pressure_cut)
    start_tick = int(from_tick if from_tick is not None else getattr(level, "current_tick", 0))
    siege.next_pulse_tick = start_tick + interval


def _begin_catastrophe_telegraph(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState, *, tick: int) -> None:
    tiles = _build_catastrophe_tiles(game, level, siege)
    if not tiles:
        _schedule_next_pulse(game, level, siege, from_tick=tick)
        return
    siege.pulse_tiles = tiles
    siege.pulse_warning_left = max(1, int(siege.pulse_warning_ticks))
    game.log.add("The anchor howls. Catastrophe pulse incoming.")


def _build_catastrophe_tiles(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState) -> List[Tuple[int, int]]:
    min_tiles = max(1, int(min(siege.pulse_min_tiles, siege.pulse_max_tiles)))
    max_tiles = max(min_tiles, int(max(siege.pulse_min_tiles, siege.pulse_max_tiles)))
    target = max_tiles
    try:
        target = int(game.rng.randint(min_tiles, max_tiles))
    except Exception:
        target = max_tiles
    target += min(2, int(siege.backlash_count // 2))
    target = max(1, min(16, target))

    candidates: List[Tuple[int, int]] = []
    ax, ay = int(siege.anchor_pos[0]), int(siege.anchor_pos[1])
    for r in (1, 2):
        for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r)):
            tx, ty = ax + dx, ay + dy
            if level.world.in_bounds(tx, ty) and level.world.is_walkable(tx, ty):
                candidates.append((tx, ty))

    for fracture in siege.fractures:
        fx, fy = int(fracture.pos[0]), int(fracture.pos[1])
        if level.world.in_bounds(fx, fy) and level.world.is_walkable(fx, fy):
            candidates.append((fx, fy))
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            tx, ty = fx + dx, fy + dy
            if level.world.in_bounds(tx, ty) and level.world.is_walkable(tx, ty):
                candidates.append((tx, ty))

    if siege.phase == "stabilize":
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if abs(dx) + abs(dy) != 2:
                    continue
                tx, ty = ax + dx, ay + dy
                if level.world.in_bounds(tx, ty) and level.world.is_walkable(tx, ty):
                    candidates.append((tx, ty))

    unique: List[Tuple[int, int]] = []
    seen = set()
    for pos in candidates:
        if pos in seen:
            continue
        seen.add(pos)
        unique.append(pos)

    if not unique:
        return []

    out: List[Tuple[int, int]] = []
    pool = list(unique)
    while pool and len(out) < target:
        pick_idx = 0
        try:
            pick_idx = int(game.rng.randint(0, len(pool) - 1))
        except Exception:
            pick_idx = 0
        pick_idx = max(0, min(len(pool) - 1, pick_idx))
        out.append(pool.pop(pick_idx))
    return out


def _resolve_catastrophe_pulse(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState, *, tick: int) -> None:
    tiles = list(siege.pulse_tiles)
    siege.pulse_tiles = []
    siege.pulse_warning_left = 0
    if not tiles:
        _schedule_next_pulse(game, level, siege, from_tick=tick)
        return

    tile_set = set((int(x), int(y)) for x, y in tiles)
    damage = max(1, int(siege.pulse_damage) + min(3, int(siege.backlash_count // 2)))
    hits = 0
    player_hit = False
    for actor in list(entity_ops_system.iter_actors(level)):
        if actor is None or not getattr(actor, "alive", True):
            continue
        if not _target_overlaps_tile_set(actor, tile_set):
            continue
        if _apply_actor_damage(game, level, actor, damage, source_label="Catastrophe Pulse"):
            hits += 1
            if actor.id == getattr(game, "player_id", ""):
                player_hit = True

    siege.pulse_count += 1
    siege.stability = max(0.0, siege.stability - 6.0)
    if hits > 0:
        if player_hit:
            game.log.add("The catastrophe pulse rips through you.")
        else:
            game.log.add(f"Catastrophe pulse detonates ({hits} caught).")
    else:
        game.log.add("Catastrophe pulse detonates into empty ground.")

    _spawn_wave(game, level, siege, bonus_count=1, force=True)
    _schedule_next_pulse(game, level, siege, from_tick=tick)


def _maybe_mark_sapper(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState, mob: object) -> None:
    if siege.sapper_max_alive <= 0:
        return
    if _count_alive_sappers(level, siege) >= int(siege.sapper_max_alive):
        return

    chance = float(siege.sapper_spawn_chance)
    chance += 0.05 * _progress_ratio(siege)
    if siege.phase == "stabilize":
        chance += 0.12
    chance += min(0.2, 0.03 * float(siege.backlash_count))
    chance = max(0.0, min(0.95, chance))

    roll = 1.0
    try:
        roll = float(game.rng.random())
    except Exception:
        roll = 1.0
    if roll > chance:
        return

    tags = getattr(mob, "tags", {}) or {}
    if tags.get("rune_siege_role") == "sapper":
        return

    tags["rune_siege_role"] = "sapper"
    tags["rune_siege_id"] = str(siege.siege_id)
    tags["rune_siege_sabotage_cd"] = 8
    effects = list(tags.get("visual_effects", []) or [])
    if "entropic" not in effects:
        effects.append("entropic")
    tags["visual_effects"] = effects
    try:
        mob.tags = tags
    except Exception:
        pass
    try:
        if not str(getattr(mob, "name", "")).startswith("Sapper "):
            mob.name = f"Sapper {mob.name}"
    except Exception:
        pass
    game.log.add("A demon saboteur breaks toward the fractures.")


def _tick_sapper_pressure(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState, tick: int) -> None:
    if siege.phase == "stabilized":
        return

    for actor in list(entity_ops_system.iter_actors(level)):
        if actor is None or not getattr(actor, "alive", True):
            continue
        tags = getattr(actor, "tags", {}) or {}
        if tags.get("rune_siege_role") != "sapper":
            continue
        if str(tags.get("rune_siege_id", "")) != str(siege.siege_id):
            continue

        cooldown = 0
        try:
            cooldown = int(tags.get("rune_siege_sabotage_cd", 0))
        except Exception:
            cooldown = 0
        if cooldown > 0:
            tags["rune_siege_sabotage_cd"] = cooldown - 1
            try:
                actor.tags = tags
            except Exception:
                pass
            continue

        target: Optional[RuneFractureState] = None
        best_d2: Optional[int] = None
        ax, ay = int(actor.pos[0]), int(actor.pos[1])
        for fracture in siege.fractures:
            if not fracture.repaired:
                continue
            dx = int(fracture.pos[0]) - ax
            dy = int(fracture.pos[1]) - ay
            d2 = dx * dx + dy * dy
            if best_d2 is None or d2 < best_d2:
                best_d2 = d2
                target = fracture

        if target is not None and best_d2 is not None and best_d2 <= 2:
            target.repaired = False
            target.progress = 0
            target.required_channels = min(5, int(target.required_channels) + 1)
            target.damage += 1
            siege.stability = max(0.0, siege.stability - float(siege.sapper_sabotage_damage))
            if siege.phase == "stabilize":
                siege.stabilize_ticks_left = min(siege.stabilize_ticks_total, siege.stabilize_ticks_left + 5)
            tags["rune_siege_sabotage_cd"] = 12
            try:
                actor.tags = tags
            except Exception:
                pass
            game.log.add(
                f"{getattr(actor, 'name', 'Saboteur')} rips open fracture {target.idx + 1} "
                f"(now {target.required_channels} channels)."
            )
            # Risky role: some sappers combust in the anchor's backlash.
            try:
                if float(game.rng.random()) < 0.35:
                    _apply_actor_damage(game, level, actor, 9999, source_label="Anchor Backlash")
            except Exception:
                pass
            continue

        if siege.phase == "stabilize":
            dx = ax - int(siege.anchor_pos[0])
            dy = ay - int(siege.anchor_pos[1])
            if dx * dx + dy * dy <= 2:
                siege.stability = max(0.0, siege.stability - 7.0)
                siege.stabilize_ticks_left = min(siege.stabilize_ticks_total, siege.stabilize_ticks_left + 3)
                tags["rune_siege_sabotage_cd"] = 10
                try:
                    actor.tags = tags
                except Exception:
                    pass
                game.log.add(f"{getattr(actor, 'name', 'Saboteur')} corrupts the core lattice.")


def _count_alive_sappers(level: "LevelState", siege: RuneAnchorSiegeState) -> int:
    alive = 0
    for actor in entity_ops_system.iter_actors(level):
        if actor is None or not getattr(actor, "alive", True):
            continue
        tags = getattr(actor, "tags", {}) or {}
        if tags.get("rune_siege_role") != "sapper":
            continue
        if str(tags.get("rune_siege_id", "")) != str(siege.siege_id):
            continue
        alive += 1
    return alive


def _apply_actor_damage(
    game: "Game",
    level: "LevelState",
    actor: object,
    damage: int,
    *,
    source_label: str,
) -> bool:
    stats = getattr(actor, "stats", None)
    if stats is None:
        return False
    try:
        hp_before = int(getattr(stats, "hp", 0))
    except Exception:
        hp_before = 0
    if hp_before <= 0:
        return False

    dmg = max(1, int(damage))
    try:
        stats.hp = int(getattr(stats, "hp", 0)) - dmg
        if hasattr(stats, "clamp"):
            stats.clamp()
    except Exception:
        return False

    hp_after = int(getattr(stats, "hp", 0))
    if getattr(actor, "id", "") == getattr(game, "player_id", ""):
        game.log.add(f"{source_label} hits you for {dmg}.")
    elif hp_after <= 0:
        game.log.add(f"{source_label} destroys {getattr(actor, 'name', 'an enemy')}.")

    if hp_after > 0:
        return True

    try:
        game._kill_actor(level, actor)
    except Exception:
        try:
            actor.alive = False
        except Exception:
            pass
        try:
            level.actors.pop(getattr(actor, "id", ""), None)
            level.entities.pop(getattr(actor, "id", ""), None)
        except Exception:
            pass
    return True


def _is_hostile(attacker: object, target: object, game: "Game") -> bool:
    try:
        return bool(game.is_hostile(attacker, target))
    except Exception:
        pass
    af = str(getattr(attacker, "faction", ""))
    tf = str(getattr(target, "faction", ""))
    if not af or not tf:
        return False
    if af == tf:
        return False
    if af == "player":
        return tf == "hostile"
    if tf == "player":
        return af == "hostile"
    return af == "hostile" or tf == "hostile"


def _resolve_anchor_pos(game: "Game", level: "LevelState", offset: Tuple[int, int]) -> Tuple[int, int]:
    entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
    x = int(entry[0]) + int(offset[0])
    y = int(entry[1]) + int(offset[1])
    x = max(0, min(level.world.width - 1, x))
    y = max(0, min(level.world.height - 1, y))
    if _is_tile_open(game, level, (x, y), avoid_actors=False, avoid_entities=False, avoid_blocking=False):
        return (x, y)
    return _nearest_walkable(game, level, (x, y), max_radius=8, avoid_actors=False, avoid_entities=False) or (x, y)


def _nearest_walkable(
    game: "Game",
    level: "LevelState",
    origin: Tuple[int, int],
    max_radius: int = 8,
    *,
    avoid_actors: bool = False,
    avoid_entities: bool = False,
) -> Optional[Tuple[int, int]]:
    ox, oy = int(origin[0]), int(origin[1])
    if _is_tile_open(
        game,
        level,
        (ox, oy),
        avoid_actors=avoid_actors,
        avoid_entities=avoid_entities,
        avoid_blocking=False,
    ):
        return (ox, oy)
    for r in range(1, max(1, int(max_radius)) + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                tx, ty = ox + dx, oy + dy
                if not _is_tile_open(
                    game,
                    level,
                    (tx, ty),
                    avoid_actors=avoid_actors,
                    avoid_entities=avoid_entities,
                    avoid_blocking=False,
                ):
                    continue
                return (tx, ty)
    return None


def _find_channel_target(actor_pos: Tuple[int, int], siege: RuneAnchorSiegeState) -> Optional[RuneFractureState]:
    ax, ay = int(actor_pos[0]), int(actor_pos[1])
    rng2 = int(siege.channel_range) * int(siege.channel_range)
    candidates: List[Tuple[int, int, RuneFractureState]] = []
    for fracture in siege.fractures:
        if fracture.repaired:
            continue
        dx = int(fracture.pos[0]) - ax
        dy = int(fracture.pos[1]) - ay
        d2 = dx * dx + dy * dy
        if d2 > rng2:
            continue
        remaining = max(0, fracture.required_channels - fracture.progress)
        candidates.append((d2, remaining, fracture))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2].idx))
    return candidates[0][2]


def _consume_coherence_crystals(game: "Game", actor_id: str, amount: int) -> bool:
    needed = max(1, int(amount))
    inv = inventory_system.get_inventory(game, actor_id)

    slots: List[Tuple[int, object, int]] = []
    total = 0
    for idx, item in enumerate(list(inv)):
        tags = getattr(item, "tags", {}) or {}
        if tags.get("item_type") != "coherence_crystal":
            continue
        qty = inventory_system.get_quantity(item)
        if qty <= 0:
            continue
        slots.append((idx, item, qty))
        total += qty
        if total >= needed:
            break

    if total < needed:
        return False

    left = needed
    for idx, item, qty in reversed(slots):
        if left <= 0:
            break
        take = min(qty, left)
        remain = qty - take
        if remain > 0:
            inventory_system.set_quantity(item, remain)
        else:
            try:
                inventory_system.remove_inventory_item_at(game, actor_id, idx)
            except Exception:
                pass
        left -= take

    game.refresh_actor_actions(actor_id)
    return True


def _count_coherence_crystals(game: "Game", actor_id: str) -> int:
    total = 0
    for item in inventory_system.get_inventory(game, actor_id):
        tags = getattr(item, "tags", {}) or {}
        if tags.get("item_type") != "coherence_crystal":
            continue
        total += max(0, int(inventory_system.get_quantity(item)))
    return max(0, int(total))


def _progress_ratio(siege: RuneAnchorSiegeState) -> float:
    if not siege.fractures:
        return 1.0
    repaired = sum(1 for f in siege.fractures if f.repaired)
    return max(0.0, min(1.0, repaired / float(len(siege.fractures))))


def _all_fractures_repaired(siege: RuneAnchorSiegeState) -> bool:
    if not siege.fractures:
        return True
    return all(f.repaired for f in siege.fractures)


def _find_spawn_tile(game: "Game", level: "LevelState", siege: RuneAnchorSiegeState) -> Optional[Tuple[int, int]]:
    cx, cy = siege.anchor_pos
    rmin = max(1, int(min(siege.spawn_radius_min, siege.spawn_radius_max)))
    rmax = max(rmin, int(max(siege.spawn_radius_min, siege.spawn_radius_max)))

    for _ in range(64):
        try:
            angle = float(game.rng.random()) * math.tau
            radius = int(game.rng.randint(rmin, rmax))
        except Exception:
            angle = 0.0
            radius = rmin

        x = int(round(cx + math.cos(angle) * radius))
        y = int(round(cy + math.sin(angle) * radius))
        if not _is_tile_open(
            game,
            level,
            (x, y),
            avoid_actors=True,
            avoid_entities=True,
            avoid_blocking=True,
        ):
            continue
        return (x, y)

    return spawning_system.find_spawn_position(
        game,
        level,
        near=siege.anchor_pos,
        radius=rmax,
        avoid_entities=True,
        avoid_actors=True,
        max_attempts=80,
    )


def _spawn_anchor_entity(game: "Game", level: "LevelState", pos: Tuple[int, int], siege_id: str) -> None:
    for ent in entity_ops_system.iter_entities(level):
        tags = getattr(ent, "tags", {}) or {}
        if tags.get("rune_anchor_siege_id") == siege_id:
            return

    if entity_ops_system.entity_at(level, pos):
        return
    try:
        ent = game._spawn_entity_from_template(
            "rune_anchor",
            pos,
            overrides={"tags": {"rune_anchor_siege_id": siege_id}},
        )
        level.entities[ent.id] = ent
    except Exception:
        pass


def _seed_coherence_crystals(
    game: "Game",
    level: "LevelState",
    center: Tuple[int, int],
    *,
    count: int,
) -> None:
    placed = 0
    attempts = 0
    while placed < max(0, int(count)) and attempts < 120:
        attempts += 1
        try:
            px = int(center[0]) + int(game.rng.randint(-6, 6))
            py = int(center[1]) + int(game.rng.randint(-6, 6))
        except Exception:
            px, py = center
        if not _is_tile_open(
            game,
            level,
            (px, py),
            avoid_actors=True,
            avoid_entities=True,
            avoid_blocking=False,
        ):
            continue
        try:
            ent = game._spawn_entity_from_template("coherence_crystal", (px, py))
            level.entities[ent.id] = ent
            placed += 1
        except Exception:
            continue


def _intro_lines_for_siege(siege_id: str) -> List[str]:
    siege_def: RuneAnchorSiegeDef | None = get_rune_anchor_siege(siege_id)
    if siege_def is None:
        return []
    return [str(line) for line in siege_def.intro_lines if line]


def _pick_random(game: "Game", items: List):
    if not items:
        return None
    try:
        return items[int(game.rng.randint(0, len(items) - 1))]
    except Exception:
        return items[0]
