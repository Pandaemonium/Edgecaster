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
    actor = level.actors.get(actor_id)
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
    actor = level.actors.get(actor_id)
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

            pos_abs = getattr(obj, "abs_pos", None)
            if pos_abs is None:
                pos_abs = self.abs_from_zone_local(lvl.coord, obj.pos)
            tx = int(pos_abs[0])
            ty = int(pos_abs[1])
            # Distance measured from tile-center to the rush polyline.
            d = _distance_to_path(float(tx) + 0.5, float(ty) + 0.5)
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

            if int(getattr(obj.stats, "hp", 0)) <= 0 and tid in lvl.actors:
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
    actor = level.actors.get(actor_id)
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

        # Calculate damage based on vertices near enemy.
        dmg = damage_from_vertices(
            active_verts,
            enemy.pos,
            flask_radius,
            per_vertex_damage,
            cap=damage_cap,
        )

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
        inv = self.get_inventory(actor_id)
        try:
            inv.remove(flask_item)
        except ValueError:
            pass  # Already removed

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
    actor = level.actors.get(actor_id)
    if actor is None:
        return
    px, py = actor.pos
    radius = 10
    rng = getattr(self, "rng", None)

    candidates = []
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if max(abs(dx), abs(dy)) > radius:
                continue
            tx, ty = px + dx, py + dy
            if not level.world.in_bounds(tx, ty):
                continue
            if not level.world.is_walkable(tx, ty):
                continue
            candidates.append((tx, ty))

    if candidates:
        dest = rng.choice(candidates) if rng else candidates[0]
        actor.pos = dest
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
    actor = level.actors.get(actor_id)
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
            pos = getattr(obj, "pos", None)
            if not pos:
                continue
            tx, ty = int(round(pos[0])), int(round(pos[1]))
            dmg_val = 0.0
            if (tx, ty) in direct_tiles:
                redness = direct_tiles[(tx, ty)]
                dmg_val = base_direct * (redness / 255.0) * mult
            elif (tx, ty) in indirect_tiles:
                redness = indirect_tiles[(tx, ty)]
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
                        if tid in level.actors:
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
                        elif tid in level.entities:
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
    actor = level.actors.get(actor_id)
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
        for aid, act in level.actors.items():
            combined[aid] = act
        for eid, ent in level.entities.items():
            if eid not in combined:
                combined[eid] = ent

        for tid, obj in combined.items():
            pos = getattr(obj, "pos", None)
            if not pos:
                continue
            tx, ty = int(round(pos[0])), int(round(pos[1]))
            heal_val = 0.0
            if (tx, ty) in direct_tiles:
                gval = direct_tiles[(tx, ty)]
                heal_val = base_direct * (gval / 255.0) * mult
            elif (tx, ty) in indirect_tiles:
                gval = indirect_tiles[(tx, ty)]
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
    actor = level.actors.get(actor_id)
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
    actor = level.actors.get(actor_id)
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
    kick_points: List[Tuple[float, float]] = []
    for v in pattern.vertices:
        tags = getattr(v, "tags", {}) or {}
        nodes: List[str] = []
        single = str(tags.get("chakra_node", "")).strip()
        if single:
            nodes.append(single)
        many = str(tags.get("chakra_nodes", "")).strip()
        if many:
            nodes.extend([s for s in many.split("|") if s])
        if not nodes:
            continue
        if any(_is_foot_lineage(node_id) for node_id in nodes):
            kick_points.append((float(v.pos[0] + anchor[0]), float(v.pos[1] + anchor[1])))

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
        pos = getattr(obj, "pos", None)
        stats = getattr(obj, "stats", None)
        if not pos or stats is None or not hasattr(stats, "hp"):
            continue
        tx = float(pos[0]) + 0.5
        ty = float(pos[1]) + 0.5

        total_dmg = 0
        for kx, ky in kick_points:
            dx = tx - kx
            dy = ty - ky
            dist = math.hypot(dx, dy)
            if dist > radius:
                continue
            falloff = max(0.0, 1.0 - (dist / max(radius, r_eps)))
            total_dmg += max(1, int(math.ceil(base_damage * falloff)))

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
        if tid in level.actors:
            if int(getattr(stats, "hp", 0)) <= 0:
                self._kill_actor(
                    level,
                    obj,
                    killer_id=actor_id,
                    killer_is_player=caster_is_player,
                )
            continue

        # Non-actor entities with HP are removed when broken.
        if int(getattr(stats, "hp", 0)) <= 0 and tid in level.entities:
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
    points: list[tuple[float, float]] = []
    for v in getattr(pattern, "vertices", []) or []:
        nodes = self._chakra_nodes_for_vertex(v)
        if not nodes:
            continue
        if not any(predicate(node_id) for node_id in nodes):
            continue
        points.append((float(v.pos[0] + anchor[0]), float(v.pos[1] + anchor[1])))
    return points

def act_palm_burst(self, actor_id: str) -> None:
    """Pulse damage from hand/palm/finger-lineage chakra vertices."""
    level = self._level()
    actor = level.actors.get(actor_id)
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

    burst_points = self._chakra_world_points(
        pattern,
        anchor,
        predicate=_is_palm_lineage,
    )
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
        pos = getattr(obj, "pos", None)
        stats = getattr(obj, "stats", None)
        if not pos or stats is None or not hasattr(stats, "hp"):
            continue
        tx = float(pos[0]) + 0.5
        ty = float(pos[1]) + 0.5

        total_dmg = 0
        for px, py in burst_points:
            dx = tx - px
            dy = ty - py
            dist = math.hypot(dx, dy)
            if dist > radius:
                continue
            falloff = max(0.0, 1.0 - (dist / max(radius, r_eps)))
            total_dmg += max(1, int(math.ceil(base_damage * falloff)))

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

        if tid in level.actors and int(getattr(stats, "hp", 0)) <= 0:
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
    actor = level.actors.get(actor_id)
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
    strike_points: list[tuple[float, float]] = []
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
        strike_points.append(((lx + rx) * 0.5, (ly + ry) * 0.5))

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
        pos = getattr(obj, "pos", None)
        stats = getattr(obj, "stats", None)
        if not pos or stats is None or not hasattr(stats, "hp"):
            continue
        tx = float(pos[0]) + 0.5
        ty = float(pos[1]) + 0.5

        total_dmg = 0
        for sx, sy in strike_points:
            dx = tx - sx
            dy = ty - sy
            dist = math.hypot(dx, dy)
            if dist > radius:
                continue
            falloff = max(0.0, 1.0 - (dist / max(radius, r_eps)))
            total_dmg += max(1, int(math.ceil(base_damage * falloff)))

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

        if tid in level.actors and int(getattr(stats, "hp", 0)) <= 0:
            self._kill_actor(
                level,
                obj,
                killer_id=actor_id,
                killer_is_player=caster_is_player,
            )

    if caster_is_player and not hit_any:
        self.log.add("Your mirrored strike finds no target.")

def act_choking_vines(self, actor_id: str) -> None:
    """Seed a tendril field that grows from rune-edge centers near enemies.

    Behavior model:
    - Initial tendril tips spawn from centers of pattern edges that are near
      hostile targets.
    - Each tick, tips extend toward nearby hostiles.
    - Existing tips can branch into new tips, producing a creeping vine net.
    - Contact applies light damage plus a mild ensnare slow.

    Runtime simulation is advanced by scheduling.choking_vines_tick().
    """
    level = self._level()
    actor = level.actors.get(actor_id)
    if actor is None:
        return

    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)
    if pattern is None or anchor is None or not getattr(pattern, "edges", None):
        if actor_id == self.player_id:
            self.log.add("No rune edges available for Choking Vines.")
        return

    # Cost gate. This is intentionally expensive for a persistent control tool.
    mana_cost = 28
    try:
        if actor.stats.mana < mana_cost:
            if actor_id == self.player_id:
                self.log.add("Not enough mana for Choking Vines.")
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
            self.log.add("No rune geometry to grow vines from.")
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
            self.log.add("No rune edges available for Choking Vines.")
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
        pos = getattr(obj, "pos", None)
        if not pos:
            continue
        hostile_abs.append((
            str(tid),
            obj,
            float(pos[0]) + abs_ox + 0.5,
            float(pos[1]) + abs_oy + 0.5,
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
        self.log.add("Vines uncoil from your rune and seek warm blood.")

    # Persist to canonical per-depth pattern state.
    if hasattr(self, "_commit_pattern_state_from_level"):
        self._commit_pattern_state_from_level(level)

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


