"""POI content realization helpers.

This module owns the runtime realization of POI content into a loaded level.
It is invoked by ``Game._spawn_poi_contents`` as a thin delegate so the Game
orchestrator stays focused on coordination instead of spawn details.

[LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
This module overlaps with `systems/poi_worldgen.py` during migration.
End-state is a single resolver/entity-graph realization pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from edgecaster.enemies import factory as enemy_factory
from edgecaster import prototypes
from edgecaster.systems import spawning as spawning_system
from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import footprints as footprints_system
from edgecaster.systems import poi_worldgen as poi_worldgen_system

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState


def spawn_poi_contents(game: "Game", level: "LevelState", coord: Tuple[int, int, int]) -> None:
    """Spawn runtime POI contents for this loaded level."""
    # Unification note: this delegate still exists because POI realization is
    # split between legacy and newer paths. Fold it into the canonical
    # attention/entity-graph resolver as the dual-path migration finishes.
    poi_ids = list(getattr(level.world, "poi_ids", []) or [])
    if bool(getattr(game, "starttsgard_cutover_enabled", False)):
        poi_ids = [pid for pid in poi_ids if pid != "starting_zone"]
    game._debug(f"[_spawn_poi_contents] coord={coord}, poi_ids={poi_ids}")
    if not poi_ids:
        return

    entry = level.world.entry or (level.world.width // 2, level.world.height // 2)
    depot_info = getattr(level.world, "depot_info", None)
    _template_spec_cache: Dict[str, Optional[Dict[str, object]]] = {}

    def _template_spec(template_id: str) -> Optional[Dict[str, object]]:
        key = str(template_id)
        if key in _template_spec_cache:
            return _template_spec_cache[key]
        try:
            spec = prototypes.resolve_proto(key)
        except Exception:
            spec = None
        if isinstance(spec, dict):
            _template_spec_cache[key] = dict(spec)
        else:
            _template_spec_cache[key] = None
        return _template_spec_cache[key]

    def tile_spawnable(
        pos: Tuple[int, int],
        *,
        avoid_actors: bool = True,
        avoid_entities: bool = True,
        avoid_blocking: bool = False,
        spawn_spec: Optional[Dict[str, object]] = None,
        footprint_rect: Optional[Tuple[float, float, float, float]] = None,
    ) -> bool:
        rect = footprint_rect
        if rect is None and spawn_spec is not None:
            rect = footprints_system.footprint_local_rect_from_spec(spawn_spec, (int(pos[0]), int(pos[1])))
        return spawning_system._is_tile_spawnable(
            game,
            level,
            pos,
            avoid_actors=avoid_actors,
            avoid_entities=avoid_entities,
            avoid_blocking=avoid_blocking,
            footprint_rect=rect,
        )

    def nearest_walkable(
        origin: Tuple[int, int],
        max_radius: int = 12,
        *,
        avoid_entities: bool = False,
        spawn_spec: Optional[Dict[str, object]] = None,
    ) -> Optional[Tuple[int, int]]:
        ox, oy = origin
        if tile_spawnable(
            (ox, oy),
            avoid_actors=True,
            avoid_entities=avoid_entities,
            avoid_blocking=False,
            spawn_spec=spawn_spec,
        ):
            return origin
        for r in range(1, max_radius + 1):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    tx, ty = ox + dx, oy + dy
                    if not tile_spawnable(
                        (tx, ty),
                        avoid_actors=True,
                        avoid_entities=avoid_entities,
                        avoid_blocking=False,
                        spawn_spec=spawn_spec,
                    ):
                        continue
                    return (tx, ty)
        return None

    def _spawn_from_template(
        template_id: str,
        pos: Tuple[int, int],
        *,
        overrides: Optional[Dict[str, object]] = None,
        deterministic_recipe: Optional[str] = None,
        deterministic_namespace: str = "poi",
    ) -> object:
        fn = getattr(game, "_spawn_entity_from_template")
        recipe = str(deterministic_recipe or template_id)
        try:
            return fn(
                template_id,
                pos,
                overrides=overrides,
                deterministic=True,
                deterministic_recipe=recipe,
                deterministic_namespace=deterministic_namespace,
                zone_coord=coord,
            )
        except TypeError:
            # Backward compatibility for tests/mocks that still expose the old signature.
            return fn(template_id, pos, overrides=overrides)

    def build_ruin_structure(*, layout: str = "multi_room") -> Optional[Dict[str, object]]:
        world = level.world
        wall_color = (110, 90, 80)
        floor_color = (70, 60, 50)

        for _ in range(80):
            w = int(game.rng.randint(9, 13))
            h = int(game.rng.randint(7, 11))
            x0 = int(game.rng.randint(1, max(1, world.width - w - 1)))
            y0 = int(game.rng.randint(1, max(1, world.height - h - 1)))

            walkable = 0
            total = w * h
            for dy in range(h):
                for dx in range(w):
                    if world.is_walkable(x0 + dx, y0 + dy):
                        walkable += 1
            if walkable < int(total * 0.7):
                continue

            interior: set[Tuple[int, int]] = set()
            for dy in range(h):
                for dx in range(w):
                    x = x0 + dx
                    y = y0 + dy
                    tile = world.get_tile(x, y)
                    if tile is None:
                        continue
                    border = (dx == 0 or dx == w - 1 or dy == 0 or dy == h - 1)
                    if border:
                        tile.walkable = False
                        tile.glyph = "#"
                        tile.color = wall_color
                    else:
                        tile.walkable = True
                        tile.glyph = "."
                        tile.color = floor_color
                        interior.add((x, y))

            door_pos = (x0 + w // 2, y0 + h - 1)

            if layout == "multi_room" and w >= 10 and h >= 8:
                if game.rng.random() < 0.5:
                    wall_x = x0 + w // 2
                    gap_y = y0 + h // 2
                    for yy in range(y0 + 1, y0 + h - 1):
                        tile = world.get_tile(wall_x, yy)
                        if tile is None:
                            continue
                        if yy == gap_y:
                            tile.walkable = True
                            tile.glyph = "."
                            tile.color = floor_color
                            interior.add((wall_x, yy))
                        else:
                            tile.walkable = False
                            tile.glyph = "#"
                            tile.color = wall_color
                            interior.discard((wall_x, yy))
                else:
                    wall_y = y0 + h // 2
                    gap_x = x0 + w // 2
                    for xx in range(x0 + 1, x0 + w - 1):
                        tile = world.get_tile(xx, wall_y)
                        if tile is None:
                            continue
                        if xx == gap_x:
                            tile.walkable = True
                            tile.glyph = "."
                            tile.color = floor_color
                            interior.add((xx, wall_y))
                        else:
                            tile.walkable = False
                            tile.glyph = "#"
                            tile.color = wall_color
                            interior.discard((xx, wall_y))

            for _ in range(int(game.rng.randint(2, 5))):
                if game.rng.random() < 0.5:
                    bx = x0 + game.rng.randint(1, w - 2)
                    by = y0 if game.rng.random() < 0.5 else (y0 + h - 1)
                else:
                    bx = x0 if game.rng.random() < 0.5 else (x0 + w - 1)
                    by = y0 + game.rng.randint(1, h - 2)
                if (bx, by) == door_pos:
                    continue
                tile = world.get_tile(bx, by)
                if tile:
                    tile.walkable = True
                    tile.glyph = "."
                    tile.color = floor_color

            center = (x0 + w // 2, y0 + h // 2)
            return {
                "rect": (x0, y0, w, h),
                "door_pos": door_pos,
                "interior": list(interior),
                "center": center,
            }

        return None

    for pid in poi_ids:
        poi_spec = game.poi_registry.get(pid)
        if not poi_spec:
            continue

        structures_to_process: List[dict] = []
        for ss in poi_spec.structure_specs:
            struct_dict = {"kind": ss.kind}
            struct_dict.update(ss.tags)
            structures_to_process.append(struct_dict)

        for struct in structures_to_process:
            if struct.get("kind") == "item_depot" and depot_info:
                for pos in (depot_info.get("wall_positions") or []):
                    try:
                        ent_here = entity_ops_system.entity_at(level, pos)
                        if not ent_here:
                            ent = _spawn_from_template(
                                "wall",
                                pos,
                                deterministic_recipe=f"poi:{pid}:item_depot_wall:{int(pos[0])},{int(pos[1])}",
                            )
                            level.entities[ent.id] = ent
                        tile = level.world.get_tile(*pos)
                        if tile:
                            tile.walkable = True
                    except Exception:
                        pass

                door_pos = depot_info.get("door")
                if door_pos:
                    try:
                        ent_here = entity_ops_system.entity_at(level, door_pos)
                        if not ent_here:
                            ent = _spawn_from_template(
                                "door",
                                door_pos,
                                deterministic_recipe=f"poi:{pid}:item_depot_door:{int(door_pos[0])},{int(door_pos[1])}",
                            )
                            level.entities[ent.id] = ent
                        tile = level.world.get_tile(*door_pos)
                        if tile:
                            tile.walkable = True
                    except Exception:
                        pass

                sign_pos = depot_info.get("sign")
                if sign_pos:
                    try:
                        ent = _spawn_from_template(
                            "sign",
                            sign_pos,
                            overrides={"tags": {"sign_text": "Item Depot"}, "name": "Item Depot"},
                            deterministic_recipe=f"poi:{pid}:item_depot_sign:{int(sign_pos[0])},{int(sign_pos[1])}",
                        )
                        level.entities[ent.id] = ent
                    except Exception:
                        pass

                interior = depot_info.get("interior") or []
                item_ids = [
                    "blueberry",
                    "raspberry",
                    "strawberry",
                    "healing_kit",
                    "destabilizer",
                    "debug_inventory",
                    "koch_knife",
                    "whip",
                    "energy_flask",
                    "resonant_ring",
                    "coherence_crystal",
                    "sage_cap",
                    "fleet_boots",
                    "vital_belt",
                    "glowing_band",
                    "heel_charm",
                    "rootwrap_boot",
                    "sun_palm_wrap",
                    "mirror_knuckle_wire",
                    "wrist_compass",
                    "ring_of_echoes",
                    "eye_sigil",
                    "seer_pin",
                    "silent_gait_strap",
                    "wand_koch",
                    "wand_branch",
                    "wand_zigzag",
                    "wand_activate_n",
                    "wand_sparkle",
                ]
                shuffled = list(item_ids)
                game.rng.shuffle(shuffled)
                spawn_queue = list(shuffled)
                while len(spawn_queue) < len(interior):
                    extra = list(item_ids)
                    game.rng.shuffle(extra)
                    spawn_queue.extend(extra)
                for i, pos in enumerate(interior):
                    try:
                        template_id = spawn_queue[i]
                        if not tile_spawnable(
                            pos,
                            avoid_actors=True,
                            avoid_entities=True,
                            avoid_blocking=True,
                            spawn_spec=_template_spec(template_id),
                        ):
                            continue
                        ent = _spawn_from_template(
                            template_id,
                            pos,
                            deterministic_recipe=f"poi:{pid}:item_depot_item:{int(pos[0])},{int(pos[1])}:{template_id}",
                        )
                        level.entities[ent.id] = ent
                    except Exception:
                        continue

            elif struct.get("kind") == "rune_anchor":
                center = (level.world.width // 2, level.world.height // 2)
                spot = nearest_walkable(
                    center,
                    avoid_entities=True,
                    spawn_spec=_template_spec("rune_anchor"),
                )
                if spot and not entity_ops_system.entity_at(level, spot):
                    try:
                        ent = _spawn_from_template(
                            "rune_anchor",
                            spot,
                            deterministic_recipe=f"poi:{pid}:rune_anchor:{int(spot[0])},{int(spot[1])}",
                        )
                        level.entities[ent.id] = ent
                    except Exception:
                        pass

            elif struct.get("kind") == "sealing_rune_trial":
                try:
                    from edgecaster.systems import seal_trials

                    trial_id = str(struct.get("trial_id") or "starter_seal")
                    seal_trials.attach_trial_to_level(game, level, trial_id)
                except Exception as e:
                    game._debug(f"[seal_trials] Failed to attach trial: {e!r}")

            elif struct.get("kind") == "rune_anchor_siege":
                try:
                    from edgecaster.systems import rune_anchor_sieges

                    siege_id = str(struct.get("siege_id") or "starter_anchor")
                    rune_anchor_sieges.attach_siege_to_level(game, level, siege_id)
                except Exception as e:
                    game._debug(f"[rune_siege] Failed to attach siege: {e!r}")

            elif struct.get("kind") == "destabilizer_ruin":
                layout = str(struct.get("layout") or "multi_room")
                ruin_info = build_ruin_structure(layout=layout)
                if ruin_info is None:
                    ruin_info = {"center": entry, "interior": []}

                door_pos = ruin_info.get("door_pos")
                if isinstance(door_pos, tuple):
                    try:
                        ent = _spawn_from_template(
                            "door",
                            door_pos,
                            deterministic_recipe=f"poi:{pid}:destabilizer_ruin_door:{int(door_pos[0])},{int(door_pos[1])}",
                        )
                        level.entities[ent.id] = ent
                        tile = level.world.get_tile(*door_pos)
                        if tile:
                            tile.walkable = False
                            tile.glyph = "+"
                    except Exception:
                        pass

                interior_tiles = list(ruin_info.get("interior") or [])
                game.rng.shuffle(interior_tiles)
                dest_pos = None
                for pos in interior_tiles:
                    if not tile_spawnable(
                        pos,
                        avoid_actors=True,
                        avoid_entities=True,
                        avoid_blocking=True,
                        spawn_spec=_template_spec("destabilizer"),
                    ):
                        continue
                    dest_pos = pos
                    break
                if dest_pos is None:
                    dest_pos = nearest_walkable(
                        tuple(ruin_info.get("center", entry)),
                        avoid_entities=True,
                        spawn_spec=_template_spec("destabilizer"),
                    )
                if dest_pos:
                    try:
                        ent = _spawn_from_template(
                            "destabilizer",
                            dest_pos,
                            deterministic_recipe=f"poi:{pid}:destabilizer:{int(dest_pos[0])},{int(dest_pos[1])}",
                        )
                        level.entities[ent.id] = ent
                    except Exception:
                        pass

                enemy_pool = struct.get("enemy_pool") or [
                    "corrupted_thug",
                    "mana_viper",
                    "shadow",
                    "raving_lunatic",
                    "goblin",
                ]
                if not isinstance(enemy_pool, list):
                    enemy_pool = list(enemy_pool)
                try:
                    enemy_count = int(struct.get("enemy_count", 5))
                except Exception:
                    enemy_count = 5
                enemy_count = max(0, enemy_count)
                boss_id = struct.get("boss_id")
                spawn_ids: List[str] = []
                if boss_id:
                    spawn_ids.append(str(boss_id))
                for _ in range(max(0, enemy_count - len(spawn_ids))):
                    try:
                        spawn_ids.append(str(game.rng.choice(enemy_pool)))
                    except Exception:
                        pass

                center = tuple(ruin_info.get("center", entry))
                for enemy_id in spawn_ids:
                    enemy_spec = _template_spec(str(enemy_id))
                    pos = spawning_system.find_spawn_position(
                        game,
                        level,
                        near=center,
                        radius=6,
                        avoid_actors=True,
                        avoid_entities=True,
                        spawn_spec=enemy_spec,
                    )
                    if pos is None:
                        continue
                    try:
                        mob = spawning_system.spawn_enemy_with_pack(
                            game,
                            level,
                            enemy_id,
                            pos,
                            zone_tier=int(getattr(level, "danger_tier", 1) or 1),
                        )
                        mob.tags = getattr(mob, "tags", None) or {}
                        mob.tags["poi_id"] = pid
                    except Exception:
                        continue

            elif struct.get("kind") == "legendary_lair":
                base_proto = str(struct.get("template_id") or struct.get("proto_id") or "imp")
                legend_name = struct.get("name")
                try:
                    hp_mult = float(struct.get("hp_mult", 3.0) or 3.0)
                except Exception:
                    hp_mult = 3.0

                boss_hint = None
                try:
                    li = getattr(level.world, "lair_info", None)
                    if isinstance(li, dict):
                        boss_hint = li.get("boss_pos")
                except Exception:
                    boss_hint = None
                center = boss_hint if boss_hint else (level.world.width // 2, level.world.height // 2)
                spot = nearest_walkable(
                    center,
                    avoid_entities=True,
                    spawn_spec=_template_spec(base_proto),
                )
                if spot:
                    actor = enemy_factory.spawn_enemy(base_proto, spot, abs_pos=game.abs_from_zone_local(coord, spot), game=game)
                    if legend_name:
                        actor.name = str(legend_name)
                    actor.tags = getattr(actor, "tags", {}) or {}
                    actor.tags["legendary"] = True
                    if "legendary_id" in struct:
                        actor.tags["legendary_id"] = struct.get("legendary_id")
                    actor.tags["lair_poi_id"] = pid
                    try:
                        from edgecaster.systems.reputation import generate_procedural_standings

                        actor.tags["faction_standings"] = generate_procedural_standings(
                            game.rng, bias_positive=0.5
                        )
                    except Exception:
                        pass
                    try:
                        base_hp = int(getattr(actor.stats, "max_hp", 1) or 1)
                        boosted = max(base_hp + 1, int(round(base_hp * hp_mult)))
                        actor.stats.max_hp = boosted
                        actor.stats.hp = boosted
                    except Exception:
                        pass

                    spawning_system.register_actor(game, level, actor, schedule_ai=True)

                    try:
                        minions = int(game.rng.randint(3, 5))
                    except Exception:
                        minions = 3
                    spawned = 0
                    attempts = 0
                    while spawned < minions and attempts < 200:
                        attempts += 1
                        dx = int(game.rng.randint(-6, 6))
                        dy = int(game.rng.randint(-4, 4))
                        if dx == 0 and dy == 0:
                            continue
                        tx = int(spot[0] + dx)
                        ty = int(spot[1] + dy)
                        if not tile_spawnable(
                            (tx, ty),
                            avoid_actors=True,
                            avoid_entities=False,
                            avoid_blocking=True,
                            spawn_spec=_template_spec(base_proto),
                        ):
                            continue
                        mob = enemy_factory.spawn_enemy(
                            base_proto, (tx, ty), abs_pos=game.abs_from_zone_local(coord, (tx, ty)),
                            game=game,
                        )
                        spawning_system.register_actor(game, level, mob, schedule_ai=True)
                        spawned += 1

            elif struct.get("kind") == "god_shrine":
                god_id = str(struct.get("god_id", ""))
                if god_id:
                    try:
                        from edgecaster.systems import gods as gods_system
                        center = (level.world.width // 2, level.world.height // 2)
                        spot = nearest_walkable(
                            center,
                            avoid_entities=True,
                        )
                        if spot:
                            actor = gods_system.spawn_god_actor(game, level, god_id, spot)
                            if actor:
                                game._debug(f"[god_shrine] Spawned {god_id} at {spot}")
                    except Exception as e:
                        game._debug(f"[god_shrine] Failed to spawn {god_id}: {e!r}")

            elif struct.get("kind") == "colosseum_arena":
                fp = poi_spec.footprint
                zx, zy, _ = coord
                zone_w = game.cfg.world_width
                zone_h = game.cfg.world_height

                arena_cx = (fp.x0 + fp.x1) / 2.0
                arena_cy = (fp.y0 + fp.y1) / 2.0
                arena_rx = (fp.x1 - fp.x0) / 2.0
                arena_ry = (fp.y1 - fp.y0) / 2.0
                wall_thickness = int(struct.get("wall_thickness", 3))
                floor_color = (180, 160, 120)
                zone_abs_x0 = zx * zone_w
                zone_abs_y0 = zy * zone_h

                world = level.world
                entrance_width = 4
                entrance_positions: set = set()
                entrances = [
                    (int(arena_cx), fp.y0 + wall_thickness // 2),
                    (int(arena_cx), fp.y1 - wall_thickness // 2 - 1),
                    (fp.x0 + wall_thickness // 2, int(arena_cy)),
                    (fp.x1 - wall_thickness // 2 - 1, int(arena_cy)),
                ]
                for ent_abs_x, ent_abs_y in entrances:
                    for offset in range(-entrance_width // 2, entrance_width // 2 + 1):
                        for ex, ey in [(ent_abs_x + offset, ent_abs_y), (ent_abs_x, ent_abs_y + offset)]:
                            entrance_positions.add((ex, ey))

                walls_spawned = 0
                for ty in range(zone_h):
                    for tx in range(zone_w):
                        abs_x = zone_abs_x0 + tx
                        abs_y = zone_abs_y0 + ty
                        dx = abs_x - arena_cx
                        dy = abs_y - arena_cy
                        if arena_rx <= 0 or arena_ry <= 0:
                            continue

                        dist_norm = (dx * dx) / (arena_rx * arena_rx) + (dy * dy) / (arena_ry * arena_ry)
                        inner_rx = arena_rx - wall_thickness
                        inner_ry = arena_ry - wall_thickness
                        if inner_rx > 0 and inner_ry > 0:
                            inner_dist = (dx * dx) / (inner_rx * inner_rx) + (dy * dy) / (inner_ry * inner_ry)
                        else:
                            inner_dist = 0

                        tile = world.get_tile(tx, ty)
                        if tile is None:
                            continue

                        if dist_norm <= 1.0:
                            if inner_dist >= 1.0 and (abs_x, abs_y) not in entrance_positions:
                                pos = (tx, ty)
                                wall_eid = f"colosseum_wall:{abs_x},{abs_y}"
                                if wall_eid not in level.entities and not entity_ops_system.entity_at(level, pos):
                                    try:
                                        ent = poi_worldgen_system.realize_poi_wall_at(
                                            game,
                                            level,
                                            pos=pos,
                                            wall_eid=wall_eid,
                                            glyph="#",
                                            color=(140, 120, 100),
                                            name="Arena Wall",
                                            description="Ancient stone walls of the colosseum.",
                                            floor_color=floor_color,
                                            deterministic_recipe=f"poi:{pid}:colosseum_wall:{abs_x},{abs_y}",
                                        )
                                        if ent is not None:
                                            walls_spawned += 1
                                    except Exception:
                                        pass
                                tile.walkable = True
                                tile.glyph = "."
                                tile.color = floor_color
                            else:
                                tile.walkable = True
                                tile.glyph = "."
                                tile.color = floor_color

                game._debug(f"[colosseum_arena] Zone ({zx}, {zy}): spawned {walls_spawned} wall entities")

        if pid == "starting_zone":
            world = level.world
            dropped = 0
            attempts = 0
            max_attempts = 200
            while dropped < 5 and attempts < max_attempts:
                attempts += 1
                x = game.rng.randint(0, world.width - 1)
                y = game.rng.randint(0, world.height - 1)
                if not tile_spawnable(
                    (x, y),
                    avoid_actors=True,
                    avoid_entities=True,
                    avoid_blocking=False,
                    spawn_spec=_template_spec("bismuth_pile"),
                ):
                    continue
                try:
                    ent = _spawn_from_template(
                        "bismuth_pile",
                        (x, y),
                        deterministic_recipe=f"poi:{pid}:starter_bismuth:{int(x)},{int(y)}",
                    )
                    level.entities[ent.id] = ent
                    dropped += 1
                except Exception:
                    continue

        npc_specs_to_process = poi_spec.npc_specs
        for spec_index, spec in enumerate(npc_specs_to_process):
            spec_key = f"npc:{spec.npc_id}:{spec_index}"
            if game.poi_registry.is_npc_spawned(pid, spec_key):
                continue
            if game.poi_registry.is_npc_dead(pid, spec_key):
                continue

            abs_positions = getattr(spec, "abs_positions", []) or []
            offsets = spec.offsets or [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
            spawn_pos = None
            npc_spawn_spec: Optional[Dict[str, object]] = None
            if spec.npc_id == "caged_demon":
                npc_spawn_spec = _template_spec("caged_demon")
            elif spec.npc_id == "merchant":
                npc_spawn_spec = _template_spec("merchant")

            if abs_positions:
                zx, zy, _ = coord
                zone_w = game.cfg.world_width
                zone_h = game.cfg.world_height
                abs_x, abs_y = abs_positions[0]
                local_x = abs_x - (zx * zone_w)
                local_y = abs_y - (zy * zone_h)
                if 0 <= local_x < zone_w and 0 <= local_y < zone_h:
                    spot = nearest_walkable((local_x, local_y), spawn_spec=npc_spawn_spec)
                    if spot:
                        spawn_pos = spot
                else:
                    continue

            if spawn_pos is None and not abs_positions:
                for dx, dy in offsets:
                    candidate = (entry[0] + dx, entry[1] + dy)
                    spot = nearest_walkable(candidate, spawn_spec=npc_spawn_spec)
                    if spot:
                        spawn_pos = spot
                        break
            if spawn_pos is None and not abs_positions:
                spawn_pos = nearest_walkable(entry, spawn_spec=npc_spawn_spec)
            if spawn_pos is None:
                continue

            poi_worldgen_system.realize_poi_npc_spec(
                game,
                level,
                coord,
                poi_id=pid,
                npc_spec=spec,
                spec_index=spec_index,
                spawn_pos=spawn_pos,
            )
