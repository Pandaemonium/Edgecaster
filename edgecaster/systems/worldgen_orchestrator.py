"""World generation orchestration."""
from typing import Optional, Tuple, TYPE_CHECKING
from edgecaster.state.levels import LevelState
from edgecaster.state.world import World
from edgecaster.patterns import builder
from edgecaster import mapgen
from edgecaster import mapgen_sites
from edgecaster.systems import spatial_index as spatial_index_system
from edgecaster.systems import difficulty as difficulty_system
from edgecaster.systems import poi_worldgen
import random

if TYPE_CHECKING:
    from edgecaster.game import Game

def make_zone(game: "Game", coord: Tuple[int, int, int], up_pos: Optional[Tuple[int, int]]) -> LevelState:
    x, y, depth = coord
    world = World(width=game.cfg.world_width, height=game.cfg.world_height)

    poi_specs = []
    poi_hits: list[str] = []
    try:
        poi_specs = spatial_index_system.query_game_poi_specs_at_zone(
            game,
            x,
            y,
            depth=depth,
            zone_w=int(game.cfg.world_width),
            zone_h=int(game.cfg.world_height),
        )
        poi_hits = [p.id for p in poi_specs]
    except Exception:
        pass

    is_lab_zone = False
    is_lair_zone = False
    lair_layout = "multi_room"
    lair_seed: int | None = None

    for poi_spec in poi_specs:
        for struct_spec in poi_spec.structure_specs:
            if struct_spec.kind == "lab":
                is_lab_zone = True
                break
            if struct_spec.kind == "legendary_lair":
                is_lair_zone = True
                lair_layout = str(struct_spec.tags.get("layout") or lair_layout)
                try:
                    lair_seed = int(struct_spec.tags.get("lair_seed", poi_spec.seed))
                except Exception:
                    pass
        if is_lab_zone or is_lair_zone:
            break

    if depth == 0 and is_lab_zone:
        mapgen.generate_lab(world, game.rng)
    elif depth == 0 and is_lair_zone:
        lair_rng = game.rng
        if lair_seed is not None:
            try:
                lair_rng = random.Random(int(lair_seed) & 0xFFFFFFFF)
            except Exception:
                lair_rng = game.rng
        mapgen_sites.generate_legendary_lair(world, lair_rng, layout=lair_layout)
    elif depth == 0:
        game._ensure_overmap_ready()
        jx_slice = jy_slice = None
        if getattr(game, "tile_julia_grid", None):
            gx0 = x * world.width
            gx1 = gx0 + world.width
            gy0 = y * world.height
            gy1 = gy0 + world.height
            xgrid = game.tile_julia_grid.get("x", [])
            ygrid = game.tile_julia_grid.get("y", [])
            if gx0 < 0 or gy0 < 0 or gx1 > len(xgrid) or gy1 > len(ygrid):
                jx_slice = jy_slice = None
            else:
                jx_slice = xgrid[gx0:gx1]
                jy_slice = ygrid[gy0:gy1]

        mapgen.generate_fractal_overworld(
            world, game.fractal_field, coord, game.rng, up_pos=up_pos,
            overmap_params=game.overmap_params, jx_slice=jx_slice, jy_slice=jy_slice,
        )

        if up_pos is None:
            if "starting_zone" in poi_hits:
                ex = world.width // 2
                ey = world.height // 2
            else:
                ex = world.width // 2
                ey = max(0, world.height - 2)
            if world.in_bounds(ex, ey) and world.is_walkable(ex, ey):
                world.entry = (ex, ey)
    else:
        mapgen.generate_basic(world, game.rng, up_pos=up_pos, coord=coord)

    try:
        poi_hits = mapgen.apply_pois(world, coord, poi_registry=game.poi_registry)
    except Exception:
        poi_hits = []
        world.poi_ids = []
    if bool(getattr(game, "starttsgard_cutover_enabled", False)) and poi_hits:
        filtered_hits = [pid for pid in poi_hits if pid != "starting_zone"]
        if len(filtered_hits) != len(poi_hits):
            poi_hits = filtered_hits
            world.poi_ids = filtered_hits

    if "starting_zone" in poi_hits and not bool(getattr(game, "starttsgard_cutover_enabled", False)):
        try:
            depot_info = mapgen.build_item_depot(world, game.rng, world.entry)
            world.depot_info = depot_info
        except Exception:
            world.depot_info = None

    lvl = LevelState(
        world=world, entities={}, events=[], order=0, current_tick=0,
        pattern=builder.Pattern(), pattern_anchor=None, activation_points=[],
        activation_ttl=0, awaiting_terminus=False, need_fov=True,
        up_stairs=world.up_stairs, down_stairs=world.down_stairs,
        spotted=set(), coord=coord,
    )

    if depth == 0 and is_lab_zone:
        from edgecaster.state.entities import Entity
        eid = f"lab_state_{game._new_id()}"
        ent = Entity(
            id=eid, name="Fractal Lab", pos=(world.width // 2, world.height // 2),
            abs_pos=game.abs_from_zone_local(coord, (world.width // 2, world.height // 2)),
            kind="lab_state", tags={"chaos": 0.0, "chaos_threshold": 1.0}, render_layer=-1
        )
        lvl.entities[eid] = ent

    difficulty_system.apply_zone_difficulty(game, lvl, coord)
    poi_worldgen.spawn_poi_contents(game, lvl, coord)
    game._sync_level_pattern_view(lvl)
    return lvl