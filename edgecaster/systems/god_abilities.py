"""God ability implementations.

Each ability is a registered action function that checks god favor before
executing. Abilities are dynamically added to the player's action tuple
when the god's chakra pattern is active + favor is sufficient.
"""

from __future__ import annotations

import math
from typing import Any, Optional, Tuple, TYPE_CHECKING

from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import gods as gods_system

if TYPE_CHECKING:
    from edgecaster.game import Game


# ---------------------------------------------------------------------------
# Dark Knife abilities
# ---------------------------------------------------------------------------

_KNIFE_RUNE_CHAKRAS = frozenset({"body", "arm", "arm.hand"})
_KNIFE_RUNE_MAX_RANGE = 5.0
_KNIFE_RUNE_EXECUTE_BASE = 5       # flat HP threshold at 0 favor
_KNIFE_RUNE_EXECUTE_SCALE = 0.35   # additional HP per point of favor (caps ~40 at 100 favor)


def act_knife_rune(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Death Rune: damage enemies near Dark Knife pattern vertices. Execute low-HP targets."""
    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "dark_knife")
    if not state.pattern_active:
        game.log.add("The Dark Knife's symbol is not active.")
        return

    # Get pattern and anchor
    pattern = getattr(game, "pattern", None)
    anchor = getattr(game, "pattern_anchor", None)
    if pattern is None or anchor is None:
        game.log.add("No rune is active.")
        return

    ax, ay = int(anchor[0]), int(anchor[1])
    favor = gods_system.get_favor(game, "dark_knife")
    base_damage = max(2, int(5 + favor * 0.15))

    # Find vertices from Dark Knife's chakra signature
    rune_points: list[tuple[float, float]] = []
    for v in getattr(pattern, "vertices", ()) or ():
        tags = getattr(v, "tags", {}) or {}
        node = str(tags.get("chakra_node", "")).strip()
        if not node:
            continue
        if node in _KNIFE_RUNE_CHAKRAS:
            rune_points.append((float(v.pos[0] + ax), float(v.pos[1] + ay)))

    if not rune_points:
        game.log.add("The Dark Knife's vertices are not present in your rune.")
        return

    # Damage enemies near rune vertices
    total_damage = 0
    kills = 0
    for actor in list(entity_ops_system.iter_actors(level)):
        if not getattr(actor, "alive", False):
            continue
        if getattr(actor, "faction", "") != "hostile":
            continue
        ex, ey = actor.pos

        # Find distance to nearest rune vertex
        min_dist = float("inf")
        for rx, ry in rune_points:
            d = math.sqrt((ex - rx) ** 2 + (ey - ry) ** 2)
            if d < min_dist:
                min_dist = d

        if min_dist > _KNIFE_RUNE_MAX_RANGE:
            continue

        # Closer = more damage (linear falloff)
        dmg = max(1, int(base_damage * (1.0 - min_dist / _KNIFE_RUNE_MAX_RANGE)))
        actor.stats.hp -= dmg
        total_damage += dmg

        # Execute check: if still alive but below flat HP threshold, kill outright
        execute_hp = _KNIFE_RUNE_EXECUTE_BASE + int(favor * _KNIFE_RUNE_EXECUTE_SCALE)
        if actor.stats.hp > 0 and actor.stats.hp < execute_hp:
            actor.stats.hp = 0

        if actor.stats.hp <= 0:
            kills += 1
            try:
                killer_is_player = bool(actor_id == getattr(game, "player_id", None))
                kill_actor = getattr(game, "_kill_actor", None)
                if callable(kill_actor):
                    kill_actor(
                        level,
                        actor,
                        killer_id=actor_id,
                        killer_is_player=killer_is_player,
                    )
            except Exception:
                pass
            # Test harnesses and a few lightweight callers still use bare mocks
            # without a real kill pipeline. Ensure the target does not linger as
            # "alive" once the rune has reduced it to 0 HP.
            try:
                if getattr(actor, "alive", True):
                    actor.alive = False
            except Exception:
                pass
            try:
                game.log.add(f"The Death Rune claims {getattr(actor, 'name', 'an enemy')}.")
            except Exception:
                pass

    if total_damage > 0:
        game.log.add(f"The Dark Knife's rune pulses ({total_damage} damage, {kills} kills).")
    else:
        game.log.add("No enemies in range of the Death Rune.")


def act_reaper_mark(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Mark a visible enemy. If it dies within 10 turns, player heals."""
    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "dark_knife")
    if not state.pattern_active:
        game.log.add("The Dark Knife's symbol is not active.")
        return

    target_pos = kwargs.get("target_pos")
    if target_pos is None:
        game.log.add("No target selected.")
        return

    tx, ty = int(target_pos[0]), int(target_pos[1])

    # Find enemy at target pos
    target = None
    for actor in entity_ops_system.iter_actors(level):
        if not getattr(actor, "alive", False):
            continue
        if actor.pos == (tx, ty) and getattr(actor, "faction", "") == "hostile":
            target = actor
            break

    if target is None:
        game.log.add("No hostile target at that position.")
        return

    # Apply the mark
    target_tags = getattr(target, "tags", {})
    target_tags["reaper_marked"] = True
    target_tags["reaper_mark_tick"] = level.current_tick
    target_tags["reaper_mark_duration"] = 10
    target_tags["reaper_mark_healer"] = actor_id

    favor = gods_system.get_favor(game, "dark_knife")
    target_tags["reaper_mark_heal"] = max(3, int(favor * 0.15))

    game.log.add(f"You mark {getattr(target, 'name', 'the target')} for death.")


def reaper_mark_on_kill(game: Any, killed_actor: Any) -> None:
    """Check if a killed actor was reaper-marked and heal the player."""
    tags = getattr(killed_actor, "tags", {}) or {}
    if not tags.get("reaper_marked"):
        return

    try:
        level = game._level()
        tick = level.current_tick
        mark_tick = int(tags.get("reaper_mark_tick", 0))
        duration = int(tags.get("reaper_mark_duration", 10))

        if tick - mark_tick > duration:
            return  # Mark expired

        healer_id = tags.get("reaper_mark_healer")
        heal_amount = int(tags.get("reaper_mark_heal", 3))

        if healer_id:
            healer = entity_ops_system.get_actor(level, healer_id)
            if getattr(healer, "alive", False):
                old_hp = healer.stats.hp
                healer.stats.hp = min(healer.stats.max_hp, healer.stats.hp + heal_amount)
                actual = healer.stats.hp - old_hp
                if actual > 0:
                    game.log.add(f"Reaper's mark heals you for {actual} HP.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Verdant Mother abilities
# ---------------------------------------------------------------------------

def act_verdant_mend(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Heal 15% max HP, scaling with favor."""
    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "verdant_mother")
    if not state.pattern_active:
        game.log.add("The Verdant Mother's symbol is not active.")
        return

    favor = gods_system.get_favor(game, "verdant_mother")
    max_hp = int(getattr(player.stats, "max_hp", 20))
    heal = max(1, int(max_hp * 0.15 + favor * 0.1))

    old_hp = player.stats.hp
    player.stats.hp = min(max_hp, player.stats.hp + heal)
    actual = player.stats.hp - old_hp

    if actual > 0:
        game.log.add(f"The Verdant Mother mends you for {actual} HP.")
    else:
        game.log.add("You are already at full health.")


def act_root_ward(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Spawn blocking root entities on adjacent tiles for 5 turns."""
    from edgecaster.state.entities import Entity

    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "verdant_mother")
    if not state.pattern_active:
        game.log.add("The Verdant Mother's symbol is not active.")
        return

    px, py = player.pos
    spawned = 0

    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = px + dx, py + dy

            # Check walkable and unoccupied
            try:
                tile = level.world.get_tile(nx, ny)
                if tile is None or not tile.walkable:
                    continue
            except Exception:
                continue

            # Don't block tiles with actors
            if entity_ops_system.actor_at(level, (nx, ny)):
                continue

            root_id = f"root_ward_{game._new_id()}"
            root = Entity(
                id=root_id,
                name="Verdant Root",
                pos=(nx, ny),
                glyph="#",
                color=(40, 160, 40),
                kind="root_ward",
                blocks_movement=True,
                tags={
                    "root_ward": True,
                    "ttl": 5,
                    "spawner_id": actor_id,
                },
            )
            level.entities[root_id] = root
            spawned += 1

    if spawned > 0:
        game.log.add(f"Roots erupt around you, blocking {spawned} tiles.")
        level.need_fov = True
    else:
        game.log.add("No space for roots to grow.")


def tick_root_wards(game: Any, level: Any, dt_ticks: int) -> None:
    """Decay root ward TTLs and remove expired ones."""
    to_remove = []
    for ent in entity_ops_system.iter_entities(level):
        eid = str(getattr(ent, "id", "") or "")
        tags = getattr(ent, "tags", {}) or {}
        if not tags.get("root_ward"):
            continue
        ttl = int(tags.get("ttl", 0)) - dt_ticks
        if ttl <= 0:
            to_remove.append(eid)
        else:
            tags["ttl"] = ttl

    for eid in to_remove:
        entity_ops_system.remove_entity(level, eid)

    if to_remove:
        level.need_fov = True


# ---------------------------------------------------------------------------
# Hollow Eye abilities
# ---------------------------------------------------------------------------

def act_all_seeing(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Extend FOV radius by 3 for 15 turns via status."""
    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "hollow_eye")
    if not state.pattern_active:
        game.log.add("The Hollow Eye's symbol is not active.")
        return

    entity_ops_system.add_status(
        game, player, "all_seeing", 15,
        on_apply="The Hollow Eye opens wide. Your vision expands.",
    )
    level.need_fov = True


def act_piercing_gaze(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Reveal tiles through walls in a line from player to target for 10 turns."""
    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "hollow_eye")
    if not state.pattern_active:
        game.log.add("The Hollow Eye's symbol is not active.")
        return

    target_pos = kwargs.get("target_pos")
    if target_pos is None:
        game.log.add("No target selected.")
        return

    tx, ty = int(target_pos[0]), int(target_pos[1])
    px, py = player.pos

    # Reveal tiles along the line from player to target
    dx = tx - px
    dy = ty - py
    dist = max(1, int(math.sqrt(dx * dx + dy * dy)))
    revealed = 0

    for i in range(dist + 1):
        t = i / max(1, dist)
        rx = int(round(px + dx * t))
        ry = int(round(py + dy * t))
        try:
            tile = level.world.get_tile(rx, ry)
            if tile is not None:
                tile.explored = True
                tile.visible = True
                revealed += 1
        except Exception:
            continue

    entity_ops_system.add_status(
        game, player, "piercing_gaze", 10,
        on_apply=f"Your gaze pierces through {revealed} tiles.",
    )
    level.need_fov = True


# ---------------------------------------------------------------------------
# Iron Spine abilities
# ---------------------------------------------------------------------------

def act_god_iron_skin(game: Any, actor_id: str, **kwargs: Any) -> None:
    """+2 defense for 20 turns, scaling with favor."""
    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "iron_spine")
    if not state.pattern_active:
        game.log.add("The Iron Spine's symbol is not active.")
        return

    favor = gods_system.get_favor(game, "iron_spine")
    bonus = 2 + int(math.floor(favor * 0.02))

    tags = getattr(player, "tags", {})
    existing_def = int(tags.get("base_defense", 0))
    tags["base_defense"] = existing_def + bonus

    entity_ops_system.add_status(
        game, player, "god_iron_skin", 20,
        on_apply=f"Iron Skin: +{bonus} defense for 20 heartbeats.",
    )
    player.tags["_god_iron_skin_bonus"] = bonus


def god_iron_skin_expire(game: Any, actor: Any) -> None:
    """Remove god iron skin defense bonus when status expires."""
    bonus = int(actor.tags.get("_god_iron_skin_bonus", 0))
    if bonus > 0:
        existing = int(actor.tags.get("base_defense", 0))
        actor.tags["base_defense"] = max(0, existing - bonus)
    actor.tags.pop("_god_iron_skin_bonus", None)


def act_unbreakable(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Survive one lethal hit with 1 HP. Costs 30 favor."""
    level = game._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    state = gods_system._ensure_favor(game, "iron_spine")
    if not state.pattern_active:
        game.log.add("The Iron Spine's symbol is not active.")
        return

    favor = gods_system.get_favor(game, "iron_spine")
    if favor < 30:
        game.log.add(f"Not enough favor ({int(favor)}/30).")
        return

    # Deduct favor
    state = gods_system._ensure_favor(game, "iron_spine")
    state.current_favor -= 30

    entity_ops_system.add_status(
        game, player, "unbreakable", 999,
        on_apply="The Iron Spine steels your body. You will not fall.",
    )


def unbreakable_check(game: Any, actor: Any, damage: int) -> int:
    """If actor has unbreakable status and damage would kill, survive with 1 HP."""
    if not entity_ops_system.has_status(actor, "unbreakable"):
        return damage

    hp = int(getattr(actor.stats, "hp", 0))
    if hp - damage <= 0:
        # Survive with 1 HP
        actual_damage = max(0, hp - 1)
        actor.statuses.pop("unbreakable", None)
        try:
            game.log.add("The Iron Spine refuses to let you fall!")
        except Exception:
            pass
        return actual_damage

    return damage


# ---------------------------------------------------------------------------
# Per-tick status cleanup
# ---------------------------------------------------------------------------

def tick_god_statuses(game: Any, level: Any, dt_ticks: int) -> None:
    """Tick down god-granted statuses and clean up expired buffs."""
    # Root ward decay
    tick_root_wards(game, level, dt_ticks)

    # Check player for expired god statuses
    try:
        player = entity_ops_system.get_actor(level, game.player_id)
        if player is None:
            return
    except Exception:
        return

    # God iron skin expiry
    if "god_iron_skin" in player.statuses:
        player.statuses["god_iron_skin"] -= dt_ticks
        if player.statuses["god_iron_skin"] <= 0:
            del player.statuses["god_iron_skin"]
            god_iron_skin_expire(game, player)

    # All-seeing expiry
    if "all_seeing" in player.statuses:
        player.statuses["all_seeing"] -= dt_ticks
        if player.statuses["all_seeing"] <= 0:
            del player.statuses["all_seeing"]
            level.need_fov = True

    # Piercing gaze expiry
    if "piercing_gaze" in player.statuses:
        player.statuses["piercing_gaze"] -= dt_ticks
        if player.statuses["piercing_gaze"] <= 0:
            del player.statuses["piercing_gaze"]
            level.need_fov = True
