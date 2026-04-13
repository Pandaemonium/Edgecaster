"""
Combat and damage resolution system.

Handles:
- Attack resolution and damage calculation
- Hostility checks (via reputation system)
- Death handling and cleanup
- Currency drops and item drops
- Lifesteal and XP drain mechanics

All functions accept (game, ...) as parameters.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState
    from edgecaster.state.actors import Actor


def _telemetry(game: "Game", event: str, **payload) -> None:
    """Best-effort telemetry bridge (combat should never fail due to logging)."""
    try:
        emit = getattr(game, "_telemetry_emit", None)
        if callable(emit):
            emit(event, **payload)
    except Exception:
        return


def _mark_dead_lineage(game: "Game", actor: "Actor") -> None:
    """Best-effort persistence write for deterministic lineage actors."""
    try:
        mark = getattr(game, "mark_actor_dead", None)
        if callable(mark):
            mark(actor, reason="killed")
    except Exception:
        pass


def is_hostile(game: "Game", attacker: "Actor", target: "Actor") -> bool:
    """
    Reputation-driven hostility check used by movement + AI.
    Delegates to reputation_system.
    """
    from edgecaster.systems import reputation as reputation_system
    return reputation_system.is_hostile(game, attacker, target)


def _has_class_tag(actor: "Actor", key: str) -> bool:
    """Check if actor has a specific class tag in its nested tags structure."""
    try:
        t = getattr(actor, "tags", None) or {}
        inner = t.get("tags")
        if isinstance(inner, dict):
            return bool(inner.get(key))
        if isinstance(inner, (list, tuple, set)):
            return key in inner
    except Exception:
        pass
    return False


def calculate_damage(attacker: "Actor", defender: "Actor") -> int:
    """
    Calculate damage for a basic attack.

    Returns the final damage value (minimum 1).
    """
    try:
        base_attack = int(getattr(attacker, "tags", {}).get("base_attack", 1))
    except Exception:
        base_attack = 1
    try:
        base_defense = int(getattr(defender, "tags", {}).get("base_defense", 0))
    except Exception:
        base_defense = 0
    try:
        bonus = int(getattr(attacker, "tags", {}).get("attack_bonus", 0))
    except Exception:
        bonus = 0

    return max(1, base_attack + bonus - base_defense)


def apply_xp_drain(game: "Game", attacker: "Actor", defender: "Actor") -> bool:
    """
    Handle XP drain attack (e.g., Shadows).

    Returns True if XP drain was applied (and damage should be skipped).
    """
    if defender.id != game.player_id:
        return False

    if not _has_class_tag(attacker, "xp_drain"):
        return False

    try:
        base_attack = int(getattr(attacker, "tags", {}).get("base_attack", 1))
    except Exception:
        base_attack = 1
    try:
        bonus = int(getattr(attacker, "tags", {}).get("attack_bonus", 0))
    except Exception:
        bonus = 0

    xp_dmg = max(1, base_attack + bonus)
    xp_before = int(getattr(defender.stats, "xp", 0))
    try:
        defender.stats.xp = max(0, int(defender.stats.xp) - xp_dmg)
    except Exception:
        pass

    if defender.id == game.player_id:
        game.log.add(f"{attacker.name} drains {xp_dmg} XP from you.")
    else:
        game.log.add(f"{attacker.name} drains {xp_dmg} XP from {defender.name}.")
    _telemetry(
        game,
        "xp_drain_hit",
        attacker_id=str(getattr(attacker, "id", "")),
        defender_id=str(getattr(defender, "id", "")),
        amount=int(xp_dmg),
        xp_before=int(xp_before),
        xp_after=int(getattr(defender.stats, "xp", 0)),
    )

    return True


def apply_damage(game: "Game", attacker: "Actor", defender: "Actor", dmg: int) -> None:
    """Apply damage to defender and log the hit."""
    tags = getattr(defender, "tags", None)
    if isinstance(tags, dict) and tags.get("invulnerable"):
        game.log.add(f"{defender.name} is invulnerable.")
        return
    hp_before = int(getattr(defender.stats, "hp", 0))
    defender.stats.hp -= dmg
    defender.stats.clamp()

    if attacker.id == game.player_id:
        game.log.add(f"You hit {defender.name} for {dmg}.")
    elif defender.id == game.player_id:
        game.log.add(f"{attacker.name} hits you for {dmg}.")
    else:
        game.log.add(f"{attacker.name} hits {defender.name} for {dmg}.")
    _telemetry(
        game,
        "damage_hit",
        attacker_id=str(getattr(attacker, "id", "")),
        defender_id=str(getattr(defender, "id", "")),
        amount=int(dmg),
        hp_before=int(hp_before),
        hp_after=int(getattr(defender.stats, "hp", 0)),
        ability="basic_attack",
    )


def apply_lifesteal(game: "Game", attacker: "Actor", defender: "Actor", dmg: int) -> None:
    """
    Apply lifesteal on hit (e.g., Blood Sipper).
    Kept data-driven via YAML tags.
    """
    try:
        lifesteal_frac = 0.0
        atags = getattr(attacker, "tags", None) or {}
        inner = atags.get("tags")
        if isinstance(inner, dict):
            raw = inner.get("lifesteal_frac", None)
            if raw is not None:
                lifesteal_frac = float(raw)
            elif inner.get("blood_sipper"):
                lifesteal_frac = 0.5
        if lifesteal_frac > 0.0:
            heal = int(math.ceil(float(dmg) * float(lifesteal_frac)))
            if heal > 0:
                hp_before = int(getattr(attacker.stats, "hp", 0))
                attacker.stats.hp = min(attacker.stats.max_hp, attacker.stats.hp + heal)
                attacker.stats.clamp()
                if defender.id == game.player_id:
                    game.log.add(f"{attacker.name} drinks blood and heals {heal}.")
                _telemetry(
                    game,
                    "lifesteal_heal",
                    attacker_id=str(getattr(attacker, "id", "")),
                    defender_id=str(getattr(defender, "id", "")),
                    heal=int(heal),
                    hp_before=int(hp_before),
                    hp_after=int(getattr(attacker.stats, "hp", 0)),
                )
    except Exception:
        pass


def handle_player_death(game: "Game", attacker: "Actor") -> None:
    """Handle player death via urgent popup."""
    cause = attacker.name
    game.set_urgent(
        "This world, now doomed, spirals infinitely toward decay and despair...",
        title=f"You die, by way of {cause}.",
        choices=["Continue..."],
    )


def spawn_currency_drop(
    game: "Game",
    level: "LevelState",
    defender: "Actor",
) -> None:
    """
    Spawn currency drop if the defender carries bismuth loot tags.
    """
    tags = getattr(defender, "tags", {}) or {}
    drop_min = int(tags.get("currency_min", 0))
    drop_max = int(tags.get("currency_max", 0))
    if drop_min <= 0 and drop_max <= 0:
        # Enemy YAML tags are preserved under tags["tags"] (dict or list).
        inner = tags.get("tags")
        if isinstance(inner, dict):
            try:
                drop_min = int(inner.get("currency_min", 0))
            except Exception:
                drop_min = 0
            try:
                drop_max = int(inner.get("currency_max", 0))
            except Exception:
                drop_max = 0

    if drop_max < drop_min:
        drop_max = drop_min

    if drop_max > 0 and level.world.is_walkable(*defender.pos):
        amt = game.rng.randint(drop_min, drop_max) if drop_min < drop_max else drop_min
        if amt > 0:
            try:
                ent = game._spawn_entity_from_template(
                    "bismuth_pile",
                    defender.pos,
                    overrides={"tags": {"amount": amt}},
                )
                level.entities[ent.id] = ent
                _telemetry(
                    game,
                    "currency_drop_spawned",
                    owner_id=str(getattr(defender, "id", "")),
                    entity_id=str(getattr(ent, "id", "")),
                    amount=int(amt),
                    at_pos=tuple(getattr(defender, "pos", (0, 0))),
                )
            except Exception:
                pass


def attack(game: "Game", level: "LevelState", attacker: "Actor", defender: "Actor") -> None:
    """
    Resolve an attack from attacker to defender.

    Handles damage calculation, XP drain, lifesteal, and death.
    """
    # Special case: some attackers drain XP instead of HP (e.g., Shadows).
    if apply_xp_drain(game, attacker, defender):
        return

    # Chakra passive: dodge chance (ankle chakra).
    try:
        dodge = float(game.chakra_effect_value("dodge_chance", actor_id=defender.id))
        if dodge > 0.0 and game.rng.random() < dodge:
            game.log.add(f"You dodge {attacker.name}'s attack!")
            return
    except Exception:
        pass

    # Calculate and apply damage
    dmg = calculate_damage(attacker, defender)

    # Chakra passive: incoming damage reduction (skull chakra).
    try:
        reduction = int(game.chakra_effect_value("incoming_damage_reduction", actor_id=defender.id))
        if reduction > 0:
            dmg = max(1, dmg - reduction)
    except Exception:
        pass

    # Status: iron_skin (halves incoming damage).
    try:
        if game._has_status(defender, "iron_skin"):
            dmg = max(1, dmg // 2)
    except Exception:
        pass

    # God ability: unbreakable (survive lethal with 1 HP).
    try:
        from edgecaster.systems import god_abilities as _god_ab
        dmg = _god_ab.unbreakable_check(game, defender, dmg)
    except Exception:
        pass

    _telemetry(
        game,
        "attack_resolved",
        attacker_id=str(getattr(attacker, "id", "")),
        defender_id=str(getattr(defender, "id", "")),
        damage=int(dmg),
    )
    apply_damage(game, attacker, defender, dmg)

    # God ability: grant favor when player takes damage.
    try:
        from edgecaster.systems import gods as _gods_sys
        if defender.id == game.player_id:
            _gods_sys.on_damage_taken_trigger(game)
    except Exception:
        pass

    # Chakra passive: counter damage (elbow chakra).
    try:
        counter = int(game.chakra_effect_value("counter_damage", actor_id=defender.id))
        if counter > 0 and getattr(attacker, "alive", True):
            attacker.stats.hp -= counter
            if hasattr(attacker.stats, "clamp"):
                attacker.stats.clamp()
            game.log.add(f"Chakra energy reflects {counter} damage back to {attacker.name}!")
            if int(getattr(attacker.stats, "hp", 0)) <= 0:
                game.log.add(f"{attacker.name} dies.")
                try:
                    spawn_currency_drop(game, level, attacker)
                    game._kill_actor(
                        level, attacker,
                        killer_id=defender.id,
                        killer_is_player=(defender.id == game.player_id),
                    )
                except Exception:
                    pass
    except Exception:
        pass

    # Lifesteal on hit
    apply_lifesteal(game, attacker, defender, dmg)

    # Handle death
    if defender.stats.hp <= 0:
        if defender.id == game.player_id:
            handle_player_death(game, attacker)
        else:
            game.log.add(f"{defender.name} dies.")
            spawn_currency_drop(game, level, defender)
            game._kill_actor(
                level,
                defender,
                killer_id=attacker.id,
                killer_is_player=(attacker.id == game.player_id),
            )


def kill_actor(
    game: "Game",
    level: "LevelState",
    actor: "Actor",
    *,
    killer_id: Optional[str] = None,
    killer_is_player: bool = False,
) -> None:
    """
    Handle removing a dead actor from the world and awarding XP.

    This includes:
    - Awarding XP (handles faction check + duplicate protection)
    - Applying reputation changes
    - Removing from actors/entities dicts
    - Freeing chained brutes if slaver dies
    - Spawning prototype-driven item drops
    - Spawning legendary rewards
    """
    # Player death: trigger the death popup and return.  Do not run the normal
    # enemy-kill accounting (XP, drops, etc.) for the player's own death.
    # This path is hit when area-effect or spell damage reduces the player to 0
    # HP and the caller goes through _kill_actor instead of combat.attack.
    if actor.id == game.player_id:
        killer_actor = None
        if killer_id is not None:
            try:
                killer_actor = level.actors.get(killer_id)
                if killer_actor is None:
                    attn = getattr(game, "attn_store", None)
                    if attn is not None:
                        killer_actor = getattr(attn, "entities", {}).get(killer_id)
            except Exception:
                pass
        if killer_actor is None:
            class _UnknownKiller:
                name = "unknown forces"
            killer_actor = _UnknownKiller()
        handle_player_death(game, killer_actor)
        return

    from edgecaster.systems import reputation as reputation_system

    # Award XP (handles faction check + duplicate protection)
    game._on_enemy_killed(actor)

    # Reputation: only the player currently tracks dynamic reputation.
    rep_changes = []
    if killer_is_player or (killer_id == game.player_id):
        try:
            rep_changes = reputation_system.apply_rep_event(game, actor, event="kill")
        except Exception:
            pass

    # Show UrgentMessage for legendary kills with reputation changes
    if rep_changes and reputation_system.is_legendary(actor):
        try:
            _show_legendary_kill_popup(game, actor, rep_changes)
        except Exception:
            pass

    # Capture pre-removal data for reward spawning.
    try:
        actor_pos = tuple(getattr(actor, "pos", (None, None)))
    except Exception:
        actor_pos = (None, None)

    aid = actor.id
    proto_id = getattr(actor, "proto_id", None) or (getattr(actor, "tags", {}) or {}).get("template_id")
    _telemetry(
        game,
        "actor_killed",
        actor_id=str(aid),
        proto_id=str(proto_id or ""),
        killer_id=str(killer_id or ""),
        killer_is_player=bool(killer_is_player),
    )

    # Phase 1 lineage persistence: deterministic actor lineages do not respawn.
    _mark_dead_lineage(game, actor)

    # Yoga aggregate persistence: dead stays dead for attention-refined enemies.
    agg_id = None
    slot = None
    try:
        tags = getattr(actor, "tags", {}) or {}
        agg_id = tags.get("from_aggregate", None)
        slot = tags.get("aggregate_slot", None)
        if agg_id is not None and slot is not None:
            amap = getattr(game, "_attn_agg_id_to_ent", None)
            if isinstance(amap, dict):
                aent = amap.get(str(agg_id))
            else:
                aent = None
            if aent is not None:
                harvested = getattr(aent, "_agg_harvested_slots", None)
                if not isinstance(harvested, set):
                    harvested = set()
                    try:
                        setattr(aent, "_agg_harvested_slots", harvested)
                    except Exception:
                        pass
                try:
                    harvested.add(int(slot))
                except Exception:
                    pass
    except Exception:
        pass

    try:
        agg_children = getattr(game, "_attn_active_agg_children", None)
        if isinstance(agg_children, dict) and agg_id in agg_children:
            slot_map = agg_children.get(agg_id)
            if isinstance(slot_map, dict):
                slot_map.pop(int(slot), None)
    except Exception:
        pass


    # Ensure attention-staged entities vanish immediately on death (otherwise they'd still render from attn_store).
    try:
        attn_store = getattr(game, "attn_store", None)
        if attn_store is not None:
            attn_store.despawn(actor.id)
    except Exception:
        pass


    # Remove from actors dict
    if aid in level.actors:
        del level.actors[aid]

    # Remove from entities dict (so it stops being rendered)
    if aid in level.entities:
        del level.entities[aid]

    # Cancel any pending deferred (telegraphed) actions by this actor.
    try:
        from edgecaster.systems.deferred import cancel_for_actor
        cancel_for_actor(level, aid)
    except Exception:
        pass

    # Slaver death frees chained brutes (they become neutral).
    _free_chained_brutes(game, level, aid, proto_id)

    # Prototype-driven item drops (e.g. whip, sage cap).
    _spawn_item_drops(game, level, actor, proto_id, actor_pos)

    # Guaranteed legendary loot drop (after removal so the tile is unoccupied).
    try:
        if actor_pos and actor_pos[0] is not None and actor_pos[1] is not None:
            game._spawn_legendary_reward(level, (int(actor_pos[0]), int(actor_pos[1])), actor)
    except Exception:
        pass


def _show_legendary_kill_popup(
    game: "Game",
    actor: "Actor",
    rep_changes: list,
) -> None:
    """Show an UrgentMessage popup for a legendary kill with reputation changes."""
    actor_name = getattr(actor, "name", "the legendary")

    # Build message body
    lines = [f"You have slain {actor_name}!", ""]
    lines.append("Reputation changes:")
    for faction_name, delta in rep_changes:
        if delta > 0:
            lines.append(f"  {faction_name}: +{delta}")
        else:
            lines.append(f"  {faction_name}: {delta}")

    message = "\n".join(lines)

    # Queue the urgent message for display
    # The scene manager will pick this up and show UrgentMessageScene
    try:
        game.urgent_message = "Legendary Slain!"
        game.urgent_body = message
        game.urgent_resolved = False
    except Exception:
        pass


def _free_chained_brutes(
    game: "Game",
    level: "LevelState",
    slaver_id: str,
    proto_id: Optional[str],
) -> None:
    """Free brutes chained to a slaver when the slaver dies."""
    if str(proto_id or "") != "slaver":
        return

    freed = 0
    for other in list(level.actors.values()):
        try:
            otags = getattr(other, "tags", None) or {}
        except Exception:
            continue
        if otags.get("slaver_master_id") != slaver_id:
            continue
        try:
            other.faction = "neutral"
        except Exception:
            pass
        try:
            otags.pop("slaver_master_id", None)
            otags["freed_from_slaver"] = True
            other.tags = otags
        except Exception:
            pass
        freed += 1

    if freed > 0:
        game.log.add("The shackled brutes are freed as the slaver falls.")


def _spawn_item_drops(
    game: "Game",
    level: "LevelState",
    actor: "Actor",
    proto_id: Optional[str],
    actor_pos: Tuple,
) -> None:
    """Spawn prototype-driven item drops (e.g. whip, sage cap)."""
    try:
        from edgecaster import prototypes as _prototypes

        spec = _prototypes.resolve_proto(str(proto_id)) if proto_id else {}
        drop_items = spec.get("drop_items") or []
        if isinstance(drop_items, (list, tuple)) and actor_pos and actor_pos[0] is not None and actor_pos[1] is not None:
            drop_pos = (int(actor_pos[0]), int(actor_pos[1]))
            for drop_pid in drop_items:
                try:
                    ent = game._spawn_entity_from_template(str(drop_pid), drop_pos)
                    level.entities[ent.id] = ent
                    game.log.add(f"{actor.name} drops {ent.name}.")
                except Exception:
                    continue
    except Exception:
        pass
