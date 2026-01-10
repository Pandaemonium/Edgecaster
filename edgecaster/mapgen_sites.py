from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, TYPE_CHECKING


from edgecaster.state.world import World

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state.sites import SiteSpec


Room = Tuple[int, int, int, int]  # x, y, w, h


@dataclass(frozen=True)
class LairLayoutResult:
    entry: Tuple[int, int]
    boss_pos: Tuple[int, int]
    rooms: List[Room]


def _fill(world: World, *, glyph: str, walkable: bool) -> None:
    for y in range(world.height):
        for x in range(world.width):
            tile = world.tiles[y][x]
            tile.glyph = glyph
            tile.walkable = walkable


def _carve_floor(world: World, x: int, y: int) -> None:
    tile = world.get_tile(x, y)
    if tile is None:
        return
    tile.glyph = "."
    tile.walkable = True


def _carve_room(world: World, room: Room) -> None:
    x, y, w, h = room
    for yy in range(y, y + h):
        for xx in range(x, x + w):
            if world.in_bounds(xx, yy):
                _carve_floor(world, xx, yy)


def _carve_h_tunnel(world: World, x1: int, x2: int, y: int) -> None:
    for xx in range(min(x1, x2), max(x1, x2) + 1):
        if world.in_bounds(xx, y):
            _carve_floor(world, xx, y)


def _carve_v_tunnel(world: World, y1: int, y2: int, x: int) -> None:
    for yy in range(min(y1, y2), max(y1, y2) + 1):
        if world.in_bounds(x, yy):
            _carve_floor(world, x, yy)


def _nearest_walkable(
    world: World,
    origin: Tuple[int, int],
    *,
    max_radius: int = 32,
) -> Optional[Tuple[int, int]]:
    ox, oy = origin
    if world.in_bounds(ox, oy) and world.is_walkable(ox, oy):
        return origin
    for r in range(1, max_radius + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                x = ox + dx
                y = oy + dy
                if not world.in_bounds(x, y):
                    continue
                if world.is_walkable(x, y):
                    return (x, y)
    return None


def _build_multi_room_lair(world: World, rng) -> LairLayoutResult:
    """Generate a simple multi-room lair layout.

    This is the first (and currently only) lair "building type". Keep the API
    flexible so we can add arena/maze/fortress variants later without touching
    zone-generation glue.
    """
    _fill(world, glyph="#", walkable=False)

    margin = 2
    max_rooms = 9
    min_size = 5
    max_size = 10

    rooms: List[Room] = []
    attempts = 0
    max_attempts = 200

    while len(rooms) < max_rooms and attempts < max_attempts:
        attempts += 1
        w = int(rng.randint(min_size, max_size))
        h = int(rng.randint(min_size, max_size))
        x = int(rng.randint(margin, max(margin, world.width - w - margin)))
        y = int(rng.randint(margin, max(margin, world.height - h - margin)))
        new_room = (x, y, w, h)

        # overlap check (with a 1-tile buffer)
        if any(
            (
                x < rx + rw + 1
                and x + w + 1 > rx
                and y < ry + rh + 1
                and y + h + 1 > ry
            )
            for rx, ry, rw, rh in rooms
        ):
            continue

        _carve_room(world, new_room)

        if rooms:
            # connect to a previous room center
            px, py, pw, ph = rooms[-1]
            prev_cx = px + pw // 2
            prev_cy = py + ph // 2
            cx = x + w // 2
            cy = y + h // 2
            if rng.random() < 0.5:
                _carve_h_tunnel(world, prev_cx, cx, prev_cy)
                _carve_v_tunnel(world, prev_cy, cy, cx)
            else:
                _carve_v_tunnel(world, prev_cy, cy, prev_cx)
                _carve_h_tunnel(world, prev_cx, cx, cy)

        rooms.append(new_room)

    # Entry: middle of the bottom edge (slightly inset so the outer border can remain walls).
    entry_guess = (world.width // 2, max(0, world.height - 2))
    _carve_floor(world, *entry_guess)

    # Carve a corridor upward toward the closest room center (or just upward a bit).
    if rooms:
        # pick the room whose center is closest to the entry
        best = None
        best_d2 = 1e18
        for rx, ry, rw, rh in rooms:
            cx = rx + rw // 2
            cy = ry + rh // 2
            dx = cx - entry_guess[0]
            dy = cy - entry_guess[1]
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best = (cx, cy)
        if best is not None:
            _carve_v_tunnel(world, entry_guess[1], best[1], entry_guess[0])
            _carve_h_tunnel(world, entry_guess[0], best[0], best[1])
    else:
        _carve_v_tunnel(world, entry_guess[1], max(0, entry_guess[1] - 6), entry_guess[0])

    entry = _nearest_walkable(world, entry_guess) or entry_guess

    # Boss spawn: prefer a room farthest from the entry to encourage exploration.
    boss_guess = (world.width // 2, world.height // 2)
    if rooms:
        farthest = None
        farthest_d2 = -1
        ex, ey = entry
        for rx, ry, rw, rh in rooms:
            cx = rx + rw // 2
            cy = ry + rh // 2
            dx = cx - ex
            dy = cy - ey
            d2 = dx * dx + dy * dy
            if d2 > farthest_d2:
                farthest_d2 = d2
                farthest = (cx, cy)
        if farthest is not None:
            boss_guess = farthest

    boss_pos = _nearest_walkable(world, boss_guess) or boss_guess

    return LairLayoutResult(entry=entry, boss_pos=boss_pos, rooms=rooms)


LAIR_LAYOUT_BUILDERS: Dict[str, Callable[[World, object], LairLayoutResult]] = {
    # Multi-room building (current default).
    "multi_room": _build_multi_room_lair,
}


def generate_legendary_lair(world: World, rng, *, layout: str = "multi_room") -> LairLayoutResult:
    """Generate a legendary lair zone layout.

    This is a "site" generator: it can fully replace the underlying overworld
    terrain for that zone. Keep this logic in mapgen (not scenes/renderers) so
    it stays deterministic and testable.
    """
    layout = str(layout or "multi_room")
    builder = LAIR_LAYOUT_BUILDERS.get(layout)
    if builder is None:
        builder = LAIR_LAYOUT_BUILDERS["multi_room"]

    result = builder(world, rng)

    # World integration fields:
    world.entry = result.entry
    world.up_stairs = None
    world.down_stairs = None
    world.is_lair = True  # type: ignore[attr-defined]
    world.lair_info = {  # type: ignore[attr-defined]
        "layout": layout,
        "entry": result.entry,
        "boss_pos": result.boss_pos,
        "rooms": result.rooms,
    }

    return result


# ---------------------------------------------------------------------------
# Biome-Based Site Realization
# ---------------------------------------------------------------------------

def _find_open_area(
    level: "LevelState",
    min_size: int = 5,
    max_attempts: int = 50,
    rng: random.Random = None,
) -> Optional[Tuple[int, int, int, int]]:
    """Find an open rectangular area in the level.

    Returns (x0, y0, width, height) or None if not found.
    """
    world = level.world
    if rng is None:
        rng = random.Random()

    for _ in range(max_attempts):
        x0 = rng.randint(2, world.width - min_size - 2)
        y0 = rng.randint(2, world.height - min_size - 2)
        w = rng.randint(min_size, min(min_size + 4, world.width - x0 - 2))
        h = rng.randint(min_size, min(min_size + 3, world.height - y0 - 2))

        # Check if area is mostly walkable
        walkable = 0
        total = w * h
        for dy in range(h):
            for dx in range(w):
                if world.is_walkable(x0 + dx, y0 + dy):
                    walkable += 1

        if walkable >= total * 0.7:
            return (x0, y0, w, h)

    return None


def _stamp_walls_site(
    level: "LevelState",
    x0: int, y0: int,
    w: int, h: int,
    door_side: str = "south",
) -> Optional[Tuple[int, int]]:
    """Stamp a rectangular wall structure with a door.

    Returns the door position (x, y) or None.
    """
    world = level.world
    door_pos = None

    for dy in range(h):
        for dx in range(w):
            x, y = x0 + dx, y0 + dy
            if not world.in_bounds(x, y):
                continue

            tile = world.get_tile(x, y)
            if tile is None:
                continue

            is_border = (dx == 0 or dx == w - 1 or dy == 0 or dy == h - 1)

            if is_border:
                tile.walkable = False
                tile.glyph = "#"
                tile.color = (120, 100, 80)
            else:
                tile.walkable = True
                tile.glyph = "."
                tile.color = (80, 70, 60)

    if door_side == "south":
        door_pos = (x0 + w // 2, y0 + h - 1)
    elif door_side == "north":
        door_pos = (x0 + w // 2, y0)
    elif door_side == "east":
        door_pos = (x0 + w - 1, y0 + h // 2)
    elif door_side == "west":
        door_pos = (x0, y0 + h // 2)

    if door_pos and world.in_bounds(*door_pos):
        tile = world.get_tile(*door_pos)
        if tile:
            tile.walkable = True
            tile.glyph = "+"
            tile.color = (160, 120, 80)

    return door_pos


def _stamp_platform_site(
    level: "LevelState",
    x0: int, y0: int,
    w: int, h: int,
    glyph: str = "=",
    color: Tuple[int, int, int] = (140, 130, 120),
) -> None:
    """Stamp an open platform (no walls, just floor tiles)."""
    world = level.world

    for dy in range(h):
        for dx in range(w):
            x, y = x0 + dx, y0 + dy
            if not world.in_bounds(x, y):
                continue

            tile = world.get_tile(x, y)
            if tile:
                tile.walkable = True
                tile.glyph = glyph
                tile.color = color


def _stamp_landmark_site(
    level: "LevelState",
    x: int, y: int,
    glyph: str = "O",
    color: Tuple[int, int, int] = (200, 180, 100),
    walkable: bool = False,
) -> None:
    """Stamp a single landmark tile."""
    world = level.world
    if world.in_bounds(x, y):
        tile = world.get_tile(x, y)
        if tile:
            tile.walkable = walkable
            tile.glyph = glyph
            tile.color = color


def _spawn_site_npc(
    game: "Game",
    level: "LevelState",
    npc_id: str,
    pos: Tuple[int, int],
    site: "SiteSpec",
) -> bool:
    """Spawn an NPC for a site. Returns True if spawned."""
    from edgecaster.systems import spawning
    from edgecaster.state.actors import Human, Stats

    final_pos = spawning.find_nearest_walkable(game, level, pos, max_radius=5)
    if final_pos is None:
        return False

    aid = game._new_id()
    npc = Human(
        id=aid,
        name=npc_id.replace("_", " ").title(),
        pos=final_pos,
        faction="npc",
        stats=Stats(hp=1, max_hp=1),
        tags={"npc_id": npc_id, "site_id": site.id},
        disposition=5,
    )
    npc.description = f"A resident of the {site.tags.get('name', site.kind)}."

    level.actors[aid] = npc
    level.entities[aid] = npc
    return True


def _spawn_site_enemy(
    game: "Game",
    level: "LevelState",
    enemy_id: str,
    pos: Tuple[int, int],
    site: "SiteSpec",
) -> bool:
    """Spawn an enemy for a site. Returns True if spawned."""
    from edgecaster.systems import spawning
    from edgecaster.enemies import factory as enemy_factory

    final_pos = spawning.find_spawn_position(
        game, level, near=pos, radius=4, avoid_actors=True
    )
    if final_pos is None:
        return False

    try:
        mob = enemy_factory.spawn_enemy(enemy_id, final_pos)
        mob.tags = getattr(mob, "tags", None) or {}
        mob.tags["site_id"] = site.id

        spawning.register_actor(game, level, mob, schedule_ai=True)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Site Type Realizers
# ---------------------------------------------------------------------------

def realize_bird_aerie(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
    rng: random.Random,
) -> None:
    """Realize a bird people aerie settlement."""
    world = level.world

    area = _find_open_area(level, min_size=6, rng=rng)
    if area is None:
        return

    x0, y0, w, h = area
    tower_x = x0 + w // 2 - 2
    tower_y = y0 + h // 2 - 2
    _stamp_walls_site(level, tower_x, tower_y, 5, 5, door_side="south")

    offsets = [(-4, -2), (4, -2), (-4, 3), (4, 3)]
    for dx, dy in offsets:
        px, py = tower_x + dx, tower_y + dy
        if world.in_bounds(px, py) and world.in_bounds(px + 2, py + 2):
            _stamp_platform_site(level, px, py, 3, 2, glyph="=", color=(160, 150, 140))

    npc_pool = site.tags.get("npc_pool", [])
    if npc_pool:
        npc_id = rng.choice(npc_pool)
        _spawn_site_npc(game, level, npc_id, (tower_x + 2, tower_y + 2), site)

    enemy_pool = site.tags.get("enemy_pool", [])
    if enemy_pool:
        for i in range(rng.randint(1, 3)):
            enemy_id = rng.choice(enemy_pool)
            guard_x = tower_x + rng.randint(-3, 6)
            guard_y = tower_y + rng.randint(-3, 6)
            _spawn_site_enemy(game, level, enemy_id, (guard_x, guard_y), site)


def realize_fishing_village(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
    rng: random.Random,
) -> None:
    """Realize a fishing village settlement."""
    world = level.world

    water_tiles = []
    for y in range(world.height):
        for x in range(world.width):
            tile = world.get_tile(x, y)
            if tile and tile.glyph == "~":
                water_tiles.append((x, y))

    if not water_tiles:
        area = _find_open_area(level, min_size=5, rng=rng)
        if area is None:
            return
        x0, y0, w, h = area
    else:
        wx, wy = rng.choice(water_tiles)
        x0 = max(2, min(world.width - 8, wx + rng.randint(-3, 3)))
        y0 = max(2, min(world.height - 8, wy + rng.randint(1, 4)))
        w, h = 6, 5

    dock_x = x0 + w // 2
    dock_y = y0 - 2
    if dock_y >= 1:
        _stamp_platform_site(level, dock_x - 1, dock_y, 3, 2, glyph="=", color=(100, 80, 60))

    _stamp_walls_site(level, x0, y0, 5, 4, door_side="south")

    hut_x = x0 + 6
    if world.in_bounds(hut_x, y0):
        _stamp_walls_site(level, hut_x, y0, 4, 3, door_side="south")

    npc_pool = site.tags.get("npc_pool", [])
    if npc_pool:
        npc_id = rng.choice(npc_pool)
        _spawn_site_npc(game, level, npc_id, (x0 + 2, y0 + 2), site)


def realize_spriggan_grove(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
    rng: random.Random,
) -> None:
    """Realize a spriggan elder tree grove."""
    world = level.world

    area = _find_open_area(level, min_size=7, rng=rng)
    if area is None:
        return

    x0, y0, w, h = area
    tree_x = x0 + w // 2
    tree_y = y0 + h // 2
    _stamp_landmark_site(level, tree_x, tree_y, glyph="Y", color=(60, 100, 50), walkable=False)
    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        _stamp_landmark_site(level, tree_x + dx, tree_y + dy, glyph="T", color=(80, 60, 40), walkable=False)

    ring_offsets = [(-3, -2), (-2, -3), (0, -3), (2, -3), (3, -2),
                    (-3, 2), (-2, 3), (0, 3), (2, 3), (3, 2),
                    (-3, 0), (3, 0)]
    for dx, dy in ring_offsets:
        if rng.random() < 0.6:
            sx, sy = tree_x + dx, tree_y + dy
            if world.in_bounds(sx, sy):
                _stamp_landmark_site(level, sx, sy, glyph="t", color=(70, 110, 60), walkable=False)

    for _ in range(rng.randint(2, 4)):
        fx = tree_x + rng.randint(-5, 5)
        fy = tree_y + rng.randint(-5, 5)
        if world.in_bounds(fx, fy):
            _stamp_landmark_site(level, fx, fy, glyph="o", color=(180, 160, 140), walkable=True)

    npc_pool = site.tags.get("npc_pool", [])
    if npc_pool:
        npc_id = rng.choice(npc_pool)
        _spawn_site_npc(game, level, npc_id, (tree_x - 2, tree_y), site)

    enemy_pool = site.tags.get("enemy_pool", [])
    if enemy_pool and rng.random() < 0.5:
        enemy_id = rng.choice(enemy_pool)
        _spawn_site_enemy(game, level, enemy_id, (tree_x + 3, tree_y + 2), site)


def realize_desert_camp(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
    rng: random.Random,
) -> None:
    """Realize a desert nomad camp."""
    world = level.world

    area = _find_open_area(level, min_size=6, rng=rng)
    if area is None:
        return

    x0, y0, w, h = area
    tent_positions = [(x0, y0), (x0 + 4, y0), (x0 + 2, y0 + 3)]
    for i, (tx, ty) in enumerate(tent_positions):
        if world.in_bounds(tx, ty) and world.in_bounds(tx + 2, ty + 2):
            _stamp_platform_site(level, tx, ty, 3, 2, glyph="^", color=(200, 180, 140))

    trade_x = x0 + 2
    trade_y = y0 + 1
    _stamp_landmark_site(level, trade_x, trade_y, glyph="$", color=(220, 200, 100), walkable=True)

    cache_x = x0 + w - 2
    cache_y = y0 + h - 2
    if world.in_bounds(cache_x, cache_y):
        _stamp_landmark_site(level, cache_x, cache_y, glyph="~", color=(100, 150, 200), walkable=True)

    npc_pool = site.tags.get("npc_pool", [])
    if npc_pool:
        npc_id = rng.choice(npc_pool)
        _spawn_site_npc(game, level, npc_id, (trade_x + 1, trade_y), site)

    enemy_pool = site.tags.get("enemy_pool", [])
    if enemy_pool and rng.random() < 0.4:
        enemy_id = rng.choice(enemy_pool)
        _spawn_site_enemy(game, level, enemy_id, (x0 - 3, y0 + rng.randint(0, h)), site)


def realize_hunter_lodge(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
    rng: random.Random,
) -> None:
    """Realize a hunter's lodge."""
    world = level.world

    area = _find_open_area(level, min_size=6, rng=rng)
    if area is None:
        return

    x0, y0, w, h = area
    _stamp_walls_site(level, x0, y0, 6, 5, door_side="south")

    rack_x = x0 + 7
    if world.in_bounds(rack_x, y0):
        _stamp_platform_site(level, rack_x, y0, 2, 3, glyph="|", color=(120, 100, 70))

    trap_x = x0 - 3
    if world.in_bounds(trap_x, y0 + 2):
        _stamp_platform_site(level, trap_x, y0 + 2, 2, 2, glyph="x", color=(140, 120, 90))

    npc_pool = site.tags.get("npc_pool", [])
    if npc_pool:
        npc_id = rng.choice(npc_pool)
        _spawn_site_npc(game, level, npc_id, (x0 + 3, y0 + 2), site)

    enemy_pool = site.tags.get("enemy_pool", [])
    if enemy_pool and rng.random() < 0.5:
        for _ in range(rng.randint(1, 2)):
            enemy_id = rng.choice(enemy_pool)
            ex = x0 + rng.randint(-6, w + 6)
            ey = y0 + rng.randint(-6, h + 6)
            _spawn_site_enemy(game, level, enemy_id, (ex, ey), site)


def realize_tropical_shrine(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
    rng: random.Random,
) -> None:
    """Realize a shaman's shrine."""
    world = level.world

    area = _find_open_area(level, min_size=5, rng=rng)
    if area is None:
        return

    x0, y0, w, h = area
    shrine_x = x0 + w // 2
    shrine_y = y0 + h // 2
    _stamp_landmark_site(level, shrine_x, shrine_y, glyph="*", color=(255, 200, 100), walkable=False)

    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, -1), (-1, 1), (1, 1)]:
        cx, cy = shrine_x + dx, shrine_y + dy
        if world.in_bounds(cx, cy):
            _stamp_landmark_site(level, cx, cy, glyph="o", color=(180, 140, 80), walkable=True)

    totem_offsets = [(-4, -3), (4, -3), (-4, 3), (4, 3)]
    for dx, dy in totem_offsets:
        if rng.random() < 0.7:
            tx, ty = shrine_x + dx, shrine_y + dy
            if world.in_bounds(tx, ty):
                _stamp_landmark_site(level, tx, ty, glyph="I", color=(100, 80, 60), walkable=False)

    npc_pool = site.tags.get("npc_pool", [])
    if npc_pool:
        npc_id = rng.choice(npc_pool)
        _spawn_site_npc(game, level, npc_id, (shrine_x + 1, shrine_y + 1), site)

    enemy_pool = site.tags.get("enemy_pool", [])
    if enemy_pool and rng.random() < 0.3:
        enemy_id = rng.choice(enemy_pool)
        _spawn_site_enemy(game, level, enemy_id, (shrine_x + rng.randint(-5, 5), shrine_y + rng.randint(-5, 5)), site)


def realize_corruption_outpost(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
    rng: random.Random,
) -> None:
    """Realize a corruption cult outpost."""
    world = level.world

    area = _find_open_area(level, min_size=6, rng=rng)
    if area is None:
        return

    x0, y0, w, h = area
    obelisk_x = x0 + w // 2
    obelisk_y = y0 + h // 2
    _stamp_landmark_site(level, obelisk_x, obelisk_y, glyph="!", color=(150, 50, 180), walkable=False)

    for dx in range(-2, 3):
        for dy in range(-2, 3):
            px, py = obelisk_x + dx, obelisk_y + dy
            if (dx != 0 or dy != 0) and world.in_bounds(px, py):
                tile = world.get_tile(px, py)
                if tile:
                    tile.glyph = "."
                    tile.color = (60, 40, 70)
                    tile.walkable = True

    cage_y = y0 - 2
    if cage_y >= 1:
        for i in range(3):
            cx = x0 + 1 + i * 2
            if world.in_bounds(cx, cage_y):
                _stamp_landmark_site(level, cx, cage_y, glyph="#", color=(100, 80, 90), walkable=False)

    npc_pool = site.tags.get("npc_pool", [])
    if npc_pool:
        npc_id = rng.choice(npc_pool)
        _spawn_site_npc(game, level, npc_id, (obelisk_x + 2, obelisk_y), site)

    enemy_pool = site.tags.get("enemy_pool", [])
    if enemy_pool:
        for _ in range(rng.randint(2, 4)):
            enemy_id = rng.choice(enemy_pool)
            ex = obelisk_x + rng.randint(-4, 4)
            ey = obelisk_y + rng.randint(-4, 4)
            _spawn_site_enemy(game, level, enemy_id, (ex, ey), site)


# ---------------------------------------------------------------------------
# Site Realization Dispatcher
# ---------------------------------------------------------------------------

_SITE_REALIZERS: Dict[str, Callable[["Game", "LevelState", "SiteSpec", random.Random], None]] = {
    "bird_aerie": realize_bird_aerie,
    "fishing_village": realize_fishing_village,
    "spriggan_grove": realize_spriggan_grove,
    "desert_camp": realize_desert_camp,
    "hunter_lodge": realize_hunter_lodge,
    "tropical_shrine": realize_tropical_shrine,
    "corruption_outpost": realize_corruption_outpost,
}


def realize_site(
    game: "Game",
    level: "LevelState",
    site: "SiteSpec",
) -> bool:
    """
    Realize a site in the given level.

    This stamps structures and spawns NPCs/enemies for the site.
    Should be called after base terrain generation.

    Returns True if realization succeeded.
    """
    debug = getattr(game, "_debug", None)

    # Check if site has already been realized (to prevent double-realization)
    if getattr(site, "_realized", False):
        if debug:
            debug(f"[realize_site] {site.kind} at {site.coord} already realized, skipping")
        return False

    realizer = _SITE_REALIZERS.get(site.kind)
    if realizer is None:
        if debug:
            debug(f"[realize_site] No realizer found for site kind '{site.kind}'")
        return False

    rng = random.Random(site.seed)

    try:
        realizer(game, level, site, rng)
        # Mark as realized to prevent double-realization
        site._realized = True  # type: ignore[attr-defined]
        if debug:
            debug(f"[realize_site] Successfully realized {site.kind} at {site.coord}")
        return True
    except Exception as e:
        if debug:
            debug(f"[realize_site] Exception realizing {site.kind} at {site.coord}: {e!r}")
        return False


def realize_sites_in_zone(
    game: "Game",
    level: "LevelState",
    zx: int,
    zy: int,
    depth: int = 0,
) -> Tuple[int, Optional["SiteSpec"]]:
    """
    Realize all sites at a zone coordinate.

    Returns a tuple of (count of sites realized, the site spec if realized).
    """
    from edgecaster.systems.site_placement import get_site_at_zone, discover_site_at_zone

    debug = getattr(game, "_debug", None)
    site = get_site_at_zone(game, zx, zy, depth)
    if debug:
        registry = getattr(game, "site_registry", None)
        registry_len = len(registry) if registry else 0
        debug(f"[realize_sites] get_site_at_zone({zx}, {zy}, {depth}) -> {site}, registry has {registry_len} sites")

    if site is None:
        return (0, None)

    success = realize_site(game, level, site)
    if debug:
        debug(f"[realize_sites] realize_site for {site.kind} at ({zx}, {zy}) -> success={success}")

    if success:
        discover_site_at_zone(game, zx, zy, depth)
        return (1, site)

    return (0, None)

