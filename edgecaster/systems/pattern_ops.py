"""
Pattern Operations - Pattern projection, vertex queries, and activation glue.

This module manages:
- Vertex projection and world-space queries
- Neighbor graph traversal (BFS)
- Pattern placement mode
- Pattern reset

The complex tick-based actions (act_ignite, act_regrow, act_freeze) remain
in game.py for now due to tight coupling with the scheduler.

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state.patterns import Pattern

from edgecaster.patterns.activation import project_vertices
from edgecaster.patterns import builder
from edgecaster.patterns import motion as pattern_motion


# ---------------------------------------------------------------------------
# Vertex Projection and Queries
# ---------------------------------------------------------------------------

def projected_vertices(game: "Game") -> List[Tuple[float, float]]:
    """Return pattern vertices in world-space coordinates."""
    lvl = game._level()
    if lvl.pattern_anchor is None:
        return []
    return project_vertices(lvl.pattern, lvl.pattern_anchor)


def nearest_vertex(game: "Game", world_pos: Tuple[float, float]) -> Optional[int]:
    """Find the index of the vertex nearest to world_pos."""
    verts = projected_vertices(game)
    if not verts:
        return None
    wx, wy = world_pos
    best_idx = None
    best_d2 = 1e18
    for i, (vx, vy) in enumerate(verts):
        dx = vx - wx
        dy = vy - wy
        d2 = dx * dx + dy * dy
        if d2 < best_d2:
            best_d2 = d2
            best_idx = i
    return best_idx


def _build_adjacency(pattern: "Pattern") -> Dict[int, List[int]]:
    """Build adjacency list from pattern edges."""
    adj: Dict[int, List[int]] = {}
    for e in pattern.edges:
        adj.setdefault(e.a, []).append(e.b)
        adj.setdefault(e.b, []).append(e.a)
    return adj


def neighbors_of(game: "Game", idx: int) -> List[int]:
    """Return vertex indices adjacent to the given vertex."""
    lvl = game._level()
    adj = _build_adjacency(lvl.pattern)
    return adj.get(idx, [])


def neighbor_set_depth(game: "Game", seed: int, depth: int) -> List[int]:
    """Return unique vertices within `depth` hops (including seed).

    Uses BFS to expand from seed vertex.
    """
    if depth <= 0:
        return [seed]

    visited = {seed}
    frontier = {seed}
    lvl = game._level()
    adj = _build_adjacency(lvl.pattern)

    for _ in range(depth):
        new_frontier = set()
        for node in frontier:
            for n in adj.get(node, []):
                if n not in visited:
                    visited.add(n)
                    new_frontier.add(n)
        if not new_frontier:
            break
        frontier = new_frontier

    return list(visited)


# ---------------------------------------------------------------------------
# Pattern Placement
# ---------------------------------------------------------------------------

def begin_place_mode(game: "Game") -> None:
    """Enter pattern placement mode, awaiting terminus selection."""
    lvl = game._level()
    lvl.awaiting_terminus = True
    game.log.add(f"Select terminus within {game.place_range} tiles (click or arrows+Enter).")


def try_place_terminus(game: "Game", target: Tuple[int, int]) -> None:
    """Attempt to place the pattern terminus at the target position."""
    lvl = game._level()
    if not lvl.awaiting_terminus:
        return

    trial = getattr(lvl, "seal_trial", None)
    use_trial_anchor = False
    anchor_x: int
    anchor_y: int

    if trial is not None and not getattr(trial, "sealed", False):
        # Trial placement: when snapped to the canonical terminus,
        # force the pattern anchor to the canonical root so alignment is exact.
        if target == tuple(trial.terminus_tile):
            use_trial_anchor = True
            anchor_x, anchor_y = trial.root_tile
        else:
            anchor_x, anchor_y = game._player().pos
    else:
        anchor_x, anchor_y = game._player().pos

    dx = target[0] - anchor_x
    dy = target[1] - anchor_y
    dist2 = dx * dx + dy * dy

    if not use_trial_anchor and dist2 > game.place_range * game.place_range:
        game.log.add("Out of range.")
        return

    def do_place() -> None:
        lvl.pattern = builder.line_pattern((0.0, 0.0), (dx, dy))
        lvl.pattern_anchor = (anchor_x, anchor_y)
        lvl.pattern_motion = None
        lvl.acidic_pattern = False  # Clear corrosive melt on new pattern
        # Clear fern growth state on new pattern
        lvl.fern_active = False
        lvl.fern_growth_tips = []
        lvl.fern_accum = 0.0
        game.log.add(f"Terminus placed at {target}.")

    game._schedule(lvl, game.cfg.place_time_ticks, do_place)
    game._advance_time(lvl, game.cfg.place_time_ticks)
    lvl.awaiting_terminus = False


# ---------------------------------------------------------------------------
# Pattern Reset
# ---------------------------------------------------------------------------

def reset_pattern(game: "Game") -> None:
    """Reset the pattern to empty and restore coherence."""
    lvl = game._level()
    lvl.pattern = builder.Pattern()
    lvl.pattern_anchor = None
    lvl.activation_points = []
    lvl.activation_ttl = 0
    lvl.acidic_pattern = False  # Clear corrosive melt
    # Clear fern growth state
    lvl.fern_active = False
    lvl.fern_growth_tips = []
    lvl.fern_accum = 0.0
    # restore coherence to max when manually resetting
    player = game._player()
    player.stats.coherence = player.stats.max_coherence
    game.log.add("Rune reset.")


# ---------------------------------------------------------------------------
# Pattern Motion
# ---------------------------------------------------------------------------

def push_pattern(
    game: "Game",
    level: "LevelState",
    target_pos: Optional[Tuple[float, float]] = None,
    rotation_deg: float = 0,
) -> None:
    """Begin repeated motion/rotation of the current pattern."""
    pattern = getattr(level, "pattern", None)
    anchor = getattr(level, "pattern_anchor", None)

    if pattern is None or anchor is None or not pattern.vertices:
        return

    com = pattern_motion.center_of_mass(pattern)
    com_world = (com[0] + anchor[0], com[1] + anchor[1])

    if target_pos is None:
        target_pos = com_world

    dx = target_pos[0] - com_world[0]
    dy = target_pos[1] - com_world[1]
    dist = (dx * dx + dy * dy) ** 0.5

    # Allow pure rotation if targeting the current tile (avoid half-tile drift).
    if dist < 0.51:
        dx = dy = 0.0

    max_range = 5.0
    if dist > max_range and dist > 0:
        scale = max_range / dist
        dx *= scale
        dy *= scale

    pattern_motion.start_motion(level, (dx, dy), rotation_deg, interval=10)


# ---------------------------------------------------------------------------
# Activation Helpers
# ---------------------------------------------------------------------------

def activation_origin(level: "LevelState") -> Optional[Tuple[int, int]]:
    """Return the pattern anchor as the activation origin."""
    return level.pattern_anchor
