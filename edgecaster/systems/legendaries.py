"""
Legendaries System - Legendary creature registry and POI discovery.

This module manages:
- Generation of legendary creatures with unique names and boosted stats
- Allocation of lair POIs for legendaries
- POI rumor/discovery tracking
- Queries for nearest legendary lairs

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.
"""

from typing import Dict, List, Tuple, Optional, Set, TYPE_CHECKING
import random

if TYPE_CHECKING:
    from edgecaster.game import Game, LevelState

from edgecaster.content import pois as poi_content
from edgecaster import prototypes


# ---------------------------------------------------------------------------
# Legendary Names (simple pool for now)
# ---------------------------------------------------------------------------

LEGENDARY_NAMES = [
    "Akhraz", "Velis", "Khor", "Sable", "Orun",
    "Nym", "Kesh", "Mora", "Zarith", "Iolan",
    "Riven", "Seph", "Vox", "Cael", "Thorn",
    "Ash", "Nyx", "Rook", "Iris", "Maven",
]


# ---------------------------------------------------------------------------
# POI ID Allocation
# ---------------------------------------------------------------------------

def alloc_legendary_lair_poi_id() -> str:
    """Return a unique POI id for a newly-generated legendary lair."""
    i = 0
    while True:
        pid = f"legendary_lair_{i:03d}"
        if pid not in poi_content.POIS:
            return pid
        i += 1


def alloc_rune_anchor_poi_id() -> str:
    """Return a unique POI id for a rune anchor."""
    i = 0
    while True:
        pid = f"rune_anchor_{i:03d}"
        if pid not in poi_content.POIS:
            return pid
        i += 1


# ---------------------------------------------------------------------------
# Legendary Initialization
# ---------------------------------------------------------------------------

def init_legendaries(
    game: "Game",
    count: int = 50,
    rng: Optional[random.Random] = None,
) -> Dict[str, dict]:
    """Generate a set of named legendary creatures and inject their lair POIs.

    Args:
        game: The Game instance (for config, enemy templates, etc.)
        count: Number of legendaries to generate
        rng: Random number generator (defaults to game.rng)

    Returns:
        Dict mapping legendary_id to legendary info dict

    Phase 1:
    - Legends are stronger versions of existing hostile templates.
    - Each gets its own lair POI on the overworld (depth 0).
    - Lair markers are hidden on the world map until discovered/rumored
      (handled by the POI discovery system).
    """
    if rng is None:
        rng = getattr(game, "rng", random.Random())

    legendary_registry: Dict[str, dict] = {}

    # Get available enemy templates
    try:
        base_templates = list(game._enemy_template_ids())
    except Exception:
        base_templates = []

    if not base_templates:
        return legendary_registry

    # Avoid placing lairs on top of existing POIs
    reserved_coords: Set[Tuple[int, int, int]] = {
        tuple(poi.coord) for poi in poi_content.POIS.values()
    }
    reserved_coords.add(tuple(getattr(game, "zone_coord", (0, 0, 0))))

    used_names: Set[str] = set()

    screens = int(getattr(game.cfg, "world_map_screens", 1) or 1)
    screens = max(1, screens)

    created = 0
    attempts = 0
    max_attempts = max(5000, int(count) * 500)

    while created < int(count) and attempts < max_attempts:
        attempts += 1
        zx = int(rng.randrange(0, screens))
        zy = int(rng.randrange(0, screens))
        coord = (zx, zy, 0)

        if coord in reserved_coords:
            continue

        base_proto = str(rng.choice(base_templates))
        try:
            base_spec = prototypes.resolve_proto(base_proto) or {}
            base_name = str(base_spec.get("name") or base_proto)
        except Exception:
            base_name = base_proto

        # Unique-ish legendary name
        raw = str(rng.choice(LEGENDARY_NAMES))
        full_name = f"{raw}, Legendary {base_name}"
        if full_name in used_names:
            full_name = f"{raw} {created + 1}, Legendary {base_name}"
        used_names.add(full_name)

        # Power boost (HP only for now)
        hp_mult = float(rng.uniform(3.0, 6.0))

        reserved_coords.add(coord)
        poi_id = alloc_legendary_lair_poi_id()
        legendary_id = f"legendary_{created:03d}"

        try:
            poi_content.POIS[poi_id] = poi_content.POI(
                id=poi_id,
                coord=coord,
                npcs=[],
                structures=[
                    {
                        "kind": "legendary_lair",
                        "legendary_id": legendary_id,
                        "template_id": base_proto,
                        "name": full_name,
                        "hp_mult": hp_mult,
                    }
                ],
            )
        except Exception:
            continue

        legendary_registry[legendary_id] = {
            "poi_id": poi_id,
            "coord": coord,
            "template_id": base_proto,
            "name": full_name,
            "hp_mult": hp_mult,
        }
        created += 1

    return legendary_registry


# ---------------------------------------------------------------------------
# POI Rumor/Discovery
# ---------------------------------------------------------------------------

def add_poi_rumor(
    game: "Game",
    poi_id: str,
    *,
    log: bool = True,
) -> None:
    """Mark a POI as rumored so it appears on the world map before discovery.

    Args:
        game: The Game instance
        poi_id: ID of the POI to rumor
        log: Whether to add a message to the game log
    """
    poi_id = str(poi_id)

    # Ensure sets exist
    if not hasattr(game, "rumored_pois"):
        game.rumored_pois = set()
    if not hasattr(game, "discovered_pois"):
        game.discovered_pois = set()

    # Already discovered = no need to rumor
    if poi_id in game.discovered_pois:
        return

    game.rumored_pois.add(poi_id)

    if not log:
        return

    poi = poi_content.POIS.get(poi_id)
    if poi:
        zx, zy, _ = poi.coord
        game.log.add(f"You hear rumors of {poi_id.replace('_', ' ')} at ({zx}, {zy}).")
    else:
        game.log.add(f"You hear rumors of {poi_id.replace('_', ' ')}.")


def discover_pois_for_level(game: "Game", level: "LevelState") -> None:
    """Record POIs attached to this level as discovered (reveals markers).

    Args:
        game: The Game instance
        level: The LevelState to check for POIs
    """
    if not hasattr(game, "discovered_pois"):
        game.discovered_pois = set()
    if not hasattr(game, "rumored_pois"):
        game.rumored_pois = set()

    poi_ids = getattr(level.world, "poi_ids", None)
    if not poi_ids:
        return

    for pid in list(poi_ids):
        game.discovered_pois.add(str(pid))
        game.rumored_pois.discard(str(pid))


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def get_nearest_legendary_lairs(
    game: "Game",
    n: int = 5,
    *,
    from_coord: Optional[Tuple[int, int, int]] = None,
) -> List[Tuple[str, Tuple[int, int, int]]]:
    """Return the N nearest legendary lair POIs (by overworld zone distance).

    Args:
        game: The Game instance
        n: Number of results to return
        from_coord: Origin coordinate (defaults to current zone)

    Returns:
        List of (poi_id, coord) tuples sorted by distance
    """
    try:
        n = int(n)
    except Exception:
        n = 5

    if n <= 0:
        return []

    origin = from_coord or getattr(game, "zone_coord", (0, 0, 0))
    try:
        ox, oy = int(origin[0]), int(origin[1])
    except Exception:
        ox, oy = 0, 0

    # Build location cache if needed
    locations = getattr(game, "poi_locations", None)
    if not locations:
        try:
            locations = {pid: tuple(poi.coord) for pid, poi in poi_content.POIS.items()}
        except Exception:
            locations = {}

    scored: List[Tuple[int, str, Tuple[int, int, int]]] = []
    for pid, coord in (locations or {}).items():
        pid_s = str(pid)
        if not pid_s.startswith("legendary_lair_"):
            continue
        try:
            zx, zy, zz = int(coord[0]), int(coord[1]), int(coord[2])
        except Exception:
            continue
        d2 = (zx - ox) * (zx - ox) + (zy - oy) * (zy - oy)
        scored.append((d2, pid_s, (zx, zy, zz)))

    scored.sort(key=lambda t: (t[0], t[1]))
    return [(pid, coord) for _, pid, coord in scored[:n]]
