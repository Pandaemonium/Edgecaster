"""POI Registry - Central management of yoga-compliant POI specs.

Provides semantic/content-state ownership for POIs plus compatibility lookup
helpers. When a shared ``SpatialIndex`` is attached, spatial reads should flow
through that index first so POI geometry is queried on the same surface as
attention/world proxies instead of maintaining a second read authority here.

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

    def __init__(self, *, zone_w: int = 60, zone_h: int = 40, spatial_index: object | None = None):
        """Initialize the registry.

        Args:
            zone_w: Width of a zone in tiles (for spatial indexing)
            zone_h: Height of a zone in tiles (for spatial indexing)
        """
        self.zone_w = zone_w
        self.zone_h = zone_h
        self.spatial_index = spatial_index

        # Primary storage by ID
        self._pois: Dict[str, POISpec] = {}
        self._spatial_ids: Dict[str, str] = {}

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
        self._mirror_poi_to_spatial_index(spec)

    def get(self, poi_id: str) -> Optional[POISpec]:
        """Get a POI by ID."""
        return self._pois.get(poi_id)

    def remove(self, poi_id: str) -> bool:
        """Remove a POI and update indices. Returns True if removed."""
        spec = self._pois.pop(poi_id, None)
        if spec:
            self._remove_spatial_mirror(str(poi_id))
            self._unindex_poi(spec)
            return True
        return False

    def attach_spatial_index(self, spatial_index: object | None) -> None:
        """Mirror existing and future POIs into the shared SpatialIndex."""
        old_index = self.spatial_index
        if old_index is not None:
            for spatial_id in list(self._spatial_ids.values()):
                try:
                    old_index.remove(spatial_id, source="poi_registry")
                except Exception:
                    pass
        self._spatial_ids.clear()
        self.spatial_index = spatial_index
        if spatial_index is None:
            return
        for spec in list(self._pois.values()):
            self._mirror_poi_to_spatial_index(spec)

    def __contains__(self, poi_id: str) -> bool:
        return poi_id in self._pois

    def __iter__(self) -> Iterator[POISpec]:
        return iter(self._pois.values())

    def __len__(self) -> int:
        return len(self._pois)

    # =========================================================================
    # Spatial Queries
    # =========================================================================

    def _query_spatial_index_rect(
        self,
        rect: ABSRect,
        *,
        depth: int,
    ) -> List[POISpec]:
        """Return POIs overlapping *rect* via the shared SpatialIndex."""
        if self.spatial_index is None:
            return []
        try:
            entries = self.spatial_index.query_rect(
                (
                    float(rect.x0),
                    float(rect.y0),
                    float(rect.x1),
                    float(rect.y1),
                ),
                int(depth),
                source="poi_registry",
            )
        except Exception:
            return []

        hit_ids: Set[str] = set()
        for entry in entries:
            poi = entry.obj if isinstance(entry.obj, POISpec) else None
            if poi is None:
                poi = self._pois.get(str(getattr(entry.obj, "id", "") or entry.entity_id))
            if poi is None:
                continue
            try:
                if int(getattr(poi, "depth", 0) or 0) != int(depth):
                    continue
            except Exception:
                continue
            try:
                if not poi.footprint.overlaps(rect):
                    continue
            except Exception:
                continue
            hit_ids.add(str(poi.id))

        if not hit_ids:
            return []
        return [poi for poi in self._pois.values() if poi.id in hit_ids]

    def get_at_zone(
        self, zx: int, zy: int, depth: int = 0
    ) -> List[POISpec]:
        """Get all POIs whose footprints overlap a zone coordinate."""
        zone_rect = ABSRect.from_zone_coord(int(zx), int(zy), self.zone_w, self.zone_h)
        return self._query_spatial_index_rect(zone_rect, depth=int(depth))

    def get_in_abs_rect(
        self, rect: ABSRect, depth: int = 0
    ) -> List[POISpec]:
        """Get all POIs whose footprints overlap the given ABS rect."""
        return self._query_spatial_index_rect(rect, depth=int(depth))

    def get_at_abs_point(
        self, x: int, y: int, depth: int = 0
    ) -> List[POISpec]:
        """Get all POIs containing an absolute point."""
        rect = ABSRect(x0=int(x), y0=int(y), x1=int(x) + 1, y1=int(y) + 1)
        hits = self._query_spatial_index_rect(rect, depth=int(depth))
        return [poi for poi in hits if poi.footprint.contains_point(int(x), int(y))]

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

    def _remove_spatial_mirror(self, poi_id: str) -> None:
        spatial_id = self._spatial_ids.pop(str(poi_id), None)
        if spatial_id and self.spatial_index is not None:
            try:
                self.spatial_index.remove(spatial_id, source="poi_registry")
            except Exception:
                pass

    def _mirror_poi_to_spatial_index(self, spec: POISpec) -> None:
        if self.spatial_index is None:
            return
        try:
            rect = (
                float(spec.footprint.x0),
                float(spec.footprint.y0),
                float(spec.footprint.x1),
                float(spec.footprint.y1),
            )
            spatial_id = self.spatial_index.add_or_update(
                spec,
                rect,
                int(spec.depth),
                "collapsed",
                semantic_id=str(spec.id),
                kind=str(spec.kind),
                source="poi_registry",
            )
            self._spatial_ids[str(spec.id)] = str(spatial_id)
        except Exception:
            pass

    def _index_poi(self, spec: POISpec) -> None:
        """Add POI to all indices."""
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
