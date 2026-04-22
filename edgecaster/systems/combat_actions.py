"""Combat action runtime extracted from Game orchestrator.

This module owns the heavy simulation logic for combat/pattern actions that were
previously embedded in ``game.py``. Functions take ``game`` as the first arg.
"""

from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from edgecaster.patterns.activation import project_vertices
from edgecaster.state.actors import Actor
from edgecaster.systems import damage_policy as damage_policy_system
from edgecaster.systems import chakra_items as chakra_items_system
from edgecaster.systems import entity_ops as entity_ops_system
from edgecaster.systems import footprints as footprints_system

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.patterns import builder


def _line_points(x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
    """Integer tile line helper (Bresenham), local to combat actions."""
    points = []
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


def _tile_centers_from_rect(
    rect: Tuple[float, float, float, float],
    *,
    max_points: int = 64,
) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    seen: set[Tuple[int, int]] = set()
    for tx, ty in footprints_system.iter_tiles_overlapped_by_rect(rect):
        key = (int(tx), int(ty))
        if key in seen:
            continue
        seen.add(key)
        out.append((float(key[0]) + 0.5, float(key[1]) + 0.5))
        if len(out) >= int(max_points):
            break
    return out


def _target_probe_points_local(obj: Any, *, max_points: int = 64) -> List[Tuple[float, float]]:
    try:
        rect = footprints_system.entity_footprint_local(obj)
        pts = _tile_centers_from_rect(rect, max_points=max_points)
        if pts:
            return pts
    except Exception:
        pass
    pos = getattr(obj, "pos", None)
    if pos is not None:
        return [(float(int(pos[0])) + 0.5, float(int(pos[1])) + 0.5)]
    return []


def _target_probe_points_abs(obj: Any, *, max_points: int = 64) -> List[Tuple[float, float]]:
    try:
        rect = footprints_system.entity_footprint_abs(obj)
        pts = _tile_centers_from_rect(rect, max_points=max_points)
        if pts:
            return pts
    except Exception:
        pass
    ap = getattr(obj, "abs_pos", None)
    if ap is not None:
        return [(float(int(ap[0])) + 0.5, float(int(ap[1])) + 0.5)]
    pos = getattr(obj, "pos", None)
    if pos is not None:
        return [(float(int(pos[0])) + 0.5, float(int(pos[1])) + 0.5)]
    return []


def _target_tiles_local(obj: Any, *, max_tiles: int = 128) -> List[Tuple[int, int]]:
    try:
        rect = footprints_system.entity_footprint_local(obj)
        out: List[Tuple[int, int]] = []
        seen: set[Tuple[int, int]] = set()
        for tx, ty in footprints_system.iter_tiles_overlapped_by_rect(rect):
            key = (int(tx), int(ty))
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
            if len(out) >= int(max_tiles):
                break
        if out:
            return out
    except Exception:
        pass
    pos = getattr(obj, "pos", None)
    if pos is not None:
        return [(int(pos[0]), int(pos[1]))]
    return []


def _target_probe_centroid_local(obj: Any) -> Optional[Tuple[float, float]]:
    probes = _target_probe_points_local(obj)
    if not probes:
        return None
    sx = sum(float(p[0]) for p in probes)
    sy = sum(float(p[1]) for p in probes)
    n = float(len(probes))
    if n <= 0.0:
        return None
    return (sx / n, sy / n)


def _wind_rush_start_vertex_candidates(
    self,
    actor_tile: Tuple[int, int],
    world_vertices: List[Tuple[float, float]],
    pattern: builder.Pattern,
) -> set[int]:
    """Return graph vertices that the actor can start Wind Rush from.

    A start is valid when the actor is:
    - standing on a projected vertex tile, or
    - standing on any projected edge tile (then either endpoint may start).
    """
    ax, ay = int(actor_tile[0]), int(actor_tile[1])
    candidates: set[int] = set()

    # Exact vertex contact.
    for idx, (vx, vy) in enumerate(world_vertices):
        if int(round(vx)) == ax and int(round(vy)) == ay:
            candidates.add(idx)

    # Edge contact. We still scan edges even if a vertex match exists, because
    # standing exactly on an edge endpoint should preserve both graph options.
    actor_pos = (ax, ay)
    for e in getattr(pattern, "edges", []) or []:
        try:
            a_idx = int(getattr(e, "a"))
            b_idx = int(getattr(e, "b"))
        except Exception:
            continue
        if (
            a_idx < 0
            or b_idx < 0
            or a_idx >= len(world_vertices)
            or b_idx >= len(world_vertices)
        ):
            continue
        av = world_vertices[a_idx]
        bv = world_vertices[b_idx]
        line = _line_points(
            int(round(av[0])),
            int(round(av[1])),
            int(round(bv[0])),
            int(round(bv[1])),
        )
        if actor_pos in line:
            candidates.add(a_idx)
            candidates.add(b_idx)

    return candidates

def _wind_rush_vertex_path(
    self,
    pattern: builder.Pattern,
    start_candidates: set[int],
    target_idx: int,
    num_vertices: int,
) -> Optional[List[int]]:
    """Shortest vertex-index path on the rune graph from starts to target."""
    if not start_candidates:
        return None
    if target_idx in start_candidates:
        return [target_idx]

    adj: Dict[int, List[int]] = {}
    for e in getattr(pattern, "edges", []) or []:
        try:
            a = int(getattr(e, "a"))
            b = int(getattr(e, "b"))
        except Exception:
            continue
        if a < 0 or b < 0 or a >= num_vertices or b >= num_vertices:
            continue
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    q: deque[int] = deque()
    prev: Dict[int, Optional[int]] = {}
    for s in start_candidates:
        if s < 0 or s >= num_vertices:
            continue
        q.append(s)
        prev[s] = None

    found = False
    while q:
        cur = q.popleft()
        if cur == target_idx:
            found = True
            break
        for nxt in adj.get(cur, []):
            if nxt in prev:
                continue
            prev[nxt] = cur
            q.append(nxt)

    if not found or target_idx not in prev:
        return None

    path: List[int] = []
    node: Optional[int] = target_idx
    while node is not None:
        path.append(node)
        node = prev.get(node)
    path.reverse()
    return path

def _wind_rush_local_path_points(
    self,
    actor_tile: Tuple[int, int],
    path_indices: List[int],
    world_vertices: List[Tuple[float, float]],
) -> List[Tuple[int, int]]:
    """Build local tile path points by walking graph segments in order."""
    points: List[Tuple[int, int]] = [(int(actor_tile[0]), int(actor_tile[1]))]
    if not path_indices:
        return points

    first = world_vertices[path_indices[0]]
    first_tile = (int(round(first[0])), int(round(first[1])))
    if first_tile != points[-1]:
        for p in _line_points(points[-1][0], points[-1][1], first_tile[0], first_tile[1]):
            if p != points[-1]:
                points.append(p)

    for i in range(len(path_indices) - 1):
        a = world_vertices[path_indices[i]]
        b = world_vertices[path_indices[i + 1]]
        a_tile = (int(round(a[0])), int(round(a[1])))
        b_tile = (int(round(b[0])), int(round(b[1])))
        for p in _line_points(a_tile[0], a_tile[1], b_tile[0], b_tile[1]):
            if p != points[-1]:
                points.append(p)

    return points

def wind_rush_preview(
    self,
    target_vertex: Optional[int],
    *,
    actor_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Preview graph-walk data for Wind Rush (used by targeting + execution)."""
    level = self._level()
    if actor_id is None:
        actor_id = self.player_id
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return None, "No actor to rush."

    origin = self._activation_origin(level)
    if origin is None or not getattr(level.pattern, "vertices", None):
        return None, "No rune pattern to rush through."

    from edgecaster.patterns.activation import project_vertices

    world_vertices = project_vertices(level.pattern, origin)
    if not world_vertices:
        return None, "No rune vertices to rush to."
    if target_vertex is None:
        return None, "Choose a vertex to Wind Rush to."

    try:
        v_idx = int(target_vertex)
    except Exception:
        return None, "Invalid Wind Rush target."
    if v_idx < 0 or v_idx >= len(world_vertices):
        return None, "Invalid Wind Rush target."

    target_local = (
        int(round(world_vertices[v_idx][0])),
        int(round(world_vertices[v_idx][1])),
    )
    if not level.world.in_bounds(*target_local):
        return None, "That vertex is beyond your reach."
    if not level.world.is_walkable(*target_local):
        return None, "You cannot rush into solid terrain."

    start_candidates = self._wind_rush_start_vertex_candidates(
        actor.pos,
        world_vertices,
        level.pattern,
    )
    if not start_candidates:
        return None, "Stand on the rune to Wind Rush."

    path_indices = self._wind_rush_vertex_path(
        level.pattern,
        start_candidates,
        v_idx,
        len(world_vertices),
    )
    if not path_indices:
        return None, "No connected rune path to that vertex."

    path_local = self._wind_rush_local_path_points(
        actor.pos,
        path_indices,
        world_vertices,
    )
    return {
        "target_vertex": v_idx,
        "target_local": target_local,
        "path_indices": path_indices,
        "path_local": path_local,
    }, None

def act_wind_rush(self, actor_id: str, target_vertex: Optional[int]) -> None:
    """Dash along rune edges to a selected vertex and strike hostiles on path.

    Design notes:
    - Target is a *vertex index* from the current projected pattern.
    - Caster must stand on the rune (vertex or edge) to initiate.
    - Movement is applied in ABS-space so crossing zone boundaries stays seamless.
    - Action timing is fixed by the action registry (`speed=5` in actions.py).
    """
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    preview, fail_text = self.wind_rush_preview(target_vertex, actor_id=actor_id)
    if preview is None:
        if actor_id == self.player_id and fail_text:
            self.log.add(fail_text)
        return

    # Mana gate kept moderate; cooldown is the primary limiter.
    mana_cost = 20
    try:
        if actor.stats.mana < mana_cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana for Wind Rush.")
            return
        actor.stats.mana -= mana_cost
        actor.stats.clamp()
    except Exception:
        return

    target_local = preview["target_local"]
    path_local = preview["path_local"]

    actor_abs = getattr(actor, "abs_pos", None)
    if actor_abs is None:
        actor_abs = self.abs_from_zone_local(level.coord, actor.pos)
    target_abs = self.abs_from_zone_local(level.coord, target_local)

    # Convert local graph-walk path to ABS-space so damage remains consistent near edges.
    path_abs = [
        self.abs_from_zone_local(level.coord, (int(p[0]), int(p[1])))
        for p in path_local
    ]
    path_centers: List[Tuple[float, float]] = [
        (float(x) + 0.5, float(y) + 0.5) for (x, y) in path_abs
    ]
    path_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    for i in range(len(path_centers) - 1):
        path_segments.append((path_centers[i], path_centers[i + 1]))

    # Coarse bounds for cheap early-out before exact distance tests.
    if path_centers:
        min_px = min(p[0] for p in path_centers)
        max_px = max(p[0] for p in path_centers)
        min_py = min(p[1] for p in path_centers)
        max_py = max(p[1] for p in path_centers)
    else:
        min_px = max_px = min_py = max_py = 0.0

    def _point_segment_dist(
        px: float,
        py: float,
        ax: float,
        ay: float,
        bx: float,
        by: float,
    ) -> float:
        """Distance from point P to segment AB in tile-space."""
        vx = bx - ax
        vy = by - ay
        wx = px - ax
        wy = py - ay
        vv = vx * vx + vy * vy
        if vv <= 1e-9:
            return math.hypot(px - ax, py - ay)
        t = (wx * vx + wy * vy) / vv
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        qx = ax + t * vx
        qy = ay + t * vy
        return math.hypot(px - qx, py - qy)

    def _distance_to_path(px: float, py: float) -> float:
        """Minimum distance from a point to the rush polyline."""
        if not path_centers:
            return 1e9
        # Fast reject outside expanded path bbox.
        if px < (min_px - 1.0) or px > (max_px + 1.0) or py < (min_py - 1.0) or py > (max_py + 1.0):
            return 1e9
        if not path_segments:
            cx, cy = path_centers[0]
            return math.hypot(px - cx, py - cy)
        best = 1e9
        for (a, b) in path_segments:
            d = _point_segment_dist(px, py, a[0], a[1], b[0], b[1])
            if d < best:
                best = d
        return best

    # Brief overlay hint for the dash line (projected in current zone view).
    # Activation overlay currently consumes local coords.
    try:
        level.activation_points = [(float(x), float(y)) for (x, y) in path_local]
        level.activation_ttl = max(6, int(getattr(self.cfg, "pattern_overlay_ttl", 12) // 2))
    except Exception:
        pass

    # Damage hostiles on path. Intentionally excludes self/friendlies/environment.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    base_damage = 8
    hit_radius_tiles = 1.0
    caster_is_player = actor_id == self.player_id
    hit_count = 0
    seen_ids: set[str] = set()
    # Use loaded active levels only; this keeps runtime predictable.
    for lvl in self._loaded_active_levels():
        for tid, obj in damage_policy_system.iter_damage_targets(
            self,
            lvl,
            actor_id,
            policy,
            include_actors=True,
            include_entities=False,
        ):
            if tid in seen_ids:
                continue
            seen_ids.add(tid)

            probe_points = _target_probe_points_abs(obj)
            if not probe_points:
                continue
            # Distance measured from target footprint probes to rush polyline.
            d = min(_distance_to_path(px, py) for (px, py) in probe_points)
            if d > hit_radius_tiles:
                continue
            scale = max(0.0, 1.0 - (d / hit_radius_tiles))
            dmg = int(math.ceil(base_damage * scale))
            if dmg <= 0:
                continue

            try:
                obj.stats.hp -= dmg
                if hasattr(obj.stats, "clamp"):
                    obj.stats.clamp()
            except Exception:
                continue

            hit_count += 1
            if caster_is_player:
                self.log.add(f"Wind Rush cuts {getattr(obj, 'name', 'something')} for {dmg}.")

            if int(getattr(obj.stats, "hp", 0)) <= 0 and entity_ops_system.get_actor(lvl, tid) is not None:
                self._kill_actor(
                    lvl,
                    obj,
                    killer_id=actor_id,
                    killer_is_player=caster_is_player,
                )

    # Move actor after applying path damage.
    if actor_id == self.player_id:
        self._move_player_to_abs((int(target_abs[0]), int(target_abs[1])))
    else:
        self._move_actor_to_abs(
            actor,
            (int(target_abs[0]), int(target_abs[1])),
            from_level=level,
        )

    if caster_is_player:
        if hit_count > 0:
            self.log.add(f"You surge along the rune path ({hit_count} hit).")
        else:
            self.log.add("You surge along the rune path.")


def act_throw_flask(
    self,
    actor_id: str,
    target_pos: Optional[Tuple[int, int]],
) -> None:
    """Throw an energy flask to activate nearby vertices with high damage."""
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return

    # Validate target position
    if target_pos is None or not level.world.in_bounds(*target_pos):
        self.log.add("Invalid target location.")
        return

    # Find the equipped flask
    from edgecaster.systems.item_grants import find_grant_origin

    inv = self.get_inventory(actor_id)
    flask = find_grant_origin(inv, "throw_flask")

    if flask is None:
        self.log.add("No energy flask equipped.")
        return

    # Get pattern origin and vertices
    origin = self._activation_origin(level)
    if origin is None or not level.pattern.vertices:
        self.log.add("No rune pattern active to energize.")
        self._consume_flask(actor_id, flask)  # Still consume the flask
        return

    world_vertices = project_vertices(level.pattern, origin)

    # Find vertices within flask radius
    flask_radius = 3.0  # 3-tile radius impact zone
    per_vertex_damage = 5
    damage_cap = 100

    tx, ty = target_pos
    center_x = tx + 0.5  # Tile center
    center_y = ty + 0.5

    active_verts = []
    r2 = flask_radius * flask_radius

    for vx, vy in world_vertices:
        dx = vx - center_x
        dy = vy - center_y
        if dx * dx + dy * dy <= r2:
            active_verts.append((vx, vy))

    if not active_verts:
        self.log.add("The flask shatters, but no vertices were in range.")
        self._consume_flask(actor_id, flask)
        return

    # Apply damage to nearby enemies
    from edgecaster.patterns.activation import damage_from_vertices

    # Hostile-only splash (no self/environment), centralized via policy.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    hit_count = 0
    for _tid, enemy in damage_policy_system.iter_damage_targets(
        self,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        if not getattr(enemy, "alive", True):
            continue

        # Calculate damage based on vertices near any overlapped target tile.
        dmg = 0
        for tile in _target_tiles_local(enemy):
            probe = damage_from_vertices(
                active_verts,
                tile,
                flask_radius,
                per_vertex_damage,
                cap=damage_cap,
            )
            if probe > dmg:
                dmg = probe
            if dmg >= int(damage_cap):
                break

        if dmg > 0:
            enemy.stats.hp -= dmg
            hit_count += 1
            self.log.add(f"Arcane energy sears {enemy.name} for {dmg} damage!")

            if enemy.stats.hp <= 0:
                self._kill_actor(
                    level,
                    enemy,
                    killer_id=actor_id,
                    killer_is_player=(actor_id == self.player_id),
                )

    # Log result
    if hit_count == 0:
        self.log.add(f"The flask energizes {len(active_verts)} vertices, but no enemies are nearby.")
    else:
        self.log.add(f"Flask impact: {len(active_verts)} vertices activated!")

    # Consume one flask from the stack
    self._consume_flask(actor_id, flask)


def _consume_flask(self, actor_id: str, flask_item: Any) -> None:
    """Consume one flask from the equipped stack."""
    from edgecaster.systems.inventory import get_quantity, set_quantity
    from edgecaster.systems import equipment as equipment_system

    qty = get_quantity(flask_item)

    if qty > 1:
        # Reduce stack by 1
        set_quantity(flask_item, qty - 1)
        self.log.add(f"Flask thrown. {qty - 1} remaining.")
    else:
        # Last flask - remove from inventory and unequip
        from edgecaster.systems import inventory as inventory_system
        inventory_system.remove_inventory_item(self, actor_id, flask_item)

        # Unequip if it was equipped
        if equipment_system.is_equipped(flask_item):
            try:
                self.unequip_item(actor_id, str(flask_item.id))
            except Exception:
                pass

        self.log.add("Last flask consumed.")

    # Refresh actions (flask action may disappear if stack depleted)
    self.refresh_actor_actions(actor_id)


def act_destabilize(self, actor_id: str) -> None:
    """Teleport randomly within 10 tiles; 50% chance to take 10% max HP."""
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    px, py = actor.pos
    radius = 10
    rng = getattr(self, "rng", None)
    base_rect = footprints_system.entity_footprint_local(actor)

    candidates = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if max(abs(dx), abs(dy)) > radius:
                continue
            tx, ty = px + dx, py + dy
            candidate_rect = footprints_system.rect_translate(
                base_rect,
                float(tx - px),
                float(ty - py),
            )
            try:
                in_bounds = footprints_system.rect_within_bounds(
                    candidate_rect,
                    width=int(level.world.width),
                    height=int(level.world.height),
                )
            except Exception:
                in_bounds = bool(level.world.in_bounds(tx, ty))
            if not in_bounds:
                continue
            if not footprints_system.world_walkable_for_rect(level.world, candidate_rect):
                continue
            if entity_ops_system.first_actor_overlapping_rect(
                level,
                candidate_rect,
                exclude_id=actor_id,
            ):
                continue
            if entity_ops_system.blocking_entity_overlapping_rect(
                level,
                candidate_rect,
                exclude_ids={actor_id},
                ignore_actor_entities=True,
            ):
                continue
            candidates.append((tx, ty))

    if candidates:
        dest = rng.choice(candidates) if rng else candidates[0]
        moved = False
        try:
            dest_abs = self.abs_from_zone_local(level.coord, dest)
            self._move_actor_to_abs(actor, dest_abs, from_level=level)
            moved = True
        except Exception:
            moved = False
        if not moved:
            try:
                self._set_entity_local_pos(actor, dest)
            except Exception:
                actor.pos = dest
            try:
                dest_abs = self.abs_from_zone_local(level.coord, dest)
                self._set_entity_abs_pos(actor, dest_abs)
            except Exception:
                pass
        if actor_id == self.player_id:
            self.log.add(f"You destabilize and reappear at {dest[0]},{dest[1]}.")
        else:
            self.log.add(f"{actor.name} flickers and reappears elsewhere.")
        level.need_fov = True

    # Damage roll: 50% chance
    if (rng.random() < 0.5) if rng else True:
        dmg = max(1, int(actor.stats.max_hp * 0.1))
        actor.stats.hp -= dmg
        actor.stats.clamp()
        if actor_id == self.player_id:
            self.log.add(f"Chaos bites! You take {dmg} damage.")
            if actor.stats.hp <= 0:
                self.set_urgent("by way of destabilization", title="You unravel...", choices=["Continue..."])
        else:
            self.log.add(f"{actor.name} shudders from the destabilization.")
            if actor.stats.hp <= 0:
                self._kill_actor(level, actor, killer_id=actor_id)


def act_ignite(self, actor_id: str) -> None:
    """Ignite red edges for 30 ticks with decaying direct/indirect damage."""
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    pattern = getattr(level, "pattern", None)
    if actor is None or pattern is None or not pattern.edges:
        return

    caster_is_player = actor_id == self.player_id

    # High mana cost gate
    cost = 30
    try:
        if actor.stats.mana < cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana to ignite.")
            return
        actor.stats.mana -= cost
        actor.stats.clamp()
    except Exception:
        pass

    duration = 30
    base_direct = 4.0
    base_indirect = 2.0

    state = {
        "remaining": duration,
        "duration": duration,
        "accum": {},  # target_id -> fractional dmg
        "direct_tiles": [],
        "indirect_tiles": [],
    }
    level.ignite_state = state
    if actor_id == self.player_id:
        self.log.add("You ignite the pattern!")

    def normalize_edge_key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a <= b else (b, a)

    def edge_color_map():
        edge_colors = getattr(pattern, "edge_colors", {}) or {}
        if edge_colors:
            return edge_colors
        return {}

    color_map = edge_color_map()

    def tiles_for_edge(a_idx: int, b_idx: int) -> list[tuple[int, int]]:
        try:
            anchor = getattr(level, "pattern_anchor", None)
            verts = project_vertices(pattern, anchor)
            ax, ay = verts[a_idx]
            bx, by = verts[b_idx]
        except Exception:
            return []
        return _line_points(int(round(ax)), int(round(ay)), int(round(bx)), int(round(by)))

    def apply_tick() -> None:
        if state.get("remaining", 0) <= 0:
            level.ignite_state = None
            return
        anchor = getattr(level, "pattern_anchor", None)
        if anchor is None:
            level.ignite_state = None
            return
        # Decay multiplier
        mult = state["remaining"] / duration

        # Collect direct tiles and redness values
        direct_tiles: dict[tuple[int, int], float] = {}
        for edge in pattern.edges:
            a = getattr(edge, "a", None)
            b = getattr(edge, "b", None)
            if a is None or b is None:
                continue
            col = color_map.get(normalize_edge_key(a, b), None)
            if col is None:
                if isinstance(edge.color, tuple) and len(edge.color) >= 3:
                    col = edge.color
                else:
                    continue
            try:
                r, g, bl = int(col[0]), int(col[1]), int(col[2])
            except Exception:
                continue
            redness = max(0, r - max(g, bl))
            if redness <= 0:
                continue
            for t in tiles_for_edge(a, b):
                prev = direct_tiles.get(t, 0.0)
                if redness > prev:
                    direct_tiles[t] = redness

        if not direct_tiles:
            state["remaining"] = 0
            level.ignite_state = None
            return

        # Indirect tiles: neighbors of direct
        indirect_tiles: dict[tuple[int, int], float] = {}
        for (dx, dy), red in direct_tiles.items():
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    nx, ny = dx + ox, dy + oy
                    if (nx, ny) in direct_tiles:
                        continue
                    prev = indirect_tiles.get((nx, ny), 0.0)
                    if red > prev:
                        indirect_tiles[(nx, ny)] = red

        # Persist tiles for renderer
        state["direct_tiles"] = list(direct_tiles.keys())
        state["indirect_tiles"] = list(indirect_tiles.keys())

        # Damage application:
        # Ignite is intentionally reckless and can hit anything damageable,
        # including the caster and HP-bearing environment entities.
        policy = damage_policy_system.DamagePolicy(
            include_self=True,
            include_hostile=True,
            include_neutral=True,
            include_friendly=True,
            include_environment=True,
        )

        for tid, obj in damage_policy_system.iter_damage_targets(
            self,
            level,
            actor_id,
            policy,
            include_actors=True,
            include_entities=True,
        ):
            tiles = _target_tiles_local(obj)
            if not tiles:
                continue
            dmg_val = 0.0
            best_direct = 0.0
            best_indirect = 0.0
            for tile in tiles:
                red = float(direct_tiles.get(tile, 0.0))
                if red > best_direct:
                    best_direct = red
                red_i = float(indirect_tiles.get(tile, 0.0))
                if red_i > best_indirect:
                    best_indirect = red_i
            if best_direct > 0.0:
                redness = best_direct
                dmg_val = base_direct * (redness / 255.0) * mult
            elif best_indirect > 0.0:
                redness = best_indirect
                dmg_val = base_indirect * (redness / 255.0) * mult
            if dmg_val <= 0:
                continue
            acc = state["accum"].get(tid, 0.0) + dmg_val
            dmg_int = int(acc)
            state["accum"][tid] = acc - dmg_int
            if dmg_int > 0:
                try:
                    obj.stats.hp -= dmg_int
                    obj.stats.clamp()
                    if int(getattr(obj.stats, "hp", 0)) <= 0:
                        if entity_ops_system.get_actor(level, tid) is not None:
                            if tid == self.player_id:
                                self.set_urgent(
                                    "by way of ignition",
                                    title="You unravel...",
                                    choices=["Continue..."],
                                )
                                continue
                            self._kill_actor(
                                level,
                                obj,
                                killer_id=actor_id,
                                killer_is_player=caster_is_player,
                            )
                        elif entity_ops_system.get_entity(level, tid) is not None:
                            if hasattr(self, "_remove_entity"):
                                self._remove_entity(level, obj, reason="destroyed_ignite")
                            else:
                                del level.entities[tid]
                            if caster_is_player:
                                name = getattr(obj, "name", None) or "object"
                                self.log.add(f"The {name} burns away.")
                except Exception:
                    pass

        state["remaining"] -= 1
        if state["remaining"] > 0:
            self._schedule(level, 1, apply_tick)
        else:
            level.ignite_state = None

    # First tick immediately
    apply_tick()


def act_regrow(self, actor_id: str) -> None:
    """Heal along green edges for 30 ticks with decaying strength."""
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    pattern = getattr(level, "pattern", None)
    if actor is None or pattern is None or not pattern.edges:
        return

    cost = 30
    try:
        if actor.stats.mana < cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana to regrow.")
            return
        actor.stats.mana -= cost
        actor.stats.clamp()
    except Exception:
        pass

    duration = 30
    base_direct = 3.5
    base_indirect = 1.5

    state = {
        "remaining": duration,
        "duration": duration,
        "accum": {},  # target_id -> fractional heal
        "direct_tiles": [],
        "indirect_tiles": [],
    }
    level.regrow_state = state
    if actor_id == self.player_id:
        self.log.add("You flood the pattern with renewal.")

    def normalize_edge_key(a: int, b: int) -> tuple[int, int]:
        return (a, b) if a <= b else (b, a)

    def edge_color_map():
        edge_colors = getattr(pattern, "edge_colors", {}) or {}
        if edge_colors:
            return edge_colors
        return {}

    color_map = edge_color_map()

    def tiles_for_edge(a_idx: int, b_idx: int) -> list[tuple[int, int]]:
        try:
            anchor = getattr(level, "pattern_anchor", None)
            verts = project_vertices(pattern, anchor)
            ax, ay = verts[a_idx]
            bx, by = verts[b_idx]
        except Exception:
            return []
        return _line_points(int(round(ax)), int(round(ay)), int(round(bx)), int(round(by)))

    def apply_tick() -> None:
        if state.get("remaining", 0) <= 0:
            level.regrow_state = None
            return
        anchor = getattr(level, "pattern_anchor", None)
        if anchor is None:
            level.regrow_state = None
            return
        mult = state["remaining"] / duration

        direct_tiles: dict[tuple[int, int], float] = {}
        for edge in pattern.edges:
            a = getattr(edge, "a", None)
            b = getattr(edge, "b", None)
            if a is None or b is None:
                continue
            col = color_map.get(normalize_edge_key(a, b), None)
            if col is None:
                if isinstance(edge.color, tuple) and len(edge.color) >= 3:
                    col = edge.color
                else:
                    continue
            try:
                r, g, bl = int(col[0]), int(col[1]), int(col[2])
            except Exception:
                continue
            greenness = max(0, g - max(r, bl))
            if greenness <= 0:
                continue
            for t in tiles_for_edge(a, b):
                prev = direct_tiles.get(t, 0.0)
                if greenness > prev:
                    direct_tiles[t] = greenness

        if not direct_tiles:
            state["remaining"] = 0
            level.regrow_state = None
            return

        indirect_tiles: dict[tuple[int, int], float] = {}
        for (dx, dy), gval in direct_tiles.items():
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    nx, ny = dx + ox, dy + oy
                    if (nx, ny) in direct_tiles:
                        continue
                    prev = indirect_tiles.get((nx, ny), 0.0)
                    if gval > prev:
                        indirect_tiles[(nx, ny)] = gval

        state["direct_tiles"] = list(direct_tiles.keys())
        state["indirect_tiles"] = list(indirect_tiles.keys())

        combined: dict[str, Any] = {}
        for act in entity_ops_system.iter_actors(level):
            combined[act.id] = act
        for ent in entity_ops_system.iter_entities(level):
            if ent.id not in combined:
                combined[ent.id] = ent

        for tid, obj in combined.items():
            tiles = _target_tiles_local(obj)
            if not tiles:
                continue
            heal_val = 0.0
            best_direct = 0.0
            best_indirect = 0.0
            for tile in tiles:
                gv = float(direct_tiles.get(tile, 0.0))
                if gv > best_direct:
                    best_direct = gv
                gv_i = float(indirect_tiles.get(tile, 0.0))
                if gv_i > best_indirect:
                    best_indirect = gv_i
            if best_direct > 0.0:
                gval = best_direct
                heal_val = base_direct * (gval / 255.0) * mult
            elif best_indirect > 0.0:
                gval = best_indirect
                heal_val = base_indirect * (gval / 255.0) * mult
            if heal_val <= 0:
                continue
            acc = state["accum"].get(tid, 0.0) + heal_val
            heal_int = int(acc)
            state["accum"][tid] = acc - heal_int
            if heal_int > 0:
                try:
                    obj.stats.hp = min(obj.stats.max_hp, obj.stats.hp + heal_int)
                except Exception:
                    pass

        state["remaining"] -= 1
        if state["remaining"] > 0:
            self._schedule(level, 1, apply_tick)
        else:
            level.regrow_state = None

    apply_tick()


def act_freeze(self, actor_id: str) -> None:
    """Deal damage and apply slowing based on pattern blueness on touched tiles."""
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if actor is None or pattern is None or anchor is None or not pattern.vertices:
        return

    caster_is_player = actor_id == self.player_id

    # High mana cost gate (no cooldown)
    cost = 35
    try:
        if actor.stats.mana < cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana to freeze.")
            return
        actor.stats.mana -= cost
        actor.stats.clamp()
    except Exception:
        pass

    dmg_scale = getattr(self, "get_param_value", lambda a, k: 0.1)("freeze", "damage_scale") or 0.1
    slow_scale = getattr(self, "get_param_value", lambda a, k: 0.04)("freeze", "slow_scale") or 0.04

    verts_world = project_vertices(pattern, anchor)
    vcolors = getattr(pattern, "vertex_colors", None) or []

    def blueness(idx: int) -> float:
        try:
            col = vcolors[idx]
        except Exception:
            col = None
        if not col or len(col) < 3:
            return 0.0
        r, g, b = col[0], col[1], col[2]
        return max(0.0, float(b) - max(float(r), float(g)))

    tile_blue: Dict[Tuple[int, int], float] = {}
    for i, (vx, vy) in enumerate(verts_world):
        tx = int(round(vx))
        ty = int(round(vy))
        blue = blueness(i)
        tile_blue[(tx, ty)] = tile_blue.get((tx, ty), 0.0) + blue

    if actor_id == self.player_id:
        self.log.add("You unleash a freezing wave through the pattern.")

    # Freeze currently affects all actors on touched tiles (including self),
    # but not environment entities.
    policy = damage_policy_system.DamagePolicy(
        include_self=True,
        include_hostile=True,
        include_neutral=True,
        include_friendly=True,
        include_environment=False,
    )
    freeze_targets = list(
        damage_policy_system.iter_damage_targets(
            self,
            level,
            actor_id,
            policy,
            include_actors=True,
            include_entities=False,
        )
    )

    for (tx, ty), bsum in tile_blue.items():
        if bsum <= 0:
            continue
        dmg = bsum * float(dmg_scale)
        slow_mult = 1.0 + bsum * float(slow_scale)
        if slow_mult > 4.0:
            slow_mult = 4.0
        for _tid, target in freeze_targets:
            if not getattr(target, "alive", True):
                continue
            if tuple(getattr(target, "pos", (None, None))) != (tx, ty):
                continue
            if dmg > 0:
                dmg_int = int(max(0, dmg))
                if dmg_int > 0:
                    target.stats.hp -= dmg_int
                    target.stats.clamp()
                    if target.id == self.player_id:
                        self.log.add(f"The freeze bites you for {dmg_int} damage.")
                        if target.stats.hp <= 0:
                            self.set_urgent("by way of freezing", title="You unravel...", choices=["Continue..."])
                    else:
                        self.log.add(f"{target.name} is frozen for {dmg_int} damage.")
                        if target.stats.hp <= 0:
                            self._kill_actor(
                                level,
                                target,
                                killer_id=actor_id,
                                killer_is_player=caster_is_player,
                            )
            tags = getattr(target, "tags", {}) or {}
            current = float(tags.get("frozen_slow", 1.0))
            if slow_mult > current:
                tags["frozen_slow"] = slow_mult
                tags["frozen_slow_timer"] = 0.0
                target.tags = tags

def act_energy_kick(self, actor_id: str) -> None:
    """Pulse damage around all foot-lineage chakra vertices in the current pattern.

    Targeting rules (current implementation):
    - Never damages the caster.
    - Can damage any other actor or entity that has HP.
    - Non-actor entities with HP are removed when reduced to <= 0.
    """
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
        if actor_id == self.player_id:
            self.log.add("No pattern to channel through your feet.")
        return

    # Mana gate for a strong short-range pulse.
    mana_cost = 24
    try:
        if actor.stats.mana < mana_cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana for Energy Kick.")
            return
        actor.stats.mana -= mana_cost
        actor.stats.clamp()
    except Exception:
        return

    def _is_foot_lineage(node_id: str) -> bool:
        n = str(node_id or "").lower()
        # Match broad lower-body endpoints while staying schema-agnostic.
        return (
            "foot" in n
            or "toe" in n
            or "ankle" in n
            or "heel" in n
        )

    # Build world-space kick points from vertex-level chakra provenance.
    kick_sources = _chakra_world_sources(self, pattern, anchor, predicate=_is_foot_lineage)
    kick_points = [p for (p, _nodes) in kick_sources]

    if not kick_points:
        if actor_id == self.player_id:
            self.log.add("No active foot chakras are encoded in this pattern.")
        return

    # Show a short pulse overlay at each kick source.
    level.activation_points = list(kick_points)
    level.activation_ttl = max(8, int(getattr(self.cfg, "pattern_overlay_ttl", 12)))

    radius = 1.8
    # Slight rebound after sweep; still below the old spike potential.
    base_damage = 6  # per kick point at distance 0
    r_eps = 1e-6

    caster_is_player = actor_id == self.player_id
    # Centralized policy: this move is intentionally "reckless" and can hit
    # almost anything with HP except the caster (hostiles, friendlies, neutrals,
    # and HP-bearing environment entities).
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=True,
        include_friendly=True,
        include_environment=True,
    )

    hit_any = False
    for tid, obj in damage_policy_system.iter_damage_targets(
        self,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=True,
    ):
        stats = getattr(obj, "stats", None)
        if stats is None or not hasattr(stats, "hp"):
            continue
        probes = _target_probe_points_local(obj)
        if not probes:
            continue

        total_dmg = 0
        for (kx, ky), source_nodes in kick_sources:
            dist = min(math.hypot(px - kx, py - ky) for (px, py) in probes)
            if dist > radius:
                continue
            falloff = max(0.0, 1.0 - (dist / max(radius, r_eps)))
            base = max(1, int(math.ceil(base_damage * falloff)))
            total_dmg += chakra_items_system.apply_damage_modifiers(
                self,
                actor_id,
                "energy_kick",
                base,
                source_nodes=source_nodes,
                illuminated_nodes=source_nodes,
            )

        if total_dmg <= 0:
            continue

        hit_any = True
        try:
            stats.hp -= total_dmg
            if hasattr(stats, "clamp"):
                stats.clamp()
        except Exception:
            continue

        if caster_is_player:
            name = getattr(obj, "name", None) or "something"
            self.log.add(f"Energy Kick shudders {name} for {total_dmg}.")

        # Actors use canonical death handling.
        if entity_ops_system.get_actor(level, tid) is not None:
            if int(getattr(stats, "hp", 0)) <= 0:
                self._kill_actor(
                    level,
                    obj,
                    killer_id=actor_id,
                    killer_is_player=caster_is_player,
                )
            continue

        # Non-actor entities with HP are removed when broken.
        if int(getattr(stats, "hp", 0)) <= 0 and entity_ops_system.get_entity(level, tid) is not None:
            if hasattr(self, "_remove_entity"):
                self._remove_entity(level, obj, reason="destroyed_energy_kick")
            else:
                del level.entities[tid]
            if caster_is_player:
                name = getattr(obj, "name", None) or "object"
                self.log.add(f"The {name} shatters.")

    if caster_is_player and not hit_any:
        self.log.add("Your kick ripples out, but hits nothing.")

def _chakra_nodes_for_vertex(self, v: Any) -> set[str]:
    """Return chakra node ids associated with a pattern vertex.

    Chakra provenance can arrive either as:
    - tags["chakra_node"] = "node_id"
    - tags["chakra_nodes"] = "a|b|c"
    """
    tags = getattr(v, "tags", {}) or {}
    nodes: set[str] = set()

    single = str(tags.get("chakra_node", "")).strip()
    if single:
        nodes.add(single)

    many = str(tags.get("chakra_nodes", "")).strip()
    if many:
        for part in many.split("|"):
            p = str(part).strip()
            if p:
                nodes.add(p)

    return nodes

def _chakra_world_points(
    self,
    pattern: Any,
    anchor: Tuple[int, int],
    *,
    predicate: Callable[[str], bool],
) -> list[tuple[float, float]]:
    """Collect world-space points for vertices matching chakra-node predicate."""
    return [p for (p, _nodes) in _chakra_world_sources(self, pattern, anchor, predicate=predicate)]


def _chakra_world_sources(
    self,
    pattern: Any,
    anchor: Tuple[int, int],
    *,
    predicate: Callable[[str], bool],
) -> list[tuple[tuple[float, float], set[str]]]:
    """Collect world-space points with matching source-node provenance."""
    out: list[tuple[tuple[float, float], set[str]]] = []
    for v in getattr(pattern, "vertices", []) or []:
        nodes = self._chakra_nodes_for_vertex(v)
        if not nodes:
            continue
        matched = {node_id for node_id in nodes if predicate(node_id)}
        if not matched:
            continue
        out.append(((float(v.pos[0] + anchor[0]), float(v.pos[1] + anchor[1])), matched))
    return out

def act_palm_burst(self, actor_id: str) -> None:
    """Pulse damage from hand/palm/finger-lineage chakra vertices."""
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
        if actor_id == self.player_id:
            self.log.add("No pattern to channel through your hands.")
        return

    mana_cost = 18
    try:
        if actor.stats.mana < mana_cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana for Palm Burst.")
            return
        actor.stats.mana -= mana_cost
        actor.stats.clamp()
    except Exception:
        return

    def _is_palm_lineage(node_id: str) -> bool:
        n = str(node_id or "").lower()
        return (
            "hand" in n
            or "palm" in n
            or "finger" in n
            or "thumb" in n
            or "index" in n
            or "middle" in n
            or "ring" in n
            or "pinky" in n
            or "knuckle" in n
            or "wrist" in n
        )

    burst_sources = _chakra_world_sources(self, pattern, anchor, predicate=_is_palm_lineage)
    burst_points = [p for (p, _nodes) in burst_sources]
    if not burst_points:
        if actor_id == self.player_id:
            self.log.add("No active palm chakras are encoded in this pattern.")
        return

    level.activation_points = list(burst_points)
    level.activation_ttl = max(8, int(getattr(self.cfg, "pattern_overlay_ttl", 12)))

    radius = 1.6
    # Slight rebound after sweep; still in the same band as Energy Kick.
    base_damage = 6
    r_eps = 1e-6
    caster_is_player = actor_id == self.player_id

    # Palm Burst is a directed combat move: hostile actors only.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )

    hit_any = False
    for tid, obj in damage_policy_system.iter_damage_targets(
        self,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        stats = getattr(obj, "stats", None)
        if stats is None or not hasattr(stats, "hp"):
            continue
        probes = _target_probe_points_local(obj)
        if not probes:
            continue

        total_dmg = 0
        for (px, py), source_nodes in burst_sources:
            dist = min(math.hypot(tx - px, ty - py) for (tx, ty) in probes)
            if dist > radius:
                continue
            falloff = max(0.0, 1.0 - (dist / max(radius, r_eps)))
            base = max(1, int(math.ceil(base_damage * falloff)))
            total_dmg += chakra_items_system.apply_damage_modifiers(
                self,
                actor_id,
                "palm_burst",
                base,
                source_nodes=source_nodes,
                illuminated_nodes=source_nodes,
            )

        if total_dmg <= 0:
            continue

        hit_any = True
        try:
            stats.hp -= total_dmg
            if hasattr(stats, "clamp"):
                stats.clamp()
        except Exception:
            continue

        if caster_is_player:
            name = getattr(obj, "name", None) or "something"
            self.log.add(f"Palm Burst cracks {name} for {total_dmg}.")

        if entity_ops_system.get_actor(level, tid) is not None and int(getattr(stats, "hp", 0)) <= 0:
            self._kill_actor(
                level,
                obj,
                killer_id=actor_id,
                killer_is_player=caster_is_player,
            )

    if caster_is_player and not hit_any:
        self.log.add("Your palms flare, but no foes are close enough.")

def act_mirror_strike(self, actor_id: str) -> None:
    """Pulse damage from mirrored chakra pairs.

    A mirrored pair is inferred from provenance ids:
    - left:  "arm.hand.thumb"
    - right: "arm_m.hand_m.thumb_m"
    i.e., node ids differing by the `_m` suffix.
    """
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None or not getattr(pattern, "vertices", None):
        if actor_id == self.player_id:
            self.log.add("No pattern to channel through mirrored pairs.")
        return

    mana_cost = 22
    try:
        if actor.stats.mana < mana_cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana for Mirror Strike.")
            return
        actor.stats.mana -= mana_cost
        actor.stats.clamp()
    except Exception:
        return

    # Build node -> world points map from vertex provenance.
    node_points: dict[str, list[tuple[float, float]]] = {}
    for v in getattr(pattern, "vertices", []) or []:
        nodes = self._chakra_nodes_for_vertex(v)
        if not nodes:
            continue
        wx = float(v.pos[0] + anchor[0])
        wy = float(v.pos[1] + anchor[1])
        for node_id in nodes:
            node_points.setdefault(node_id, []).append((wx, wy))

    if not node_points:
        if actor_id == self.player_id:
            self.log.add("No chakra provenance found for Mirror Strike.")
        return

    # Derive strike points from present mirrored pairs.
    strike_entries: list[tuple[tuple[float, float], set[str]]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for node_id, left_pts in node_points.items():
        if not left_pts:
            continue
        if node_id.endswith("_m"):
            base = node_id[:-2]
            mirror = node_id
        else:
            base = node_id
            mirror = f"{node_id}_m"
        right_pts = node_points.get(mirror, [])
        if not right_pts:
            continue

        key = tuple(sorted((base, mirror)))
        if key in seen_pairs:
            continue
        seen_pairs.add(key)

        # Use centroid of each side and strike at the midpoint between them.
        lx = sum(p[0] for p in left_pts) / len(left_pts)
        ly = sum(p[1] for p in left_pts) / len(left_pts)
        rx = sum(p[0] for p in right_pts) / len(right_pts)
        ry = sum(p[1] for p in right_pts) / len(right_pts)
        strike_entries.append((((lx + rx) * 0.5, (ly + ry) * 0.5), {base, mirror}))

    strike_points = [p for (p, _nodes) in strike_entries]

    if not strike_points:
        if actor_id == self.player_id:
            self.log.add("No mirrored chakra pairs are active in this pattern.")
        return

    level.activation_points = list(strike_points)
    level.activation_ttl = max(10, int(getattr(self.cfg, "pattern_overlay_ttl", 12)))

    radius = 1.85
    # Mirror Strike remains a premium burst, but no longer far above peers.
    base_damage = 6
    r_eps = 1e-6
    caster_is_player = actor_id == self.player_id

    # Mirror Strike is precise: hostile actors only.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )

    hit_any = False
    for tid, obj in damage_policy_system.iter_damage_targets(
        self,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        stats = getattr(obj, "stats", None)
        if stats is None or not hasattr(stats, "hp"):
            continue
        probes = _target_probe_points_local(obj)
        if not probes:
            continue

        total_dmg = 0
        for (sx, sy), source_nodes in strike_entries:
            dist = min(math.hypot(px - sx, py - sy) for (px, py) in probes)
            if dist > radius:
                continue
            falloff = max(0.0, 1.0 - (dist / max(radius, r_eps)))
            base = max(1, int(math.ceil(base_damage * falloff)))
            total_dmg += chakra_items_system.apply_damage_modifiers(
                self,
                actor_id,
                "mirror_strike",
                base,
                source_nodes=source_nodes,
                illuminated_nodes=source_nodes,
            )

        if total_dmg <= 0:
            continue

        hit_any = True
        try:
            stats.hp -= total_dmg
            if hasattr(stats, "clamp"):
                stats.clamp()
        except Exception:
            continue

        if caster_is_player:
            name = getattr(obj, "name", None) or "something"
            self.log.add(f"Mirror Strike rends {name} for {total_dmg}.")

        if entity_ops_system.get_actor(level, tid) is not None and int(getattr(stats, "hp", 0)) <= 0:
            self._kill_actor(
                level,
                obj,
                killer_id=actor_id,
                killer_is_player=caster_is_player,
            )

    if caster_is_player and not hit_any:
        self.log.add("Your mirrored strike finds no target.")

def act_aggressive_vines(self, actor_id: str) -> None:
    """Seed a free-form tendril field that grows from rune-edge centers.

    Behavior model:
    - Initial tendril tips spawn from centers of pattern edges that are near
      hostile targets.
    - Each tick, tips extend toward nearby hostiles.
    - Existing tips can branch into new tips, producing a creeping vine net.
    - Contact applies light damage plus a mild ensnare slow.

    Runtime simulation is advanced by scheduling.choking_vines_tick().
    """
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return

    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None or not getattr(pattern, "edges", None):
        if actor_id == self.player_id:
            self.log.add("No rune edges available for Aggressive Vines.")
        return

    # Cost gate. This is intentionally expensive for a persistent control tool.
    mana_cost = 28
    try:
        if actor.stats.mana < mana_cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana for Aggressive Vines.")
            return
        actor.stats.mana -= mana_cost
        actor.stats.clamp()
    except Exception:
        return

    # Project edge midpoints in ABS tile-space so the simulation stays stable
    # even if the current zone view changes.
    verts_world = project_vertices(pattern, anchor)
    if not verts_world:
        if actor_id == self.player_id:
            self.log.add("No rune geometry to grow aggressive vines from.")
        return

    zx, zy, _ = getattr(level, "coord", self.zone_coord)
    zw, zh = self._zone_dims()
    abs_ox = float(zx * zw)
    abs_oy = float(zy * zh)

    edge_midpoints_abs: list[tuple[float, float]] = []
    for e in getattr(pattern, "edges", []) or []:
        try:
            ax, ay = verts_world[int(e.a)]
            bx, by = verts_world[int(e.b)]
        except Exception:
            continue
        edge_midpoints_abs.append((
            (float(ax) + float(bx)) * 0.5 + abs_ox,
            (float(ay) + float(by)) * 0.5 + abs_oy,
        ))

    if not edge_midpoints_abs:
        if actor_id == self.player_id:
            self.log.add("No rune edges available for Aggressive Vines.")
        return

    # Hostile actor targets only.
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    hostile_abs: list[tuple[str, Actor, float, float]] = []
    for tid, obj in damage_policy_system.iter_damage_targets(
        self,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        center = _target_probe_centroid_local(obj)
        if center is None:
            continue
        cx, cy = center
        hostile_abs.append((
            str(tid),
            obj,
            float(cx) + abs_ox,
            float(cy) + abs_oy,
        ))

    if not hostile_abs:
        if actor_id == self.player_id:
            self.log.add("No nearby hostile minds for the vines to seek.")
        return

    # Choose initial tips: nearest edge center to each hostile, with dedupe.
    # Keep initial tendril seeding close to the rune so the effect reads as
    # a local control zone instead of a map-wide projectile.
    seed_radius = 2.6
    tip_keys: set[tuple[int, int]] = set()
    tips: list[dict[str, float]] = []
    for _tid, _obj, hx, hy in hostile_abs:
        best = None
        best_d2 = 1e18
        for mx, my in edge_midpoints_abs:
            dx = hx - mx
            dy = hy - my
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best = (mx, my)
        if best is None:
            continue
        if math.sqrt(best_d2) > seed_radius:
            continue
        key = (int(round(best[0] * 10.0)), int(round(best[1] * 10.0)))
        if key in tip_keys:
            continue
        tip_keys.add(key)
        tips.append(
            {
                "x": float(best[0]),
                "y": float(best[1]),
                "age": 0.0,
                # Per-tip local origin used by scheduler leash logic.
                "ox": float(best[0]),
                "oy": float(best[1]),
            }
        )

    # Fallback: ensure at least one tip exists by choosing the globally nearest
    # hostile/edge pair even if it exceeds seed_radius.
    if not tips:
        best = None
        best_d2 = 1e18
        for _tid, _obj, hx, hy in hostile_abs:
            for mx, my in edge_midpoints_abs:
                dx = hx - mx
                dy = hy - my
                d2 = dx * dx + dy * dy
                if d2 < best_d2:
                    best_d2 = d2
                    best = (mx, my)
        if best is not None:
            tips.append(
                {
                    "x": float(best[0]),
                    "y": float(best[1]),
                    "age": 0.0,
                    "ox": float(best[0]),
                    "oy": float(best[1]),
                }
            )

    if not tips:
        if actor_id == self.player_id:
            self.log.add("The vines fail to find purchase.")
        return

    # Slightly longer duration, but much lower spread parameters below.
    duration_ticks = 120
    state: dict[str, Any] = {
        "caster_id": str(actor_id),
        "duration": int(duration_ticks),
        "remaining": int(duration_ticks),
        "tick": 0,
        "seed": int(self.rng.randint(0, 2**31 - 1)),
        "edge_midpoints_abs": edge_midpoints_abs,
        "tips": tips,
        "segments": [],  # (x0, y0, x1, y1) in ABS tile coords
        "accum_damage": {},  # target id -> fractional damage
        "spawn_radius": float(seed_radius),
        # Lower growth step makes per-turn movement subtle.
        "grow_step": 0.30,
        # Fewer branches to avoid explosive map coverage.
        "branch_chance": 0.12,
        "max_tips": 10,
        "max_segments": 360,
        # Ignore far-away hostiles; vines stay near the rune.
        "seek_radius": 7.0,
        # Hard leash from each tip's seed origin.
        "max_tip_range": 6.5,
        "hit_radius": 1.15,
        "base_damage": 1.6,
        "ensnare_slow_mult": 1.30,
    }
    level.choking_vines_state = state

    # Optional compatibility overlay: localize current tip positions.
    level.activation_points = [
        (float(t["x"] - abs_ox), float(t["y"] - abs_oy))
        for t in tips
    ]
    level.activation_ttl = max(level.activation_ttl, 8)

    if actor_id == self.player_id:
            self.log.add("Aggressive vines uncoil from your rune and seek warm blood.")

    # Persist to canonical per-depth pattern state.
    if hasattr(self, "_commit_pattern_state_from_level"):
        self._commit_pattern_state_from_level(level)


def _safe_norm(dx: float, dy: float) -> tuple[float, float]:
    mag = math.hypot(dx, dy)
    if mag <= 1e-9:
        return (0.0, 0.0)
    return (dx / mag, dy / mag)


def _clamp_turn(prev_dir: tuple[float, float], desired_dir: tuple[float, float], max_deg: float) -> tuple[float, float]:
    """Limit angular turn from prev_dir toward desired_dir by max_deg."""
    px, py = _safe_norm(prev_dir[0], prev_dir[1])
    dx, dy = _safe_norm(desired_dir[0], desired_dir[1])
    if (px == 0.0 and py == 0.0) or (dx == 0.0 and dy == 0.0):
        return (dx, dy)

    a_prev = math.atan2(py, px)
    a_des = math.atan2(dy, dx)
    delta = (a_des - a_prev + math.pi) % (2.0 * math.pi) - math.pi
    limit = math.radians(max(0.0, float(max_deg)))
    if abs(delta) <= limit:
        return (dx, dy)
    a_new = a_prev + (limit if delta > 0.0 else -limit)
    return (math.cos(a_new), math.sin(a_new))


def _distance_point_to_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    """Return Euclidean distance from point P to segment AB."""
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    denom = abx * abx + aby * aby
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / denom))
    qx = ax + abx * t
    qy = ay + aby * t
    return math.hypot(px - qx, py - qy)


def _vertex_chakra_nodes(v: Any) -> set[str]:
    tags = getattr(v, "tags", {}) or {}
    out: set[str] = set()
    one = str(tags.get("chakra_node", "")).strip()
    if one:
        out.add(one)
    many = str(tags.get("chakra_nodes", "")).strip()
    if many:
        out.update({p for p in many.split("|") if p})
    return out


def _split_pattern_edge_at_midpoint(pattern: Any, edge_idx: int) -> Optional[tuple[int, tuple[float, float], tuple[float, float]]]:
    """Split edge `edge_idx` into two edges via a newly inserted midpoint vertex."""
    try:
        e = pattern.edges[edge_idx]
        a_idx = int(getattr(e, "a"))
        b_idx = int(getattr(e, "b"))
        va = pattern.vertices[a_idx]
        vb = pattern.vertices[b_idx]
        ax, ay = float(va.pos[0]), float(va.pos[1])
        bx, by = float(vb.pos[0]), float(vb.pos[1])
    except Exception:
        return None

    if math.hypot(bx - ax, by - ay) <= 1e-6:
        return None

    mx = (ax + bx) * 0.5
    my = (ay + by) * 0.5

    node_ids: set[str] = set()
    node_ids.update(_vertex_chakra_nodes(va))
    node_ids.update(_vertex_chakra_nodes(vb))
    tags: dict[str, str] = {}
    if node_ids:
        ordered = sorted(node_ids)
        tags["chakra_node"] = ordered[0]
        if len(ordered) > 1:
            tags["chakra_nodes"] = "|".join(ordered)

    new_idx = pattern.add_vertex((mx, my), color=getattr(e, "color", "neutral"), tags=tags)
    weight = float(getattr(e, "weight", 1.0) or 1.0)
    color = getattr(e, "color", "neutral")

    # Replace original edge with two new edges through midpoint.
    pattern.edges.pop(edge_idx)
    pattern.add_edge(a_idx, new_idx, color=color, weight=weight)
    pattern.add_edge(new_idx, b_idx, color=color, weight=weight)
    return (new_idx, (ax, ay), (bx, by))


def act_choking_vines(self, actor_id: str) -> None:
    """Grow constricting rune branches from rune edges toward hostiles.

    Unlike Aggressive Vines (free-form overlay), this variant mutates the
    actual rune graph by inserting new vertices/edges over time.
    """
    level = self._level()
    actor = entity_ops_system.get_actor(level, actor_id)
    if actor is None:
        return
    _dbg = getattr(self, "_debug", None)
    def _d(msg: str) -> None:
        try:
            if _dbg is not None:
                _dbg(f"[rune_vines] {msg}")
        except Exception:
            pass

    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None or not getattr(pattern, "edges", None):
        _d("cast denied: no pattern/anchor/edges")
        if actor_id == self.player_id:
            self.log.add("No rune edges available for Choking Vines.")
        return

    mana_cost = 26
    try:
        if actor.stats.mana < mana_cost:
            _d(f"cast denied: mana {int(getattr(actor.stats, 'mana', 0))}/{mana_cost}")
            if actor_id == self.player_id:
                self.log.add("Not enough mana for Choking Vines.")
            return
        actor.stats.mana -= mana_cost
        actor.stats.clamp()
    except Exception:
        _d("cast denied: actor stats unavailable")
        return

    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    # Hostiles are tracked in both world-space (for logs/FX) and local
    # rune-space (for geometric growth decisions against pattern vertices).
    hostile_local: list[tuple[str, Actor, float, float, float, float]] = []
    for tid, obj in damage_policy_system.iter_damage_targets(
        self,
        level,
        actor_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        center = _target_probe_centroid_local(obj)
        if center is None:
            continue
        wx, wy = center
        lx = wx - float(anchor[0])
        ly = wy - float(anchor[1])
        hostile_local.append((str(tid), obj, wx, wy, lx, ly))
    _d(
        f"cast begin: hostiles={len(hostile_local)} edges={len(getattr(pattern, 'edges', []) or [])} "
        f"verts={len(getattr(pattern, 'vertices', []) or [])} actor={actor_id}"
    )

    if not hostile_local:
        _d("cast denied: no hostile targets")
        if actor_id == self.player_id:
            self.log.add("No nearby hostile minds for the vines to seek.")
        return

    seed_radius = 6.0
    used_edge_keys: set[tuple[int, int]] = set()
    tips: list[dict[str, Any]] = []
    # First visible growth step created immediately at cast time.
    initial_max_step = 4.0
    initial_nominal_step = 1.8
    # Relaxed from 25deg to 40deg per design request.
    angle_limit_deg = 25.0

    def _nearest_edge_for_target(tx: float, ty: float) -> tuple[int, float, tuple[int, int]] | None:
        best_idx = -1
        best_d2 = 1e18
        best_key = (0, 0)
        for idx, e in enumerate(getattr(pattern, "edges", []) or []):
            try:
                a_idx = int(getattr(e, "a"))
                b_idx = int(getattr(e, "b"))
                va = pattern.vertices[a_idx]
                vb = pattern.vertices[b_idx]
                ax, ay = float(va.pos[0]), float(va.pos[1])
                bx, by = float(vb.pos[0]), float(vb.pos[1])
            except Exception:
                continue
            key = (min(a_idx, b_idx), max(a_idx, b_idx))
            if key in used_edge_keys:
                continue
            mx = (ax + bx) * 0.5
            my = (ay + by) * 0.5
            dx = tx - mx
            dy = ty - my
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_idx = idx
                best_key = key
        if best_idx < 0:
            return None
        return (best_idx, best_d2, best_key)

    def _seed_tip_from_split(
        *,
        mid_idx: int,
        ax: float,
        ay: float,
        bx: float,
        by: float,
        target_x: float,
        target_y: float,
    ) -> dict[str, Any]:
        """
        Create one immediate branch segment from a newly split midpoint.

        This guarantees that Choking Vines visibly branches on cast, instead
        of only inserting split vertices and waiting for later tick growth.
        """
        mx, my = map(float, pattern.vertices[mid_idx].pos)
        edge_dir = _safe_norm(float(bx) - float(ax), float(by) - float(ay))
        to_target = _safe_norm(float(target_x) - mx, float(target_y) - my)
        # Keep forward orientation of edge_dir toward target.
        if edge_dir[0] * to_target[0] + edge_dir[1] * to_target[1] < 0.0:
            edge_dir = (-edge_dir[0], -edge_dir[1])
        desired = to_target if to_target != (0.0, 0.0) else edge_dir
        if desired == (0.0, 0.0):
            desired = (1.0, 0.0)
        grow_dir = _clamp_turn(edge_dir, desired, angle_limit_deg)
        if grow_dir == (0.0, 0.0):
            grow_dir = desired

        dist = math.hypot(float(target_x) - mx, float(target_y) - my)
        step = min(initial_max_step, max(1.0, min(initial_nominal_step, dist)))
        nx = mx + grow_dir[0] * step
        ny = my + grow_dir[1] * step

        src_tags = dict(getattr(pattern.vertices[mid_idx], "tags", {}) or {})
        src_tags["rune_vine"] = "1"
        head_idx = pattern.add_vertex((nx, ny), color="verdant", tags=src_tags)
        pattern.add_edge(int(mid_idx), int(head_idx), color="verdant", weight=1.0)
        return {
            "vertex": int(head_idx),
            "dx": float(grow_dir[0]),
            "dy": float(grow_dir[1]),
            "age": 0.0,
        }

    for _tid, _obj, wx, wy, hx, hy in hostile_local:
        found = _nearest_edge_for_target(hx, hy)
        if found is None:
            _d(f"seed skip: no edge for hostile@w({wx:.2f},{wy:.2f}) l({hx:.2f},{hy:.2f})")
            continue
        edge_idx, d2, edge_key = found
        if math.sqrt(d2) > seed_radius:
            _d(
                f"seed skip: hostile@w({wx:.2f},{wy:.2f}) l({hx:.2f},{hy:.2f}) nearest_edge={edge_idx} "
                f"dist={math.sqrt(d2):.2f} > seed_radius={seed_radius:.2f}"
            )
            continue
        split = _split_pattern_edge_at_midpoint(pattern, edge_idx)
        if split is None:
            _d(f"seed skip: split failed edge_idx={edge_idx} key={edge_key}")
            continue
        mid_idx, (ax, ay), (bx, by) = split
        used_edge_keys.add(edge_key)
        _d(
            f"seed add: edge_idx={edge_idx} key={edge_key} mid={mid_idx} "
            f"hostile@w({wx:.2f},{wy:.2f}) l({hx:.2f},{hy:.2f}) edge=(({ax:.2f},{ay:.2f})->({bx:.2f},{by:.2f}))"
        )
        tips.append(
            _seed_tip_from_split(
                mid_idx=int(mid_idx),
                ax=float(ax),
                ay=float(ay),
                bx=float(bx),
                by=float(by),
                target_x=float(hx),
                target_y=float(hy),
            )
        )
        if len(tips) >= 6:
            break

    # Fallback: split the globally-nearest edge once so the ability always starts.
    if not tips:
        best = None
        best_d2 = 1e18
        best_h = None
        for _tid, _obj, _wx, _wy, hx, hy in hostile_local:
            found = _nearest_edge_for_target(hx, hy)
            if found is None:
                continue
            idx, d2, key = found
            if d2 < best_d2:
                best = (idx, key)
                best_d2 = d2
                best_h = (hx, hy)
        if best is not None and best_h is not None:
            split = _split_pattern_edge_at_midpoint(pattern, best[0])
            if split is not None:
                mid_idx, (ax, ay), (bx, by) = split
                _d(
                    f"seed fallback: edge_idx={best[0]} mid={mid_idx} "
                    f"hostile@({best_h[0]:.2f},{best_h[1]:.2f})"
                )
                tips.append(
                    _seed_tip_from_split(
                        mid_idx=int(mid_idx),
                        ax=float(ax),
                        ay=float(ay),
                        bx=float(bx),
                        by=float(by),
                        target_x=float(best_h[0]),
                        target_y=float(best_h[1]),
                    )
                )

    if not tips:
        _d("cast denied: no tips could be seeded")
        if actor_id == self.player_id:
            self.log.add("The vines fail to find purchase.")
        return

    level.rune_choking_vines_state = {
        "caster_id": str(actor_id),
        "duration": 60,
        "remaining": 60,
        "tick": 0,
        "tips": tips,
        "dot_targets": {},
        "seed_radius": float(seed_radius),
        "seek_radius": 5,
        "grow_step": 1.15,
        "max_step": 2.0,
        "angle_limit_deg": angle_limit_deg,
        "branch_chance": 0.06,
        "max_tips": 4,
        # Grow on a slower cadence so vines feel like creeping tendrils.
        # `grow_every=5` means one growth pass every 5 heartbeats.
        "grow_every": 15,
        # Limit how many existing tip heads can advance per growth pass.
        "growth_budget": 1,
        # Limit how many fresh edge reseeds can spawn per growth pass.
        "reseed_per_tick": 1,
        # Disable the temporary aggressive early-growth booster used for diagnostics.
        "min_growth_heartbeats": 0,
        "hit_radius": 1.0,
        "hit_damage": 1,
        "dot_damage": 1,
        "dot_duration": 2,
        "root_duration": 2,
        # Leave verbose diagnostics off in normal play.
        "debug_verbose": False,
    }
    _d(
        f"cast state created: tips={len(tips)} remaining={level.rune_choking_vines_state.get('remaining')} "
        f"growth_budget={level.rune_choking_vines_state.get('growth_budget')} "
        f"reseed_per_tick={level.rune_choking_vines_state.get('reseed_per_tick')}"
    )

    # Brief activation hint at seed vertices.
    level.activation_points = [
        tuple(pattern.vertices[int(t["vertex"])].pos)
        for t in tips
        if 0 <= int(t.get("vertex", -1)) < len(pattern.vertices)
    ]
    level.activation_ttl = max(int(getattr(level, "activation_ttl", 0) or 0), 8)

    if actor_id == self.player_id:
        self.log.add("Constricting vines split from your rune and begin to creep.")

    if hasattr(self, "_commit_pattern_state_from_level"):
        self._commit_pattern_state_from_level(level)


def rune_choking_vines_tick(game: "Game", level: Any, delta: int) -> None:
    """Advance rune-mutating Choking Vines for `delta` heartbeats."""
    if delta <= 0:
        return
    state = getattr(level, "rune_choking_vines_state", None)
    if not state:
        return
    _dbg = getattr(game, "_debug", None)
    def _d(msg: str) -> None:
        try:
            if _dbg is not None:
                _dbg(f"[rune_vines] {msg}")
        except Exception:
            pass

    try:
        remaining = int(state.get("remaining", 0))
    except Exception:
        _d("tick abort: invalid remaining -> clearing state")
        level.rune_choking_vines_state = None
        return
    if bool(state.get("debug_verbose", False)):
        _d(
            f"tick start: delta={int(delta)} remaining={remaining} "
            f"tips={len(list(state.get('tips', []) or []))} "
            f"verts={len(getattr(getattr(level, 'pattern', None), 'vertices', []) or [])} "
            f"edges={len(getattr(getattr(level, 'pattern', None), 'edges', []) or [])}"
        )

    for _ in range(int(delta)):
        if remaining <= 0:
            break
        _step_rune_choking_vines(game, level, state)
        remaining -= 1
        state["remaining"] = remaining

    if remaining <= 0:
        _d("tick complete: duration expired -> clearing state")
        level.rune_choking_vines_state = None

    # Keep activation overlay tied to current vine tip vertices.
    try:
        pattern = getattr(level, "pattern", None)
        tips = list(state.get("tips", []) or [])
        if pattern is not None:
            level.activation_points = [
                tuple(pattern.vertices[int(t["vertex"])].pos)
                for t in tips
                if 0 <= int(t.get("vertex", -1)) < len(pattern.vertices)
            ]
            if level.activation_points:
                level.activation_ttl = max(int(getattr(level, "activation_ttl", 0) or 0), 3)
    except Exception:
        pass

    try:
        game._commit_pattern_state_from_level(level)
    except Exception:
        pass


def _step_rune_choking_vines(game: "Game", level: Any, state: dict[str, Any]) -> None:
    """Single-step growth pass for rune-mutating Choking Vines."""
    pattern = getattr(level, "pattern", None)
    if pattern is None or not getattr(pattern, "vertices", None):
        if bool(state.get("debug_verbose", False)):
            try:
                game._debug("[rune_vines] step abort: missing pattern/vertices")
            except Exception:
                pass
        return

    caster_id = str(state.get("caster_id", getattr(game, "player_id", "")))
    tick = int(state.get("tick", 0))
    state["tick"] = tick + 1
    _debug_verbose = bool(state.get("debug_verbose", False))
    if _debug_verbose:
        try:
            game._debug(
                f"[rune_vines] step tick={tick} tips={len(list(state.get('tips', []) or []))} "
                f"verts={len(getattr(pattern, 'vertices', []) or [])} "
                f"edges={len(getattr(pattern, 'edges', []) or [])}"
            )
        except Exception:
            pass

    # Hostile actor targets only (no friendlies/environment for this control tool).
    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    anchor = getattr(level, "pattern_anchor", None)
    if anchor is None:
        state["dot_targets"] = {}
        return

    # Hostiles tracked as:
    # (id, actor, world_x, world_y, local_x, local_y)
    hostiles: list[tuple[str, Actor, float, float, float, float]] = []
    for tid, obj in damage_policy_system.iter_damage_targets(
        game,
        level,
        caster_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        center = _target_probe_centroid_local(obj)
        if center is None:
            continue
        wx, wy = center
        lx = wx - float(anchor[0])
        ly = wy - float(anchor[1])
        hostiles.append((str(tid), obj, wx, wy, lx, ly))
    if _debug_verbose:
        try:
            game._debug(f"[rune_vines] step hostiles={len(hostiles)}")
        except Exception:
            pass

    dot_targets: dict[str, int] = {
        str(k): int(v) for (k, v) in dict(state.get("dot_targets", {}) or {}).items()
    }
    dot_damage = int(state.get("dot_damage", 3) or 3)
    root_duration = int(state.get("root_duration", 2) or 2)
    dot_duration = int(state.get("dot_duration", 2) or 2)
    hit_damage = int(state.get("hit_damage", 3) or 3)

    # Damage-over-time pass for already-ensnared targets.
    for tid in list(dot_targets.keys()):
        rem = int(dot_targets.get(tid, 0))
        if rem <= 0:
            dot_targets.pop(tid, None)
            continue
        target = entity_ops_system.get_actor(level, tid)
        if target is None:
            dot_targets.pop(tid, None)
            continue
        try:
            target.stats.hp -= dot_damage
            target.stats.clamp()
        except Exception:
            dot_targets.pop(tid, None)
            continue
        rem -= 1
        if rem <= 0:
            dot_targets.pop(tid, None)
        else:
            dot_targets[tid] = rem
        if int(getattr(target.stats, "hp", 0)) <= 0:
            game._kill_actor(
                level,
                target,
                killer_id=caster_id,
                killer_is_player=(caster_id == str(getattr(game, "player_id", ""))),
            )
            dot_targets.pop(tid, None)
    if _debug_verbose:
        try:
            game._debug(f"[rune_vines] step dot_targets_active={len(dot_targets)}")
        except Exception:
            pass

    if not hostiles:
        if _debug_verbose:
            try:
                game._debug("[rune_vines] step early-return: no hostiles")
            except Exception:
                pass
        state["dot_targets"] = dot_targets
        return

    tips: list[dict[str, Any]] = list(state.get("tips", []) or [])

    grow_every = max(1, int(state.get("grow_every", 2) or 2))
    if (tick % grow_every) != 0:
        if _debug_verbose:
            try:
                game._debug(
                    f"[rune_vines] step early-return: tick%grow_every != 0 "
                    f"(tick={tick}, grow_every={grow_every})"
                )
            except Exception:
                pass
        state["dot_targets"] = dot_targets
        return

    grow_step = float(state.get("grow_step", 1.15) or 1.15)
    max_step = float(state.get("max_step", 3.2) or 3.2)
    seek_radius = float(state.get("seek_radius", 8.5) or 8.5)
    seed_radius = float(state.get("seed_radius", 6.0) or 6.0)
    angle_limit_deg = float(state.get("angle_limit_deg", 40.0) or 40.0)
    branch_chance = float(state.get("branch_chance", 0.08) or 0.08)
    hit_radius = float(state.get("hit_radius", 1.0) or 1.0)
    max_tips = max(1, int(state.get("max_tips", 24) or 24))
    growth_budget = max(1, int(state.get("growth_budget", 2) or 2))
    reseed_per_tick = max(0, int(state.get("reseed_per_tick", 2) or 2))
    min_growth_heartbeats = max(0, int(state.get("min_growth_heartbeats", 50) or 50))

    # During the early growth window, bias toward obvious continued growth:
    # multiple reseeds and multiple advancing tips so the effect does not
    # collapse into a single branch.
    if tick < min_growth_heartbeats:
        per_enemy = max(1, len(hostiles))
        growth_budget = max(growth_budget, min(6, per_enemy))
        reseed_per_tick = max(reseed_per_tick, min(6, per_enemy))
    if _debug_verbose:
        try:
            game._debug(
                f"[rune_vines] step params: growth_budget={growth_budget} "
                f"reseed_per_tick={reseed_per_tick} seek_radius={seek_radius:.2f} "
                f"seed_radius={seed_radius:.2f} max_tips={max_tips}"
            )
        except Exception:
            pass

    rng = getattr(game, "rng", None)
    if rng is None:
        class _FallbackRng:
            def random(self) -> float:
                return 0.0
            def uniform(self, a: float, b: float) -> float:
                return (a + b) * 0.5
        rng = _FallbackRng()

    new_tips: list[dict[str, Any]] = []
    grew_segments: list[tuple[float, float, float, float]] = []

    def _nearest_hostile_for(x: float, y: float) -> tuple[tuple[str, Actor, float, float, float, float] | None, float]:
        nearest = None
        best_d2 = 1e18
        for target in hostiles:
            dx = target[4] - x
            dy = target[5] - y
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                nearest = target
        return nearest, best_d2

    def _grow_from_vertex(
        src_idx: int,
        prev_dx: float,
        prev_dy: float,
        tx: float,
        ty: float,
        *,
        override_step: float | None = None,
    ) -> tuple[dict[str, Any] | None, tuple[float, float, float, float] | None]:
        if src_idx < 0 or src_idx >= len(pattern.vertices):
            return None, None
        if len(pattern.vertices) >= int(getattr(game.cfg, "max_vertices", 20000)):
            return None, None
        sx, sy = map(float, pattern.vertices[src_idx].pos)
        desired = _safe_norm(tx - sx, ty - sy)
        if desired == (0.0, 0.0):
            return None, None
        prev = _safe_norm(float(prev_dx), float(prev_dy))
        if prev == (0.0, 0.0):
            prev = desired
        grow_dir = _clamp_turn(prev, desired, angle_limit_deg)
        if grow_dir == (0.0, 0.0):
            grow_dir = desired
        dist = math.hypot(tx - sx, ty - sy)
        step = float(override_step) if override_step is not None else min(grow_step, dist)
        step = min(max_step, max(0.55, step))
        nx = sx + grow_dir[0] * step
        ny = sy + grow_dir[1] * step

        src_tags = dict(getattr(pattern.vertices[src_idx], "tags", {}) or {})
        src_tags["rune_vine"] = "1"
        new_idx = pattern.add_vertex((nx, ny), color="verdant", tags=src_tags)
        pattern.add_edge(src_idx, new_idx, color="verdant", weight=1.0)
        tip_obj = {
            "vertex": int(new_idx),
            "dx": float(grow_dir[0]),
            "dy": float(grow_dir[1]),
            "age": 0.0,
        }
        return tip_obj, (sx, sy, nx, ny)

    # 1) Reseed from pattern edges near hostiles each heartbeat.
    #    Choose nearest eligible edge PER hostile (not global best-first), so
    #    multiple fronts can form at once.
    used_edge_keys: set[tuple[int, int]] = set()
    reseeded = 0
    for _target_id, _obj, _wx, _wy, tx, ty in hostiles:
        if reseeded >= reseed_per_tick:
            break
        if len(tips) + len(new_tips) >= max_tips:
            break
        best_edge_idx = -1
        best_key = (0, 0)
        best_d2 = 1e18
        for idx, e in enumerate(getattr(pattern, "edges", []) or []):
            try:
                a_idx = int(getattr(e, "a"))
                b_idx = int(getattr(e, "b"))
                va = pattern.vertices[a_idx]
                vb = pattern.vertices[b_idx]
                mx = (float(va.pos[0]) + float(vb.pos[0])) * 0.5
                my = (float(va.pos[1]) + float(vb.pos[1])) * 0.5
            except Exception:
                continue
            key = (min(a_idx, b_idx), max(a_idx, b_idx))
            if key in used_edge_keys:
                continue
            dx = tx - mx
            dy = ty - my
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_edge_idx = idx
                best_key = key
        if best_edge_idx < 0 or math.sqrt(best_d2) > seed_radius:
            if _debug_verbose:
                try:
                    game._debug(
                        f"[rune_vines] reseed skip target@l({tx:.2f},{ty:.2f}) "
                        f"best_edge={best_edge_idx} dist={(math.sqrt(best_d2) if best_d2 < 1e17 else -1):.2f}"
                    )
                except Exception:
                    pass
            continue
        split = _split_pattern_edge_at_midpoint(pattern, best_edge_idx)
        if split is None:
            if _debug_verbose:
                try:
                    game._debug(f"[rune_vines] reseed split-fail edge={best_edge_idx}")
                except Exception:
                    pass
            continue
        used_edge_keys.add(best_key)
        mid_idx, (_ax, _ay), (_bx, _by) = split
        tip_obj, seg = _grow_from_vertex(
            int(mid_idx),
            tx - float(pattern.vertices[mid_idx].pos[0]),
            ty - float(pattern.vertices[mid_idx].pos[1]),
            tx,
            ty,
            override_step=min(1.35, grow_step * 1.15),
        )
        if tip_obj is not None and seg is not None:
            new_tips.append(tip_obj)
            grew_segments.append(seg)
            reseeded += 1
    if _debug_verbose:
        try:
            game._debug(
                f"[rune_vines] reseed summary: reseeded={reseeded} "
                f"new_tips={len(new_tips)} used_edge_keys={len(used_edge_keys)}"
            )
        except Exception:
            pass

    # 2) Grow multiple existing tips each heartbeat.
    #    Assign each hostile to its nearest tip (prefer distinct tips) to avoid
    #    all growth collapsing into one tendril.
    assignments: list[tuple[float, int, tuple[str, Actor, float, float, float, float]]] = []
    used_tip_indices: set[int] = set()
    for hostile in hostiles:
        tx, ty = hostile[4], hostile[5]
        best_i = -1
        best_d2 = 1e18
        for i, tip in enumerate(tips):
            if i in used_tip_indices:
                continue
            v_idx = int(tip.get("vertex", -1))
            if v_idx < 0 or v_idx >= len(pattern.vertices):
                continue
            sx, sy = map(float, pattern.vertices[v_idx].pos)
            dx = tx - sx
            dy = ty - sy
            d2 = dx * dx + dy * dy
            if d2 < best_d2:
                best_d2 = d2
                best_i = i
        if best_i >= 0 and best_d2 <= (seek_radius * seek_radius):
            assignments.append((best_d2, best_i, hostile))
            used_tip_indices.add(best_i)

    # Fallback so a lone hostile can still drive one tip even when we have
    # fewer/more tips than hostile assignments.
    if not assignments:
        for i, tip in enumerate(tips):
            v_idx = int(tip.get("vertex", -1))
            if v_idx < 0 or v_idx >= len(pattern.vertices):
                continue
            sx, sy = map(float, pattern.vertices[v_idx].pos)
            nearest, d2 = _nearest_hostile_for(sx, sy)
            if nearest is None or d2 > (seek_radius * seek_radius):
                continue
            assignments.append((d2, i, nearest))
        assignments.sort(key=lambda t: t[0])
    if _debug_verbose:
        try:
            game._debug(f"[rune_vines] growth assignments={len(assignments)}")
        except Exception:
            pass

    moves = 0
    for _d2, i, nearest in assignments:
        if moves >= growth_budget:
            break
        if len(pattern.vertices) >= int(getattr(game.cfg, "max_vertices", 20000)):
            break
        if i < 0 or i >= len(tips):
            continue
        tip = tips[i]
        v_idx = int(tip.get("vertex", -1))
        if v_idx < 0 or v_idx >= len(pattern.vertices):
            continue
        tx, ty = nearest[4], nearest[5]
        tip_obj, seg = _grow_from_vertex(
            v_idx,
            float(tip.get("dx", 0.0)),
            float(tip.get("dy", 0.0)),
            tx,
            ty,
        )
        if tip_obj is None or seg is None:
            if _debug_verbose:
                try:
                    game._debug(f"[rune_vines] grow skip: tip_idx={i} could not grow")
                except Exception:
                    pass
            continue
        # Advance this tip head.
        tip["vertex"] = int(tip_obj["vertex"])
        tip["dx"] = float(tip_obj["dx"])
        tip["dy"] = float(tip_obj["dy"])
        tip["age"] = float(tip.get("age", 0.0)) + 1.0
        grew_segments.append(seg)
        moves += 1

        # Low-probability local branch at tip end to keep an organic silhouette.
        if len(tips) + len(new_tips) < max_tips and float(rng.random()) < branch_chance:
            base_ang = math.atan2(float(tip_obj["dy"]), float(tip_obj["dx"]))
            ang = base_ang + math.radians(float(rng.uniform(-24.0, 24.0)))
            btx = float(pattern.vertices[int(tip_obj["vertex"])].pos[0]) + math.cos(ang) * grow_step
            bty = float(pattern.vertices[int(tip_obj["vertex"])].pos[1]) + math.sin(ang) * grow_step
            branch_tip, branch_seg = _grow_from_vertex(
                int(tip_obj["vertex"]),
                math.cos(ang),
                math.sin(ang),
                btx,
                bty,
                override_step=min(1.0, grow_step * 0.85),
            )
            if branch_tip is not None and branch_seg is not None:
                new_tips.append(branch_tip)
                grew_segments.append(branch_seg)
    if _debug_verbose:
        try:
            game._debug(
                f"[rune_vines] growth summary: moves={moves} grew_segments={len(grew_segments)} "
                f"tips_before={len(tips)} new_tips={len(new_tips)}"
            )
        except Exception:
            pass

    if new_tips:
        tips.extend(new_tips[: max(0, max_tips - len(tips))])
    state["tips"] = tips[:max_tips]

    # Hit logic for newly-grown vine segments.
    for ax, ay, bx, by in grew_segments:
        for target_id, target_actor, _wx, _wy, tx, ty in hostiles:
            if _distance_point_to_segment(tx, ty, ax, ay, bx, by) > hit_radius:
                continue
            try:
                target_actor.stats.hp -= hit_damage
                target_actor.stats.clamp()
            except Exception:
                continue
            try:
                target_actor.statuses["rooted"] = max(int(target_actor.statuses.get("rooted", 0)), root_duration)
            except Exception:
                pass
            dot_targets[target_id] = max(int(dot_targets.get(target_id, 0)), dot_duration)
            if int(getattr(target_actor.stats, "hp", 0)) <= 0:
                game._kill_actor(
                    level,
                    target_actor,
                    killer_id=caster_id,
                    killer_is_player=(caster_id == str(getattr(game, "player_id", ""))),
                )
                dot_targets.pop(target_id, None)

    state["dot_targets"] = dot_targets
    if _debug_verbose:
        try:
            game._debug(
                f"[rune_vines] step done: tips={len(state.get('tips', []) or [])} "
                f"dot_targets={len(dot_targets)} verts={len(getattr(pattern, 'vertices', []) or [])} "
                f"edges={len(getattr(pattern, 'edges', []) or [])}"
            )
        except Exception:
            pass

def act_corrosive_melt(self, actor_id: str) -> None:
    """Activate acidic mode on the current pattern.

    When active, edges that touch enemy tiles dissolve and deal damage
    based on their green intensity. Lasts until pattern reset.
    """
    level = self._level()
    player = self._player()

    # Check if already acidic
    if level.acidic_pattern:
        self.log.add("Pattern is already acidic.")
        return

    # Check if there's a pattern to make acidic
    if not level.pattern.vertices:
        self.log.add("No pattern to corrode. Place a terminus first.")
        return

    # Get mana cost from params
    mana_cost = self._param_value("corrosive_melt", "mana_cost")
    if mana_cost is None:
        mana_cost = 30

    # Check mana
    if player.stats.mana < mana_cost:
        self.log.add(f"Not enough mana ({int(player.stats.mana)}/{mana_cost}).")
        return

    # Spend mana and activate
    player.stats.mana -= mana_cost
    player.stats.clamp()
    level.acidic_pattern = True

    self.log.add("Pattern becomes acidic! Edges will dissolve on enemy contact.")

def act_start_fern(self, actor_id: str) -> None:
    """Toggle Barnsley fern auto-growth on the current pattern.

    When active, the fern grows as a connected tree using Barnsley affine
    transforms. Growth consumes coherence and oldest vertices are pruned
    when over capacity.
    """
    from edgecaster.systems import fern_growth

    level = self._level()

    # Check if there's a pattern anchor
    if not level.pattern_anchor:
        self.log.add("Need a pattern anchor to grow the fern from.")
        return

    # Toggle fern growth
    if level.fern_active:
        level.fern_active = False
        level.fern_accum = 0.0
        # Reset fern state for next activation
        fern_growth._reset_fern_state(level)
        if hasattr(level, "_fern_node_to_vertex"):
            del level._fern_node_to_vertex
        self.log.add("Fern growth stopped.")
        return

    # Activate fern growth
    level.fern_active = True
    level.fern_accum = 0.0
    # Reset fern state to start fresh
    fern_growth._reset_fern_state(level)
    if hasattr(level, "_fern_node_to_vertex"):
        del level._fern_node_to_vertex
    self.log.add("Fern begins to grow...")


def act_mirror_blade(
    self,
    actor_id: str,
    *,
    target_pos: Optional[Tuple[int, int]] = None,
) -> None:
    """Summon a mirror clone of the player that fights autonomously for 30 heartbeats."""
    from edgecaster.state.actors import Actor, Stats
    from edgecaster.systems import spawning as spawning_system
    from edgecaster.systems import blade_runtime as blade_runtime_system

    level = self._level()
    player = entity_ops_system.get_actor(level, actor_id)
    if player is None or not getattr(player, "alive", False):
        return

    # Mana cost: 8 or half max_mana, whichever is smaller
    mana_cost = min(8, max(1, int(getattr(player.stats, "max_mana", 0) or 0) // 2))
    current_mana = int(getattr(player.stats, "mana", 0) or 0)
    if current_mana < mana_cost:
        self.log.add(f"Not enough mana ({current_mana}/{mana_cost}).")
        return

    # Find spawn position near target
    spawn_pos = target_pos or player.pos
    # Search for nearest walkable, unoccupied tile
    found_pos = None
    sx, sy = spawn_pos
    for r in range(0, 6):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if abs(dx) + abs(dy) != r:
                    continue
                tx, ty = sx + dx, sy + dy
                if not level.world.in_bounds(tx, ty):
                    continue
                if not level.world.is_walkable(tx, ty):
                    continue
                if self._actor_at(level, (tx, ty)):
                    continue
                found_pos = (tx, ty)
                break
            if found_pos:
                break
        if found_pos:
            break

    if found_pos is None:
        self.log.add("No space to summon a mirror blade.")
        return

    # Deduct mana
    player.stats.mana -= mana_cost
    if hasattr(player.stats, "clamp"):
        player.stats.clamp()

    # Build clone stats: half player max HP (capped at current HP)
    p_stats = player.stats
    clone_hp = max(1, min(int(p_stats.hp), int(p_stats.max_hp) // 2))
    p_tags = getattr(player, "tags", {}) or {}
    base_attack = int(p_tags.get("base_attack", 2) or 2)

    # Create clone actor
    clone_id = self._new_id()
    abs_pos = self.abs_from_zone_local(level.coord, found_pos)
    clone = Actor(
        id=clone_id,
        name=f"Mirror {player.name}",
        pos=found_pos,
        abs_pos=abs_pos,
        glyph=getattr(player, "glyph", "@"),
        color=(140, 200, 255),  # Cyan tint to distinguish
        faction="player",
        stats=Stats(
            hp=clone_hp,
            max_hp=clone_hp,
            mana=0,
            max_mana=0,
        ),
        actions=tuple(getattr(player, "actions", ("move", "wait"))),
        tags={
            "ai": "mirror_blade_clone",
            "mirror_blade_clone": True,
            "summoner_id": actor_id,
            "base_attack": base_attack,
            "no_xp": True,
        },
        statuses={"mirror_blade_ttl": 10},
    )

    # Copy blade state from player so clone has matching melee damage/reach
    try:
        player_blade = blade_runtime_system.ensure_actor_blade_state(self, actor_id)
        from edgecaster.systems.blade_runtime import BladeState
        clone_blade = BladeState(generators=list(player_blade.generators))
        blade_states = getattr(self, "blade_states", None)
        if not isinstance(blade_states, dict):
            blade_states = {}
            self.blade_states = blade_states
        blade_states[clone_id] = clone_blade
    except Exception:
        pass

    # Register and schedule AI
    spawning_system.register_actor(self, level, clone, schedule_ai=True)

    self.log.add("A shimmering mirror of yourself steps into being.")
