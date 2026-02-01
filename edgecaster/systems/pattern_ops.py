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


def try_place_terminus(game: "Game", target_abs: Tuple[int, int]) -> None:
    """Attempt to place the pattern terminus at the ABS target position."""
    lvl = game._level()
    if not lvl.awaiting_terminus:
        return

    trial = getattr(lvl, "seal_trial", None)
    use_trial_anchor = False
    anchor_local: Tuple[int, int]

    if trial is not None and not getattr(trial, "sealed", False):
        # Trial placement: snapped to canonical terminus → force canonical root anchor.
        trial_term = tuple(getattr(trial, "terminus_tile", ()))
        if trial_term and target_abs == game.abs_from_zone_local(game.zone_coord, trial_term):
            use_trial_anchor = True
            anchor_local = tuple(trial.root_tile)
        else:
            anchor_local = game._player().pos
    else:
        anchor_local = game._player().pos

    # Canonical ABS anchor is derived from the local anchor in the current zone.
    anchor_abs = game.abs_from_zone_local(game.zone_coord, anchor_local)

    dx = int(target_abs[0] - anchor_abs[0])
    dy = int(target_abs[1] - anchor_abs[1])
    dist2 = dx * dx + dy * dy

    if not use_trial_anchor and dist2 > game.place_range * game.place_range:
        game.log.add("Out of range.")
        return

    def do_place() -> None:
        # Build pattern in pattern-local coords; anchor is canonical ABS.
        pat = builder.line_pattern((0.0, 0.0), (dx, dy))

        st = game._pattern_state(depth=game.zone_coord[2])
        st["pattern"] = pat
        st["anchor_abs"] = (int(anchor_abs[0]), int(anchor_abs[1]))
        st["activation_points"] = []
        st["activation_ttl"] = 0
        st["pattern_motion"] = None

        # Keep level view consistent immediately.
        game._sync_level_pattern_view(lvl)
        lvl.pattern_motion = None
        lvl.acidic_pattern = False
        lvl.fern_active = False
        lvl.fern_growth_tips = []
        lvl.fern_accum = 0.0
        if hasattr(game, "_commit_pattern_state_from_level"):
            game._commit_pattern_state_from_level(lvl)
        

        game.log.add(f"Terminus placed at ABS {target_abs}.")

    game._schedule(lvl, game.cfg.place_time_ticks, do_place)
    game._advance_time(lvl, game.cfg.place_time_ticks)
    lvl.awaiting_terminus = False



# ---------------------------------------------------------------------------
# Pattern Reset
# ---------------------------------------------------------------------------

def reset_pattern(game: "Game") -> None:
    """Reset the pattern to empty and restore coherence."""
    lvl = game._level()

    st = game._pattern_state(depth=game.zone_coord[2])
    st["pattern"] = builder.Pattern()
    st["anchor_abs"] = None
    st["activation_points"] = []
    st["activation_ttl"] = 0
    st["pattern_motion"] = None

    game._sync_level_pattern_view(lvl)

    lvl.acidic_pattern = False
    lvl.fern_active = False
    lvl.fern_growth_tips = []
    lvl.fern_accum = 0.0

    player = game._player()
    player.stats.coherence = player.stats.max_coherence
    if hasattr(game, "_commit_pattern_state_from_level"):
        game._commit_pattern_state_from_level(lvl)

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
    if hasattr(game, "_commit_pattern_state_from_level"):
        game._commit_pattern_state_from_level(level)


# ---------------------------------------------------------------------------
# Activation Helpers
# ---------------------------------------------------------------------------

def activation_origin(level: "LevelState") -> Optional[Tuple[int, int]]:
    """Return the pattern anchor as the activation origin."""
    return level.pattern_anchor
