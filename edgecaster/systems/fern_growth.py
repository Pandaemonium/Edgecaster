"""
Fern Growth System - Grows a fern frond shape over time.

This creates a fern-like structure with:
- A curved central rachis (stem) that arcs gracefully
- Pinnae (leaflets) branching off alternately left and right
- Each pinna curves back toward the tip
- Sub-pinnae on larger fronds

The fern grows from the base upward, with new pinnae appearing
as the main stem extends.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Tuple, Optional

if TYPE_CHECKING:
    from edgecaster.game import Game
    from edgecaster.state.levels import LevelState

Vec2 = Tuple[float, float]


# -----------------------------------------------------------------------------
# Growth Tip - a point that can grow
# -----------------------------------------------------------------------------

@dataclass
class GrowthTip:
    """A growing point in the fern."""
    x: float
    y: float
    angle: float  # Current direction of growth (radians)
    curvature: float  # How much the angle changes per step (radians)
    generation: int  # 0 = main rachis, 1 = pinnae, 2 = pinnules
    length: float  # How much this tip has grown
    max_length: float  # Maximum length before stopping
    steps_since_branch: int = 0  # Steps since last pinna spawned
    branch_side: int = 1  # 1 or -1, alternates for pinnae
    vertex_id: Optional[int] = None  # Pattern vertex ID for this tip's position


# -----------------------------------------------------------------------------
# Fern State
# -----------------------------------------------------------------------------

@dataclass
class FernState:
    """State for the growing fern."""
    tips: List[GrowthTip] = field(default_factory=list)
    turn: int = 0


def _get_fern_state(level: "LevelState") -> FernState:
    """Get or initialize fern state."""
    if not hasattr(level, "_fern_state") or level._fern_state is None:
        state = FernState()
        # Start with main rachis growing upward with slight curve
        state.tips.append(GrowthTip(
            x=0.0, y=0.0,
            angle=-math.pi / 2,  # Pointing up (negative Y)
            curvature=0.02,  # Gentle curve to the right
            generation=0,
            length=0.0,
            max_length=20.0,  # Main rachis can grow long
            steps_since_branch=0,
            branch_side=1,
            vertex_id=None,
        ))
        level._fern_state = state
    return level._fern_state


def _reset_fern_state(level: "LevelState") -> None:
    """Reset fern state."""
    level._fern_state = None


# -----------------------------------------------------------------------------
# Growth Logic
# -----------------------------------------------------------------------------

def grow_tips(
    state: FernState,
    pattern,
    rng: random.Random,
    growth_amount: float = 0.5,
) -> int:
    """Grow all tips by a small amount.

    Args:
        state: The fern state
        pattern: The pattern to add vertices/edges to
        rng: Random number generator
        growth_amount: How much each tip grows per call

    Returns:
        Number of new vertices added.
    """
    if not pattern:
        return 0

    state.turn += 1
    added = 0
    new_tips: List[GrowthTip] = []

    for tip in state.tips:
        # Skip tips that have reached their max length
        if tip.length >= tip.max_length:
            continue

        # Apply curvature to angle
        tip.angle += tip.curvature

        # Calculate new position
        old_x, old_y = tip.x, tip.y
        dx = math.cos(tip.angle) * growth_amount
        dy = math.sin(tip.angle) * growth_amount
        new_x = old_x + dx
        new_y = old_y + dy

        # Ensure we have a vertex for the old position
        if tip.vertex_id is None:
            tip.vertex_id = pattern.add_vertex((old_x, old_y), color="green")
            added += 1

        # Add vertex for new position
        new_vertex_id = pattern.add_vertex((new_x, new_y), color="green")
        added += 1

        # Add edge from old to new
        pattern.add_edge(tip.vertex_id, new_vertex_id, color="green")

        # Update tip position
        tip.x = new_x
        tip.y = new_y
        tip.vertex_id = new_vertex_id
        tip.length += growth_amount
        tip.steps_since_branch += 1

        # Spawn pinnae from main rachis (generation 0)
        if tip.generation == 0:
            # Spawn pinnae regularly, alternating sides
            branch_interval = 3  # Steps between pinnae
            if tip.steps_since_branch >= branch_interval and tip.length > 2.0:
                tip.steps_since_branch = 0

                # Pinna angles off perpendicular to rachis, curving back
                # toward the tip of the frond
                pinna_base_angle = tip.angle + (math.pi / 2) * tip.branch_side
                # Curve back toward tip (opposite of branch side)
                pinna_curvature = -0.08 * tip.branch_side

                # Pinnae get shorter toward the tip of the frond
                progress = tip.length / tip.max_length
                pinna_length = 6.0 * (1.0 - progress * 0.7)

                new_tips.append(GrowthTip(
                    x=new_x, y=new_y,
                    angle=pinna_base_angle,
                    curvature=pinna_curvature,
                    generation=1,
                    length=0.0,
                    max_length=pinna_length,
                    steps_since_branch=0,
                    branch_side=tip.branch_side,  # Remember which side
                    vertex_id=new_vertex_id,
                ))

                # Alternate sides
                tip.branch_side *= -1

        # Spawn pinnules from pinnae (generation 1)
        elif tip.generation == 1:
            branch_interval = 2
            if tip.steps_since_branch >= branch_interval and tip.length > 1.0:
                tip.steps_since_branch = 0

                # Pinnules branch off the pinna, curving same direction
                pinnule_angle = tip.angle + (math.pi / 3) * tip.branch_side
                pinnule_curvature = tip.curvature * 0.5

                # Pinnules are short
                pinnule_length = 2.0 * (1.0 - tip.length / tip.max_length)

                if pinnule_length > 0.5:
                    new_tips.append(GrowthTip(
                        x=new_x, y=new_y,
                        angle=pinnule_angle,
                        curvature=pinnule_curvature,
                        generation=2,
                        length=0.0,
                        max_length=pinnule_length,
                        steps_since_branch=0,
                        branch_side=tip.branch_side,
                        vertex_id=new_vertex_id,
                    ))

                tip.branch_side *= -1

    # Add new branches to the tips list
    state.tips.extend(new_tips)

    # Remove exhausted tips (keeps memory bounded)
    state.tips = [t for t in state.tips if t.length < t.max_length]

    return added


# -----------------------------------------------------------------------------
# Pruning
# -----------------------------------------------------------------------------

def prune_oldest(level: "LevelState", count: int) -> int:
    """Prune the oldest vertices from the pattern.

    Returns number of vertices pruned.
    """
    pattern = level.pattern
    if not pattern or count <= 0:
        return 0

    actual = min(count, len(pattern.vertices))
    if actual <= 0:
        return 0

    # Remove edges referencing pruned vertices
    pattern.edges = [
        e for e in pattern.edges
        if e.a >= actual and e.b >= actual
    ]

    # Reindex remaining edges
    for e in pattern.edges:
        e.a -= actual
        e.b -= actual

    # Remove old vertices
    pattern.vertices = pattern.vertices[actual:]

    # Update tip vertex IDs in fern state
    state = getattr(level, "_fern_state", None)
    if state:
        for tip in state.tips:
            if tip.vertex_id is not None:
                tip.vertex_id -= actual
                if tip.vertex_id < 0:
                    tip.vertex_id = None

    return actual


# -----------------------------------------------------------------------------
# Coherence Payment
# -----------------------------------------------------------------------------

def _pay_coherence_for_growth(game: "Game", cost: float) -> bool:
    """Attempt to pay coherence for growth.

    Returns True if growth is allowed, False if insufficient coherence.
    """
    player = game._player()
    stats = player.stats

    # Stop growth below 20% coherence (safety buffer)
    min_coherence = int(stats.max_coherence * 0.2)
    if stats.coherence <= min_coherence:
        return False

    if stats.coherence < cost:
        return False

    stats.coherence = int(max(0, stats.coherence - cost))
    return True


# -----------------------------------------------------------------------------
# Main Tick Function
# -----------------------------------------------------------------------------

def tick(game: "Game", level: "LevelState", delta: int) -> None:
    """Process fern growth each tick.

    Called from advance_time() in scheduling.py.
    """
    if not getattr(level, "fern_active", False):
        return

    pattern = level.pattern
    if not pattern:
        level.fern_active = False
        return

    anchor = level.pattern_anchor
    if not anchor:
        level.fern_active = False
        return

    # Get parameters (with fallbacks)
    try:
        growth_rate = game._param_value("fern", "growth_rate") or 2
        max_vertices = game._param_value("fern", "max_vertices") or 100
        coherence_cost = game._param_value("fern", "coherence_cost") or 1.0
        prune_batch = game._param_value("fern", "prune_batch") or 1
    except Exception:
        growth_rate = 2
        max_vertices = 100
        coherence_cost = 1.0
        prune_batch = 1

    # Accumulate fractional growth
    level.fern_accum = getattr(level, "fern_accum", 0.0) + (delta * growth_rate / 20.0)

    # How many growth steps to run this tick
    steps_this_tick = int(level.fern_accum)
    if steps_this_tick <= 0:
        return

    level.fern_accum -= steps_this_tick

    # Check if we can pay coherence
    cost = coherence_cost * steps_this_tick * 0.5
    if not _pay_coherence_for_growth(game, float(cost)):
        return

    # Get fern state and RNG
    state = _get_fern_state(level)
    rng = getattr(game, "rng", random.Random())

    # Grow the fern
    for _ in range(steps_this_tick):
        grow_tips(state, pattern, rng, growth_amount=0.4)

    # Prune if over capacity
    current_verts = len(pattern.vertices)
    if current_verts > max_vertices:
        over = current_verts - max_vertices
        to_prune = min(prune_batch, over)
        prune_oldest(level, to_prune)
