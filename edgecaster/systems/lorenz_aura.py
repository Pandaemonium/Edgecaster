"""
Lorenz Aura System - Game-side integration for the Strange Attractor's butterfly storm.

This module manages the game integration for the Lorenz attractor visual effect:
- Contact damage to enemies overlapping the butterfly storm
- Zone transition handling (resetting the storm)
- has_lorenz_aura property logic

The core math (attractor simulation) lives in edgecaster/lorenz.py.
This module is the game-side orchestration.

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.
"""

from typing import Dict, List, Tuple, Optional, TYPE_CHECKING
import math

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState
    from edgecaster.state import Actor

from edgecaster import lorenz
from edgecaster.patterns import builder
from edgecaster.systems import damage_policy as damage_policy_system
from edgecaster.systems import entity_ops as entity_ops_system


# ---------------------------------------------------------------------------
# Lorenz State Initialization
# ---------------------------------------------------------------------------

def init_lorenz_state(game: "Game") -> None:
    """Initialize all Lorenz-related state on the game object.

    Called during Game.__init__ to set up the butterfly storm state.
    """
    game.lorenz_points: List[Tuple[float, float, float]] = []
    game.lorenz_center_x: Optional[float] = None
    game.lorenz_center_y: Optional[float] = None
    game._lorenz_prev_pos: Optional[Tuple[int, int]] = None
    game._lorenz_prev_zone: Tuple[int, int, int] = (0, 0, 0)
    game.lorenz_reset_trails: bool = False


# ---------------------------------------------------------------------------
# Lorenz Aura Check
# ---------------------------------------------------------------------------

def has_lorenz_aura(game: "Game") -> bool:
    """Check if the current character should have the Lorenz storm aura.

    Returns True if the character's class is "Strange Attractor".
    """
    return getattr(game.character, "player_class", None) == "Strange Attractor"


# ---------------------------------------------------------------------------
# Lorenz Advancement
# ---------------------------------------------------------------------------

def advance_lorenz(game: "Game", level: "LevelState", delta: int) -> None:
    """Advance the Lorenz aura for Strange Attractors.

    Args:
        game: The Game instance
        level: The current LevelState
        delta: Time delta in game ticks
    """
    if not has_lorenz_aura(game):
        # Keep all Lorenz state dormant/cleared for other classes
        game.lorenz_points = []
        game.lorenz_center_x = None
        game.lorenz_center_y = None
        game._lorenz_prev_pos = None
        game._lorenz_prev_zone = game.zone_coord
        return

    # First advance the continuous Lorenz dynamics (in lorenz.py)
    lorenz.advance_lorenz(game, level, delta)

    # Then apply contact damage from butterflies to nearby hostiles
    apply_contact_damage(game, level)

    # Clear reset flag after consumers have had a chance to react
    if game.lorenz_reset_trails:
        game.lorenz_reset_trails = False


# ---------------------------------------------------------------------------
# Contact Damage
# ---------------------------------------------------------------------------

# Match the renderer's projection parameters
LORENZ_ROTATION_ANGLE = math.radians(30.0)
LORENZ_SCALE = 0.18  # same as AsciiRenderer.lorenz_scale
LORENZ_RADIUS_TILES = 7  # same as AsciiRenderer.lorenz_radius_tiles

# Damage flavor text
DAMAGE_VERBS = [
    "cuts", "slices", "singes", "shocks", "jolts",
    "burns", "blinds", "chars", "sears"
]


def apply_contact_damage(game: "Game", level: "LevelState") -> None:
    """Apply 'butterfly' contact damage to hostiles overlapping the Lorenz storm.

    We mirror the renderer's projection:
    - Take (x, z) from each Lorenz point
    - Rotate in the (x, z) plane by 30 deg
    - Subtract a fixed 'natural' Lorenz center between the wings
    - Scale to tile offsets, clamp to a radius
    - Map to world tiles around lorenz_center_x/lorenz_center_y

    Args:
        game: The Game instance
        level: The current LevelState
    """
    points = getattr(game, "lorenz_points", None)
    if not points:
        return

    # Get storm center
    center_x = game.lorenz_center_x
    center_y = game.lorenz_center_y
    if center_x is None or center_y is None:
        player = game._player()
        center_x, center_y = player.pos

    # Project points to tile positions
    tile_hits = _project_to_tile_hits(points, center_x, center_y, level)
    if not tile_hits:
        return

    # Apply damage to hostile actors
    _apply_damage_to_hostiles(game, level, tile_hits)


def _project_to_tile_hits(
    points: List[Tuple[float, float, float]],
    center_x: float,
    center_y: float,
    level: "LevelState",
) -> Dict[Tuple[int, int], int]:
    """Project Lorenz points to tile positions and count hits per tile.

    Args:
        points: List of (x, y, z) Lorenz attractor points
        center_x: X center of storm in world tiles
        center_y: Y center of storm in world tiles
        level: LevelState for bounds checking

    Returns:
        Dict mapping tile positions to hit counts
    """
    cos_a = math.cos(LORENZ_ROTATION_ANGLE)
    sin_a = math.sin(LORENZ_ROTATION_ANGLE)

    # Project into a rotated 2D plane, track z band for the natural center
    points_2d: List[Tuple[float, float, float]] = []
    z_min = float("inf")
    z_max = float("-inf")

    for (x, y, z) in points:
        u = x
        v = z
        ux = cos_a * u - sin_a * v
        uy = sin_a * u + cos_a * v
        points_2d.append((ux, uy, z))
        if z < z_min:
            z_min = z
        if z > z_max:
            z_max = z

    if not points_2d:
        return {}

    if z_max <= z_min:
        z_max = z_min + 1e-6

    # Same 'natural' center as the renderer: between the wings
    z_mid = 0.5 * (z_min + z_max)
    x0 = 0.0
    natural_ux = cos_a * x0 - sin_a * z_mid
    natural_uy = sin_a * x0 + cos_a * z_mid

    # Map butterflies to tiles and count hits
    tile_hits: Dict[Tuple[int, int], int] = {}
    r2_max = float(LORENZ_RADIUS_TILES * LORENZ_RADIUS_TILES)

    for (ux, uy, z) in points_2d:
        rel_x = ux - natural_ux
        rel_y = uy - natural_uy
        dx = rel_x * LORENZ_SCALE
        dy = rel_y * LORENZ_SCALE

        if dx * dx + dy * dy > r2_max:
            continue

        tx = int(round(center_x + dx))
        ty = int(round(center_y + dy))

        if not level.world.in_bounds(tx, ty):
            continue

        key = (tx, ty)
        tile_hits[key] = tile_hits.get(key, 0) + 1

    return tile_hits


def _apply_damage_to_hostiles(
    game: "Game",
    level: "LevelState",
    tile_hits: Dict[Tuple[int, int], int],
) -> None:
    """Apply butterfly damage to hostile actors on affected tiles.

    Args:
        game: The Game instance
        level: The current LevelState
        tile_hits: Dict mapping tile positions to butterfly hit counts
    """
    base_damage = 1  # TODO: scale with stats later

    policy = damage_policy_system.DamagePolicy(
        include_self=False,
        include_hostile=True,
        include_neutral=False,
        include_friendly=False,
        include_environment=False,
    )
    for _tid, actor in damage_policy_system.iter_damage_targets(
        game,
        level,
        game.player_id,
        policy,
        include_actors=True,
        include_entities=False,
    ):
        if not getattr(actor, "alive", True):
            continue

        hits = tile_hits.get(actor.pos, 0)
        if hits <= 0:
            continue

        dmg = base_damage * hits
        if dmg <= 0:
            continue

        # Apply damage
        actor.stats.hp -= dmg
        actor.stats.clamp()

        # Log message
        verb = game.rng.choice(DAMAGE_VERBS)
        game.log.add(f"Your butterfly {verb} the {actor.name} for {dmg} damage.")

        # 50% chance to distract nearby foes
        if game.rng.random() < 0.5:
            _apply_distracted_status(game, actor)

        # Check for death
        if actor.stats.hp <= 0:
            game.log.add(f"{actor.name} dies.")
            game._kill_actor(level, actor, killer_id=game.player_id, killer_is_player=True)


def _apply_distracted_status(game: "Game", actor: "Actor") -> None:
    """Apply or refresh 'distracted' status on an actor.

    Args:
        game: The Game instance
        actor: The actor to distract
    """
    if not game._has_status(actor, "distracted"):
        game._add_status(
            actor,
            "distracted",
            duration=1,
            on_apply=f"The {actor.name} seems distracted by the butterflies."
        )
    else:
        # Refresh duration
        actor.statuses["distracted"] = max(actor.statuses.get("distracted", 0), 1)


# ---------------------------------------------------------------------------
# Zone Transition
# ---------------------------------------------------------------------------

def reset_on_zone_change(game: "Game", player: "Actor") -> None:
    """Hard-snap the Lorenz storm to the player when changing zones.

    Args:
        game: The Game instance
        player: The player actor
    """
    if not has_lorenz_aura(game):
        return

    # Clear all continuous state so we don't smear across zones
    game.lorenz_points = []
    game._lorenz_prev_pos = player.pos
    game._lorenz_prev_zone = game.zone_coord

    # Center the storm on the new player position immediately
    px, py = player.pos
    game.lorenz_center_x = float(px)
    game.lorenz_center_y = float(py)

    # Tell the renderer to nuke any old afterimage frames
    game.lorenz_reset_trails = True

    # Optionally seed fresh butterflies right away so you never get a "blank" frame
    lorenz.init_lorenz_points(game)

    # Clear any existing pattern when entering a zone and reset coherence
    lvl = game._level()
    lvl.pattern = builder.Pattern()
    lvl.pattern_anchor = None
    lvl.activation_points = []
    lvl.activation_ttl = 0
    entity_ops_system.remove_entities_by_kind(lvl, "aggressive_vines", "rune_choking_vines")
    lvl.acidic_pattern = False
    lvl.fern_active = False
    lvl.fern_growth_tips = []
    lvl.fern_accum = 0.0
    player.stats.coherence = player.stats.max_coherence
