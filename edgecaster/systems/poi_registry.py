"""POI Registry - Central management of yoga-compliant POIs.

Provides efficient lookup by ID, zone coordinate, or spatial rect.
Supports multi-zone POIs, nesting hierarchies, and content state tracking.

Yoga principles:
- POIs exist in ABS space independent of zone loading
- Querying does not trigger gameplay side effects
- Content state (killed NPCs, removed items) persists across zone loads
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Set, Tuple, TYPE_CHECKING

from edgecaster.state.pois import ABSRect, POISpec, POIContentState

if TYPE_CHECKING:
    from edgecaster.game import Game


class POIRegistry:
    """Central registry for all POIs with multi-index lookup.

    Invariants:
    - POIs exist in ABS space independent of zone loading
    - Querying does not trigger gameplay side effects
    - Supports efficient spatial queries
    - Tracks content state for persistence
    """

    def __init__(self, *, zone_w: int = 60, zone_h: int = 40):
        """Initialize the registry.

        Args:
            zone_w: Width of a zone in tiles (for spatial indexing)
            zone_h: Height of a zone in tiles (for spatial indexing)
        """
        self.zone_w = zone_w
        self.zone_h = zone_h

        # Primary storage by ID
        self._pois: Dict[str, POISpec] = {}

        # Spatial indices
        # (zx, zy, depth) -> list of POI IDs that overlap this zone
        self._by_zone: Dict[Tuple[int, int, int], List[str]] = {}
        # (zx, zy) -> list of POI IDs (any depth)
        self._by_zone_2d: Dict[Tuple[int, int], List[str]] = {}

        # Hierarchy indices
        self._by_parent: Dict[str, List[str]] = {}  # parent_id -> child_ids
        self._roots: Set[str] = set()  # POIs with no parent

        # Kind index
        self._by_kind: Dict[str, List[str]] = {}  # kind -> poi_ids

        # Content state (persisted)
        self._content_state: Dict[str, POIContentState] = {}

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    def add(self, spec: POISpec) -> None:
        """Add a POI to the registry."""
        if spec.id in self._pois:
            # Remove old version first
            self.remove(spec.id)

        self._pois[spec.id] = spec
        self._index_poi(spec)

    def get(self, poi_id: str) -> Optional[POISpec]:
        """Get a POI by ID."""
        return self._pois.get(poi_id)

    def remove(self, poi_id: str) -> bool:
        """Remove a POI and update indices. Returns True if removed."""
        spec = self._pois.pop(poi_id, None)
        if spec:
            self._unindex_poi(spec)
            return True
        return False

    def __contains__(self, poi_id: str) -> bool:
        return poi_id in self._pois

    def __iter__(self) -> Iterator[POISpec]:
        return iter(self._pois.values())

    def __len__(self) -> int:
        return len(self._pois)

    # =========================================================================
    # Spatial Queries
    # =========================================================================

    def get_at_zone(
        self, zx: int, zy: int, depth: int = 0
    ) -> List[POISpec]:
        """Get all POIs whose footprints overlap a zone coordinate."""
        ids = self._by_zone.get((zx, zy, depth), [])
        return [self._pois[pid] for pid in ids if pid in self._pois]

    def get_at_zone_any_depth(self, zx: int, zy: int) -> List[POISpec]:
        """Get all POIs at a zone coordinate (any depth)."""
        ids = self._by_zone_2d.get((zx, zy), [])
        return [self._pois[pid] for pid in ids if pid in self._pois]

    def get_in_abs_rect(
        self, rect: ABSRect, depth: int = 0
    ) -> List[POISpec]:
        """Get all POIs whose footprints overlap the given ABS rect."""
        result: List[POISpec] = []
        zones = rect.zone_coords(self.zone_w, self.zone_h)
        seen: Set[str] = set()

        for zx, zy in zones:
            for pid in self._by_zone.get((zx, zy, depth), []):
                if pid not in seen:
                    seen.add(pid)
                    poi = self._pois.get(pid)
                    if poi and poi.footprint.overlaps(rect):
                        result.append(poi)

        return result

    def get_at_abs_point(
        self, x: int, y: int, depth: int = 0
    ) -> List[POISpec]:
        """Get all POIs containing an absolute point."""
        zx = x // self.zone_w
        zy = y // self.zone_h
        result: List[POISpec] = []

        for pid in self._by_zone.get((zx, zy, depth), []):
            poi = self._pois.get(pid)
            if poi and poi.footprint.contains_point(x, y):
                result.append(poi)

        return result

    # =========================================================================
    # Hierarchy Queries
    # =========================================================================

    def get_children(self, poi_id: str) -> List[POISpec]:
        """Get direct children of a POI."""
        ids = self._by_parent.get(poi_id, [])
        return [self._pois[pid] for pid in ids if pid in self._pois]

    def get_descendants(self, poi_id: str) -> List[POISpec]:
        """Get all descendants (recursive) of a POI."""
        result: List[POISpec] = []
        stack = list(self._by_parent.get(poi_id, []))

        while stack:
            cid = stack.pop()
            if cid in self._pois:
                result.append(self._pois[cid])
                stack.extend(self._by_parent.get(cid, []))

        return result

    def get_ancestors(self, poi_id: str) -> List[POISpec]:
        """Get all ancestors (root first) of a POI."""
        result: List[POISpec] = []
        current = self._pois.get(poi_id)

        while current and current.parent_id:
            parent = self._pois.get(current.parent_id)
            if parent:
                result.insert(0, parent)
            current = parent

        return result

    def get_root_pois(self) -> List[POISpec]:
        """Get all top-level POIs (no parent)."""
        return [self._pois[pid] for pid in self._roots if pid in self._pois]

    # =========================================================================
    # Kind Queries
    # =========================================================================

    def get_by_kind(self, kind: str) -> List[POISpec]:
        """Get all POIs of a specific kind."""
        ids = self._by_kind.get(kind, [])
        return [self._pois[pid] for pid in ids if pid in self._pois]

    # =========================================================================
    # Content State Management
    # =========================================================================

    def get_content_state(self, poi_id: str) -> POIContentState:
        """Get or create content state for a POI."""
        if poi_id not in self._content_state:
            self._content_state[poi_id] = POIContentState(poi_id=poi_id)
        return self._content_state[poi_id]

    def mark_npc_spawned(
        self, poi_id: str, spec_key: str, entity_id: str
    ) -> None:
        """Record that an NPC was spawned for this POI."""
        state = self.get_content_state(poi_id)
        state.spawned_npcs[spec_key] = entity_id

    def mark_npc_killed(self, poi_id: str, entity_id: str) -> None:
        """Record that an NPC in this POI was killed."""
        state = self.get_content_state(poi_id)
        state.killed_npcs.add(entity_id)

    def mark_item_removed(self, poi_id: str, entity_id: str) -> None:
        """Record that an item in this POI was taken/destroyed."""
        state = self.get_content_state(poi_id)
        state.removed_items.add(entity_id)

    def mark_visited(self, poi_id: str, tick: Optional[int] = None) -> None:
        """Mark a POI as visited."""
        state = self.get_content_state(poi_id)
        state.visited = True
        if state.first_visited_tick is None:
            state.first_visited_tick = tick

    def mark_cleared(self, poi_id: str) -> None:
        """Mark a POI as cleared."""
        state = self.get_content_state(poi_id)
        state.cleared = True

    def is_npc_spawned(self, poi_id: str, spec_key: str) -> bool:
        """Check if an NPC spec has been spawned."""
        state = self._content_state.get(poi_id)
        return state is not None and spec_key in state.spawned_npcs

    def is_npc_dead(self, poi_id: str, spec_key: str) -> bool:
        """Check if a spawned NPC is dead."""
        state = self._content_state.get(poi_id)
        if not state:
            return False
        entity_id = state.spawned_npcs.get(spec_key)
        return entity_id is not None and entity_id in state.killed_npcs

    # =========================================================================
    # Realization Tracking
    # =========================================================================

    def mark_zone_realized(self, poi_id: str, zx: int, zy: int) -> None:
        """Mark that this POI's contents have been realized for a zone."""
        poi = self._pois.get(poi_id)
        if poi:
            poi.realized_zones.add((zx, zy))

    def is_zone_realized(self, poi_id: str, zx: int, zy: int) -> bool:
        """Check if a POI's contents are realized for a zone."""
        poi = self._pois.get(poi_id)
        return poi is not None and (zx, zy) in poi.realized_zones

    def get_unrealized_zones(self, poi_id: str) -> Set[Tuple[int, int]]:
        """Get zones that overlap this POI but haven't been realized."""
        poi = self._pois.get(poi_id)
        if not poi:
            return set()
        all_zones = poi.get_zone_coords(self.zone_w, self.zone_h)
        return all_zones - poi.realized_zones

    # =========================================================================
    # Serialization
    # =========================================================================

    def to_save_data(self) -> Dict[str, Any]:
        """Serialize registry to save data."""
        return {
            "pois": [self._serialize_poi(p) for p in self._pois.values()],
            "content_state": {
                k: self._serialize_content_state(v)
                for k, v in self._content_state.items()
            },
        }

    @classmethod
    def from_save_data(
        cls,
        data: Dict[str, Any],
        *,
        zone_w: int = 60,
        zone_h: int = 40,
    ) -> "POIRegistry":
        """Deserialize registry from save data."""
        registry = cls(zone_w=zone_w, zone_h=zone_h)

        for poi_data in data.get("pois", []):
            try:
                spec = registry._deserialize_poi(poi_data)
                registry.add(spec)
            except (KeyError, ValueError, TypeError):
                continue

        for poi_id, state_data in data.get("content_state", {}).items():
            try:
                state = registry._deserialize_content_state(state_data)
                registry._content_state[poi_id] = state
            except (KeyError, ValueError, TypeError):
                continue

        return registry

    def _serialize_poi(self, poi: POISpec) -> Dict[str, Any]:
        """Serialize a single POI."""
        return {
            "id": poi.id,
            "kind": poi.kind,
            "name": poi.name,
            "footprint": {
                "x0": poi.footprint.x0,
                "y0": poi.footprint.y0,
                "x1": poi.footprint.x1,
                "y1": poi.footprint.y1,
            },
            "depth": poi.depth,
            "anchor_abs": list(poi.anchor_abs),
            "parent_id": poi.parent_id,
            "child_ids": list(poi.child_ids),
            "seed": poi.seed,
            "tags": poi.tags,
            "realized_zones": [list(z) for z in poi.realized_zones],
        }

    def _deserialize_poi(self, data: Dict[str, Any]) -> POISpec:
        """Deserialize a single POI."""
        fp = data["footprint"]
        return POISpec(
            id=data["id"],
            kind=data["kind"],
            name=data["name"],
            footprint=ABSRect(
                x0=fp["x0"],
                y0=fp["y0"],
                x1=fp["x1"],
                y1=fp["y1"],
            ),
            depth=data.get("depth", 0),
            anchor_abs=tuple(data.get("anchor_abs", (0, 0))),
            parent_id=data.get("parent_id"),
            child_ids=data.get("child_ids", []),
            seed=data.get("seed", 0),
            tags=data.get("tags", {}),
            realized_zones={tuple(z) for z in data.get("realized_zones", [])},
        )

    def _serialize_content_state(self, state: POIContentState) -> Dict[str, Any]:
        """Serialize content state."""
        return {
            "poi_id": state.poi_id,
            "spawned_npcs": state.spawned_npcs,
            "spawned_structures": state.spawned_structures,
            "spawned_entities": state.spawned_entities,
            "killed_npcs": list(state.killed_npcs),
            "removed_items": list(state.removed_items),
            "modified_structures": state.modified_structures,
            "cleared": state.cleared,
            "visited": state.visited,
            "first_visited_tick": state.first_visited_tick,
        }

    def _deserialize_content_state(self, data: Dict[str, Any]) -> POIContentState:
        """Deserialize content state."""
        return POIContentState(
            poi_id=data["poi_id"],
            spawned_npcs=data.get("spawned_npcs", {}),
            spawned_structures=data.get("spawned_structures", {}),
            spawned_entities=data.get("spawned_entities", {}),
            killed_npcs=set(data.get("killed_npcs", [])),
            removed_items=set(data.get("removed_items", [])),
            modified_structures=data.get("modified_structures", {}),
            cleared=data.get("cleared", False),
            visited=data.get("visited", False),
            first_visited_tick=data.get("first_visited_tick"),
        )

    # =========================================================================
    # Internal Indexing
    # =========================================================================

    def _index_poi(self, spec: POISpec) -> None:
        """Add POI to all indices."""
        # Spatial index by zone
        zones = spec.get_zone_coords(self.zone_w, self.zone_h)

        # Debug logging for POI indexing (only for multi-zone POIs to reduce spam)
        if len(zones) > 1:
            try:
                with open("C:/Games/Edgecaster/debug.log", "a") as f:
                    f.write(f"[POIRegistry] Indexing multi-zone POI '{spec.id}' ({spec.kind}): zones={sorted(zones)}, footprint=({spec.footprint.x0}, {spec.footprint.y0})-({spec.footprint.x1}, {spec.footprint.y1})\n")
            except Exception:
                pass

        for zx, zy in zones:
            key_3d = (zx, zy, spec.depth)
            if key_3d not in self._by_zone:
                self._by_zone[key_3d] = []
            if spec.id not in self._by_zone[key_3d]:
                self._by_zone[key_3d].append(spec.id)

            key_2d = (zx, zy)
            if key_2d not in self._by_zone_2d:
                self._by_zone_2d[key_2d] = []
            if spec.id not in self._by_zone_2d[key_2d]:
                self._by_zone_2d[key_2d].append(spec.id)

        # Hierarchy index
        if spec.parent_id:
            if spec.parent_id not in self._by_parent:
                self._by_parent[spec.parent_id] = []
            if spec.id not in self._by_parent[spec.parent_id]:
                self._by_parent[spec.parent_id].append(spec.id)
        else:
            self._roots.add(spec.id)

        # Kind index
        if spec.kind not in self._by_kind:
            self._by_kind[spec.kind] = []
        if spec.id not in self._by_kind[spec.kind]:
            self._by_kind[spec.kind].append(spec.id)

    def _unindex_poi(self, spec: POISpec) -> None:
        """Remove POI from all indices."""
        # Spatial indices
        zones = spec.get_zone_coords(self.zone_w, self.zone_h)
        for zx, zy in zones:
            key_3d = (zx, zy, spec.depth)
            if key_3d in self._by_zone:
                try:
                    self._by_zone[key_3d].remove(spec.id)
                except ValueError:
                    pass

            key_2d = (zx, zy)
            if key_2d in self._by_zone_2d:
                try:
                    self._by_zone_2d[key_2d].remove(spec.id)
                except ValueError:
                    pass

        # Hierarchy index
        if spec.parent_id and spec.parent_id in self._by_parent:
            try:
                self._by_parent[spec.parent_id].remove(spec.id)
            except ValueError:
                pass

        self._roots.discard(spec.id)

        # Kind index
        if spec.kind in self._by_kind:
            try:
                self._by_kind[spec.kind].remove(spec.id)
            except ValueError:
                pass
