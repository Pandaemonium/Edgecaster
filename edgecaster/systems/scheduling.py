"""
Scheduling and time advancement system.

Handles:
- Event scheduling with heapq priority queue
- Time advancement and event processing
- Periodic regen ticks
- Cooldown decay for entities and actors
- Coherence drain based on pattern vertices
- Frozen/slow effect decay
- Attack bonus and action tick offset decay

All functions accept (game, level, ...) as parameters.
"""

from __future__ import annotations

import heapq
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState
    from edgecaster.state.actors import Actor


def schedule(game: "Game", level: "LevelState", delay: int, action: Callable[[], None]) -> None:
    """Schedule an action to run after `delay` ticks."""
    level.order += 1
    heapq.heappush(level.events, (level.current_tick + delay, level.order, action))


def advance_time(
    game: "Game",
    level: "LevelState",
    delta: int,
    *,
    apply_player_systems: bool = True,
) -> None:
    """
    Advance time by `delta` ticks, executing any scheduled events.

    Also handles:
    - Activation TTL decay
    - FOV updates
    - Lorenz aura advancement
    - Coherence drain
    - Cooldown ticks
    - Pattern motion
    """
    
    # Yoga safety net: ensure staged ontology matches last known view before ticking
    try:
        abs_rect = getattr(game, "_last_view_abs_rect", None)
        cam_lod = getattr(game, "_last_view_cam_lod", None)
        if abs_rect is not None and cam_lod is not None:
            game.sync_attention_instantiation(abs_rect, cam_lod=float(cam_lod))
    except Exception:
        pass

    # Import here to avoid circular imports
    from edgecaster.patterns import motion as pattern_motion

    target = level.current_tick + delta
    while level.events and level.events[0][0] <= target:
        tick, _, action = heapq.heappop(level.events)
        level.current_tick = tick
        action()
    level.current_tick = target

    # Cooldowns tick down (always, for active zones)
    cooldown_tick(game, level, delta)

    if not apply_player_systems:
        return

    # Decay activation TTL
    if level.activation_ttl > 0:
        level.activation_ttl = max(0, level.activation_ttl - delta)
        if level.activation_ttl == 0:
            level.activation_points = []

    # FOV update if needed
    if level.need_fov:
        game._update_fov(level)

    # Advance the Lorenz aura in game-time
    game._advance_lorenz(level, delta)

    # Coherence drain based on vertices
    coherence_tick(game, level, delta)

    # Chakra charge + resonance tick
    chakra_charge_tick(game, level, delta)

    # Pattern motion tick
    pattern_motion.step_motion(game, level, delta)

    # Acidic pattern damage (corrosive melt)
    acidic_pattern_tick(game, level)

    # Fern growth tick (Barnsley fern auto-growth)
    from edgecaster.systems import fern_growth
    fern_growth.tick(game, level, delta)
    # Sealing rune trials (match evaluation)
    try:
        from edgecaster.systems import seal_trials
        seal_trials.update_trial(game, level)
    except Exception:
        pass


def chakra_charge_tick(game: "Game", level: "LevelState", delta: int) -> None:
    """Update chakra charge for actors with chakra_state."""
    if delta <= 0:
        return

    try:
        from edgecaster.prototypes import resolve_body_schema
        from edgecaster.systems import chakras as chakra_system
    except Exception:
        return

    # Only charge while the player's pattern exists
    charging = bool(getattr(level, "pattern", None) and level.pattern.vertices)

    for actor in level.actors.values():
        chakra_state = getattr(actor, "chakra_state", None)
        if chakra_state is None:
            continue

        # Only the player currently builds charge from the shared pattern.
        if getattr(actor, "id", None) != getattr(game, "player_id", None):
            continue

        try:
            dex = int(getattr(actor.stats, "agi", 0))
        except Exception:
            dex = 0

        body_schema = resolve_body_schema(actor) or {}
        chakra_system.tick_chakra_charge(
            body_schema,
            chakra_state,
            delta,
            charging=charging,
            dex=dex,
        )


def start_regen(game: "Game", level: "LevelState", actor_id: str, amount: int, interval: int) -> None:
    """
    Start periodic regen for an actor: heals `amount` HP every `interval` ticks.
    """
    def tick() -> None:
        actor = level.actors.get(actor_id)
        if actor is None or getattr(actor, "alive", True) is False:
            return
        try:
            stats = actor.stats
            if stats.hp < stats.max_hp:
                stats.hp = min(stats.max_hp, stats.hp + amount)
        except Exception:
            pass
        # Reschedule if still alive
        if actor is not None:
            schedule(game, level, interval, tick)

    schedule(game, level, interval, tick)


def coherence_tick(game: "Game", level: "LevelState", delta: int) -> None:
    """Drain coherence each tick based on vertex count beyond INT*4."""
    from edgecaster.patterns import builder

    player = game._player()
    stats = player.stats
    intel = game.character.stats.get("int", 0)
    discount = intel * 4
    verts = len(level.pattern.vertices) if level.pattern else 0
    over = max(0, verts - discount)
    if over <= 0:
        return
    # Drain per tick: over/10 per design
    drain = over * delta / 10.0
    stats.coherence = int(max(0, stats.coherence - drain))
    if stats.coherence <= 0:
        # Pattern unravels immediately
        level.pattern = builder.Pattern()
        level.pattern_anchor = None
        level.activation_points = []
        level.activation_ttl = 0
        # Clear fern growth state
        level.fern_active = False
        level.fern_growth_tips = []
        level.fern_accum = 0.0
        game.log.add("Your pattern loses coherence and unravels.")
        stats.coherence = stats.max_coherence


def cooldown_tick(game: "Game", level: "LevelState", delta: int) -> None:
    """Tick down cooldowns on actors, ground entities, and inventory items."""
    seen: set[str] = set()

    def tick_entity(ent) -> None:
        if not hasattr(ent, "cooldowns"):
            return
        ent_id = getattr(ent, "id", None)
        if ent_id and ent_id in seen:
            return
        if ent_id:
            seen.add(ent_id)
        cds = getattr(ent, "cooldowns", {})
        to_delete = []
        for name, val in list(cds.items()):
            new_val = max(0, val - delta)
            if new_val <= 0:
                to_delete.append(name)
            else:
                cds[name] = new_val
        for name in to_delete:
            del cds[name]

    for act in level.actors.values():
        tick_entity(act)
    for ent in level.entities.values():
        tick_entity(ent)
    for items in getattr(game, "inventories", {}).values():
        for ent in items:
            tick_entity(ent)

    # Tick down frozen/chilled slow effects (decay 0.1 every 10 ticks).
    _tick_frozen_slow(level, delta)

    # Tick down temporary attack bonuses (used by enemies like the Gory Ascetic).
    _tick_attack_bonus(level, delta)

    # Tick down additive action-speed modifiers (used by War Drummer haste).
    _tick_action_offset(level, delta)


def _tick_frozen_slow(level: "LevelState", delta: int) -> None:
    """Decay frozen slow effects on actors."""
    for actor in level.actors.values():
        tags = getattr(actor, "tags", None) or {}
        mult = float(tags.get("frozen_slow", 1.0))
        if mult <= 1.0:
            continue
        acc = float(tags.get("frozen_slow_timer", 0.0))
        acc += delta
        if acc >= 10:
            steps = int(acc // 10)
            acc = acc % 10
            mult = max(1.0, mult - steps * 0.1)
        if mult <= 1.0 + 1e-6:
            tags.pop("frozen_slow", None)
            tags.pop("frozen_slow_timer", None)
        else:
            tags["frozen_slow"] = mult
            tags["frozen_slow_timer"] = acc
        actor.tags = tags


def _tick_attack_bonus(level: "LevelState", delta: int) -> None:
    """Decay temporary attack bonuses on actors."""
    for actor in level.actors.values():
        tags = getattr(actor, "tags", None) or {}
        try:
            ticks = int(tags.get("attack_bonus_ticks", 0))
        except Exception:
            ticks = 0
        if ticks <= 0:
            continue
        ticks = max(0, ticks - delta)
        if ticks <= 0:
            tags.pop("attack_bonus", None)
            tags.pop("attack_bonus_ticks", None)
        else:
            tags["attack_bonus_ticks"] = ticks
        actor.tags = tags


def _tick_action_offset(level: "LevelState", delta: int) -> None:
    """Decay additive action-speed modifiers on actors."""
    for actor in level.actors.values():
        tags = getattr(actor, "tags", None) or {}
        try:
            offset = int(tags.get("action_tick_offset", 0))
        except Exception:
            offset = 0
        if offset == 0:
            continue
        try:
            ticks = int(tags.get("action_tick_offset_ticks", 0))
        except Exception:
            ticks = 0
        if ticks <= 0:
            # Defensive: remove broken/incomplete entries.
            tags.pop("action_tick_offset", None)
            tags.pop("action_tick_offset_ticks", None)
            actor.tags = tags
            continue

        ticks = max(0, ticks - delta)
        if ticks <= 0:
            tags.pop("action_tick_offset", None)
            tags.pop("action_tick_offset_ticks", None)
        else:
            tags["action_tick_offset_ticks"] = ticks
        actor.tags = tags


def slow_mult(actor: "Actor") -> float:
    """Get slow multiplier for an actor. Delegates to action_runner."""
    from edgecaster.systems import action_runner
    return action_runner.slow_mult(actor)


def apply_action_tick_offset(actor: "Actor", delay: int) -> int:
    """Apply additive tick offset. Delegates to action_runner."""
    from edgecaster.systems import action_runner
    return action_runner.apply_tick_offset(actor, delay)


def acidic_pattern_tick(game: "Game", level: "LevelState") -> None:
    """Process acidic pattern damage: dissolve edges that touch enemies.

    When acidic_pattern is active, any pattern edge that touches an enemy tile
    is deleted, and damage is dealt based on the edge's green intensity.
    """
    if not getattr(level, "acidic_pattern", False):
        return

    pattern = level.pattern
    if not pattern or not pattern.vertices:
        return

    anchor = level.pattern_anchor
    if anchor is None:
        return

    # Get damage scale from params
    try:
        damage_scale = game._param_value("corrosive_melt", "damage_scale")
        if damage_scale is None:
            damage_scale = 1.0
    except Exception:
        damage_scale = 1.0

    # Build world-space vertices (Vertex objects have .pos attribute)
    ax, ay = anchor
    verts_world = [(v.pos[0] + ax, v.pos[1] + ay) for v in pattern.vertices]

    def line_points(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
        """Bresenham line (integer grid points)."""
        points: list[tuple[int, int]] = []
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

    # Map enemy positions to actors
    enemy_positions: dict[tuple[int, int], "Actor"] = {}
    player_id = getattr(game, "player_id", None)
    for actor in level.actors.values():
        if not getattr(actor, "alive", True):
            continue
        if actor.id == player_id:
            continue
        if getattr(actor, "faction", None) == "player":
            continue
        enemy_positions[actor.pos] = actor

    if not enemy_positions:
        return

    # Find edges that touch enemies and compute damage
    edges_to_remove: list[int] = []
    damage_by_actor: dict[str, float] = {}

    # Get edge_colors dict if available (set by verdant_edges, etc.)
    edge_colors = getattr(pattern, "edge_colors", {}) or {}

    edges = getattr(pattern, "edges", []) or []
    for edge_idx, edge in enumerate(edges):
        try:
            a = verts_world[int(edge.a)]
            b = verts_world[int(edge.b)]
        except Exception:
            continue

        # Get edge tiles
        edge_tiles = line_points(
            int(round(float(a[0]))),
            int(round(float(a[1]))),
            int(round(float(b[0]))),
            int(round(float(b[1]))),
        )

        # Check for enemy contact
        for tile in edge_tiles:
            if tile in enemy_positions:
                actor = enemy_positions[tile]
                # Calculate green intensity (0.0 to 1.0) - check edge_colors dict first
                green_intensity = _edge_green_intensity(edge, edge_colors)
                damage = green_intensity * damage_scale
                # Accumulate damage for this actor
                damage_by_actor[actor.id] = damage_by_actor.get(actor.id, 0) + damage
                # Mark edge for removal
                if edge_idx not in edges_to_remove:
                    edges_to_remove.append(edge_idx)
                break  # Edge touched an enemy, no need to check more tiles

    if not edges_to_remove:
        return

    # Apply damage to enemies
    for actor_id, damage in damage_by_actor.items():
        actor = level.actors.get(actor_id)
        if actor and getattr(actor, "alive", True):
            int_damage = max(1, int(damage))
            actor.stats.hp -= int_damage
            actor.stats.clamp()
            game.log.add(f"Acidic edge dissolves into {getattr(actor, 'name', 'enemy')} for {int_damage} damage!")
            if actor.stats.hp <= 0:
                game._kill_actor(level, actor, killer_id=getattr(game, "player_id", None), killer_is_player=True)

    # Remove edges (in reverse order to preserve indices)
    from edgecaster.patterns import builder
    new_edges = [e for i, e in enumerate(edges) if i not in edges_to_remove]
    pattern.edges = new_edges

    # Prune orphaned vertices
    _prune_orphaned_vertices(pattern)


def _edge_green_intensity(edge, edge_colors: dict) -> float:
    """Calculate the green intensity of an edge (0.0 to 1.0).

    First checks edge_colors dict (set by verdant_edges), then falls back to edge.color.
    """
    color = None

    # First try to get color from edge_colors dict (keyed by (a, b) or (b, a))
    a_idx = getattr(edge, "a", None)
    b_idx = getattr(edge, "b", None)
    if a_idx is not None and b_idx is not None:
        # Try both orderings since edge_colors uses normalized keys
        color = edge_colors.get((a_idx, b_idx)) or edge_colors.get((b_idx, a_idx))
        # Also try with min/max normalization
        if color is None:
            norm_key = (min(a_idx, b_idx), max(a_idx, b_idx))
            color = edge_colors.get(norm_key)

    # Fall back to edge.color if not in edge_colors
    if color is None:
        color = getattr(edge, "color", None)

    if color is None:
        return 0.0

    # If color is a string like "green", "neutral", etc.
    if isinstance(color, str):
        if color == "green":
            return 1.0
        elif color == "neutral":
            return 0.0
        else:
            return 0.0

    # If color is an RGB tuple (r, g, b)
    if isinstance(color, (tuple, list)) and len(color) >= 3:
        r, g, b = color[0], color[1], color[2]
        # Normalize to 0-1 if values are 0-255
        if max(r, g, b) > 1:
            r, g, b = r / 255.0, g / 255.0, b / 255.0
        # Green intensity = how much greener than red/blue
        # Pure green (0, 255, 0) = 1.0
        # White (255, 255, 255) = 0.0
        # We want edges that are more green relative to other colors
        if g == 0:
            return 0.0
        avg_rb = (r + b) / 2.0
        green_excess = max(0.0, g - avg_rb)
        return min(1.0, green_excess)

    return 0.0


def _prune_orphaned_vertices(pattern) -> None:
    """Remove vertices not connected to any edge, preserving vertex 0 (root)."""
    if not pattern.vertices or not pattern.edges:
        return

    # Find all vertices referenced by edges
    used_vertices: set[int] = set()
    for edge in pattern.edges:
        used_vertices.add(int(edge.a))
        used_vertices.add(int(edge.b))

    # Always keep vertex 0 (the root)
    used_vertices.add(0)

    # If all vertices are used, nothing to do
    if len(used_vertices) == len(pattern.vertices):
        return

    # Build mapping from old indices to new indices
    old_to_new: dict[int, int] = {}
    new_vertices: list = []
    for old_idx in sorted(used_vertices):
        old_to_new[old_idx] = len(new_vertices)
        new_vertices.append(pattern.vertices[old_idx])

    # Update edge indices
    from edgecaster.state.patterns import Edge
    new_edges = []
    for edge in pattern.edges:
        new_a = old_to_new.get(int(edge.a))
        new_b = old_to_new.get(int(edge.b))
        if new_a is not None and new_b is not None:
            new_edges.append(Edge(a=new_a, b=new_b, color=edge.color))

    pattern.vertices = new_vertices
    pattern.edges = new_edges
