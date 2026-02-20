"""God ability implementations.

Each ability is a registered action function that checks god favor before
executing. Abilities are dynamically added to the player's action tuple
when invoked + favor is sufficient.
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

def act_blood_edge(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Buff next melee attack by floor(favor * 0.2) bonus damage."""
    level = game._level()
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "dark_knife":
        game.log.add("The Dark Knife is not invoked.")
        return

    favor = gods_system.get_favor(game, "dark_knife")
    bonus = max(1, int(math.floor(favor * 0.2)))

    # Apply via attack_bonus tag (additive with existing bonus)
    tags = getattr(player, "tags", {})
    existing = int(tags.get("attack_bonus", 0))
    tags["attack_bonus"] = existing + bonus

    # Track so we can remove it after one hit
    entity_ops_system.add_status(
        game, player, "blood_edge", 2,
        on_apply=f"Blood Edge: +{bonus} damage on next attack.",
    )
    # Store the bonus amount for cleanup
    player.tags["_blood_edge_bonus"] = bonus


def blood_edge_consume(game: Any, actor: Any) -> None:
    """Called after a melee hit to consume the blood edge buff."""
    if not entity_ops_system.has_status(actor, "blood_edge"):
        return
    bonus = int(actor.tags.get("_blood_edge_bonus", 0))
    if bonus > 0:
        existing = int(actor.tags.get("attack_bonus", 0))
        actor.tags["attack_bonus"] = max(0, existing - bonus)
    actor.tags.pop("_blood_edge_bonus", None)
    actor.statuses.pop("blood_edge", None)


def act_reaper_mark(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Mark a visible enemy. If it dies within 10 turns, player heals."""
    level = game._level()
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "dark_knife":
        game.log.add("The Dark Knife is not invoked.")
        return

    target_pos = kwargs.get("target_pos")
    if target_pos is None:
        game.log.add("No target selected.")
        return

    tx, ty = int(target_pos[0]), int(target_pos[1])

    # Find enemy at target pos
    target = None
    for actor in level.actors.values():
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

        if healer_id and healer_id in level.actors:
            healer = level.actors[healer_id]
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
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "verdant_mother":
        game.log.add("The Verdant Mother is not invoked.")
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
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "verdant_mother":
        game.log.add("The Verdant Mother is not invoked.")
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
    for eid, ent in level.entities.items():
        tags = getattr(ent, "tags", {}) or {}
        if not tags.get("root_ward"):
            continue
        ttl = int(tags.get("ttl", 0)) - dt_ticks
        if ttl <= 0:
            to_remove.append(eid)
        else:
            tags["ttl"] = ttl

    for eid in to_remove:
        del level.entities[eid]

    if to_remove:
        level.need_fov = True


# ---------------------------------------------------------------------------
# Hollow Eye abilities
# ---------------------------------------------------------------------------

def act_all_seeing(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Extend FOV radius by 3 for 15 turns via status."""
    level = game._level()
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "hollow_eye":
        game.log.add("The Hollow Eye is not invoked.")
        return

    entity_ops_system.add_status(
        game, player, "all_seeing", 15,
        on_apply="The Hollow Eye opens wide. Your vision expands.",
    )
    level.need_fov = True


def act_piercing_gaze(game: Any, actor_id: str, **kwargs: Any) -> None:
    """Reveal tiles through walls in a line from player to target for 10 turns."""
    level = game._level()
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "hollow_eye":
        game.log.add("The Hollow Eye is not invoked.")
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
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "iron_spine":
        game.log.add("The Iron Spine is not invoked.")
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
    player = level.actors.get(actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    invoked = gods_system.get_invoked_god(game)
    if invoked != "iron_spine":
        game.log.add("The Iron Spine is not invoked.")
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
        player = level.actors.get(game.player_id)
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

    # Blood edge auto-expire (should be consumed on hit, but safety net)
    if "blood_edge" in player.statuses:
        player.statuses["blood_edge"] -= dt_ticks
        if player.statuses["blood_edge"] <= 0:
            del player.statuses["blood_edge"]
            blood_edge_consume(game, player)

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
