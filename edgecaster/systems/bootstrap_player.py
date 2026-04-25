"""Player bootstrap initialization."""
from typing import TYPE_CHECKING
from edgecaster.enemies import factory as enemy_factory
from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import inventory as inventory_system
from edgecaster.systems import blade_runtime as blade_runtime_system
from edgecaster.systems import chakra_items as chakra_items_system

if TYPE_CHECKING:
    from edgecaster.game import Game

def bootstrap_player(game: "Game") -> None:
    px, py = game._level().world.entry
    player_name = game.character.name or "Edgecaster"
    player_stats = game._build_player_stats()

    player_tmpl_id = getattr(game.character, "template_id", None) or "human_base"
    player = enemy_factory.spawn_enemy(player_tmpl_id, (px, py), game=game)

    player.id = game._new_id()
    player.name = player_name
    player.pos = (px, py)
    player.faction = "player"
    player.stats = player_stats
    player.description = "You attempt to perceive yourself, but can do so only incompletely."
    player.tags["icon_path"] = "assets/icons/bismuth_wizard.png"

    actions = ["move", "wait"]
    player_class = getattr(game.character, "player_class", None) or getattr(game.character, "char_class", None)
    generator_choice = getattr(game.character, "generator", "koch")
    illuminator_choice = getattr(game.character, "illuminator", "radius")

    if player_class == "Kochbender":
        actions += ["place", "polygon", "star", "subdivide", "extend", generator_choice]
        if illuminator_choice == "radius":
            actions.append("activate_all")
        elif illuminator_choice == "neighbors":
            actions.append("activate_seed")
        else:
            actions.append("activate_all")
        actions.extend([
            "reset", "meditate", "rainbow_edges", "verdant_edges",
            "corrosive_melt", "start_fern", "winter_hue", "freeze",
            "ignite", "regrow", "aggressive_vines", "choking_vines",
            "push_pattern", "corruption_cone", "place_rune_anchor", "lightning"
        ])
    elif player_class == "Monk":
        actions += [
            "place", "subdivide", "extend", "activate_seed", "reset",
            "meditate", "push_pattern", "chakra", "wind_rush", "energy_kick",
            "palm_burst", "mirror_strike", "aggressive_vines", "choking_vines",
        ]
    elif player_class == "Gardener":
        actions += [
            "place", "branch", "cultivate", "activate_all", "activate_seed",
            "verdant_edges", "start_fern", "regrow", "choking_vines", "aggressive_vines",
        ]
    elif player_class == "Blade":
        actions += [
            "slash", "thrust", "cleave", "throwing_knife", "mirror_blade",
            "place", "subdivide", "extend", "activate_seed", "reset", "meditate", "push_pattern",
        ]

    player.actions = tuple(actions)
    player.tags.setdefault("is_player", True)
    if player_class:
        player.tags.setdefault("class", player_class)

    chakra_init = getattr(game.character, "chakra_init", None)
    if chakra_init:
        try:
            chakra_items_system.apply_chakra_state_snapshot(player, chakra_init, game=game)
        except Exception:
            pass

    game.player_id = player.id
    lvl = game._level()
    lvl.actors[player.id] = player
    lvl.entities[player.id] = player

    if player_class == "Blade":
        try:
            blade_runtime_system.ensure_actor_blade_state(game, player.id)
        except Exception:
            pass

    player.abs_pos = game.abs_from_zone_local(game.zone_coord, player.pos)

    try:
        from edgecaster.systems import entity_lifecycle as _elic
        from edgecaster.systems import entity_body as _ebod
        _expand_queue = [player.id]
        _expand_seen = set()
        while _expand_queue:
            _eid = _expand_queue.pop(0)
            if _eid in _expand_seen:
                continue
            _expand_seen.add(_eid)
            _ent = _elic.find_runtime_entity(game, _eid) or (player if _eid == player.id else None)
            if _ent is not None and _ebod.can_expand_entity(_ent):
                for _cid in _elic.expand_entity(game, _eid, reason="body_init"):
                    _expand_queue.append(str(_cid))
    except Exception:
        pass

    try:
        entity_graph_ops_system.register_entity(game, player, lod_state="expanded")
    except Exception:
        pass

    try:
        recursive_item = game._spawn_entity_from_template(
            "debug_inventory",
            player.pos,
            overrides={"name": "recursive Inventory", "tags": {"recursive_inventory": True}},
        )
    except Exception:
        recursive_item = None

    if recursive_item is not None:
        inventory_system.add_inventory_item(game, game.player_id, recursive_item)
        inventory_system.add_inventory_item(game, recursive_item.id, recursive_item)
        recursive_item.description = "A Platonic bag that appears to contain, among other things, itself."

    try:
        wand_defs = [
            ("wand_koch", "koch"),
            ("wand_branch", "branch"),
            ("wand_zigzag", "zigzag"),
            ("wand_activate_n", "activate_seed"),
            ("wand_sparkle", "sparkle"),
        ]
        intrinsic_set = {str(x) for x in (getattr(player, "actions", ()) or []) if x}
        candidates = [wid for wid, act in wand_defs if act not in intrinsic_set]
        pool = candidates if len(candidates) >= 2 else [wid for wid, _ in wand_defs]

        first = game.rng.choice(pool)
        pool2 = [x for x in pool if x != first]
        second = game.rng.choice(pool2) if pool2 else first
        for wid in (first, second):
            try:
                wand = game._spawn_entity_from_template(wid, player.pos)
                entity_graph_ops_system.attach_entity_to_parent(game, wand, game.player_id, socket_id="inventory")
            except Exception:
                continue
    except Exception:
        pass