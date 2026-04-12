"""Authoritative entity containment graph.

EntityGraphStore is the write-first authority for parent/child containment
relationships between runtime entities. The six parallel stores on Game
(LevelState.actors, LevelState.entities, game.inventories, game.attn_store,
game.world_entity_index, game.poi_registry) remain as caches and compatibility
facades while migration proceeds; this graph is where containment and identity
relationships are definitively recorded.

[ENTITY_CHAKRA][PHASE_2C] initial substrate
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set


@dataclass
class EntityGraphNode:
    """Single node in the entity containment graph.

    entity_id is the primary key and should match the runtime entity.id (or
    entity.entity_id when populated). parent_entity_id/socket_id record
    containment; semantic_id is the optional stable content-addressable name.

    dirty: True when this node's channel state has changed since the last
    reducer pass.  The channel reducer skips clean subtrees (dirty=False with
    no dirty descendants) so only modified paths are re-reduced each tick.
    Call EntityGraphStore.mark_dirty_up(entity_id) after any channel mutation
    to propagate the dirty flag up to the root.
    """

    entity_id: str
    semantic_id: Optional[str] = None
    parent_entity_id: Optional[str] = None
    socket_id: Optional[str] = None
    kind: str = "generic"
    lod_state: str = "expanded"
    layout_id: Optional[str] = None
    rule_id: Optional[str] = None
    tags: Dict[str, Any] = field(default_factory=dict)
    dirty: bool = True  # New nodes start dirty so the first reduction pass includes them.


class EntityGraphStore:
    """Authoritative containment graph for runtime entities.

    Stores containment edges (parent → children via socket) and provides
    fast lookup by entity_id and semantic_id. Maintains two derived indexes:

      - children_by_parent: Dict[parent_id, Set[child_id]]
      - semantic_index:     Dict[semantic_id, entity_id]

    These are kept in sync by every write operation. Callers should write
    through the register/deregister/reparent helpers rather than mutating
    .nodes directly.

    Unification note: attach_entity_to_parent and detach_entity_from_parent in
    entity_graph_ops.py write into this store alongside legacy attribute
    patching. Future phases will replace parallel-store queries with graph
    queries and eventually delete the legacy bridge.
    """

    def __init__(self) -> None:
        # Primary index: entity_id → node
        self.nodes: Dict[str, EntityGraphNode] = {}
        # Derived index: parent_entity_id → set of child entity_ids
        self.children_by_parent: Dict[str, Set[str]] = {}
        # Derived index: semantic_id → entity_id (last-write wins)
        self.semantic_index: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def register(
        self,
        entity_id: str,
        *,
        semantic_id: Optional[str] = None,
        parent_entity_id: Optional[str] = None,
        socket_id: Optional[str] = None,
        kind: str = "generic",
        lod_state: Optional[str] = None,
        layout_id: Optional[str] = None,
        rule_id: Optional[str] = None,
        tags: Optional[Dict[str, Any]] = None,
    ) -> EntityGraphNode:
        """Register or update a node in the graph.

        Idempotent: calling again with a known entity_id updates the node
        and keeps derived indexes consistent. Returns the node.
        """
        eid = str(entity_id)
        existing = self.nodes.get(eid)
        next_semantic_id = str(semantic_id) if semantic_id else None
        next_layout_id = str(layout_id) if layout_id else None
        next_rule_id = str(rule_id) if rule_id else None
        next_lod_state = str(lod_state or "").strip() or "expanded"
        next_tags = dict(tags or {})

        # Remove stale parent-index entry when reparenting.
        if existing is not None and existing.parent_entity_id != parent_entity_id:
            self._remove_from_parent_index(eid, existing.parent_entity_id)

        # Remove stale semantic-index entry when semantic_id changes.
        if existing is not None and next_semantic_id is None:
            next_semantic_id = existing.semantic_id
        if existing is not None and next_layout_id is None:
            next_layout_id = existing.layout_id
        if existing is not None and next_rule_id is None:
            next_rule_id = existing.rule_id
        if existing is not None and (not str(lod_state or "").strip()):
            next_lod_state = str(existing.lod_state or "expanded")
        if existing is not None and not next_tags:
            next_tags = dict(existing.tags or {})

        if existing is not None and existing.semantic_id and existing.semantic_id != next_semantic_id:
            if self.semantic_index.get(existing.semantic_id) == eid:
                self.semantic_index.pop(existing.semantic_id, None)

        # Preserve dirty flag when re-registering an existing node that was
        # already marked dirty; a clean re-registration should not lose dirtiness.
        next_dirty = True if existing is None else bool(getattr(existing, "dirty", True))

        node = EntityGraphNode(
            entity_id=eid,
            semantic_id=next_semantic_id,
            parent_entity_id=str(parent_entity_id) if parent_entity_id else None,
            socket_id=str(socket_id) if socket_id else None,
            kind=str(kind or "generic"),
            lod_state=next_lod_state,
            layout_id=next_layout_id,
            rule_id=next_rule_id,
            tags=next_tags,
            dirty=next_dirty,
        )
        self.nodes[eid] = node

        # Rebuild derived indexes.
        if node.parent_entity_id:
            self.children_by_parent.setdefault(node.parent_entity_id, set()).add(eid)
        if node.semantic_id:
            self.semantic_index[node.semantic_id] = eid

        return node

    def deregister(self, entity_id: str) -> None:
        """Remove a node and clean up all derived indexes.

        Children of the removed node are orphaned (parent_entity_id set to
        None) rather than cascade-deleted; they remain valid graph nodes.
        """
        eid = str(entity_id)
        node = self.nodes.pop(eid, None)
        if node is None:
            return

        self._remove_from_parent_index(eid, node.parent_entity_id)

        if node.semantic_id:
            if self.semantic_index.get(node.semantic_id) == eid:
                self.semantic_index.pop(node.semantic_id, None)

        # Orphan any direct children without cascading the deregister.
        children = self.children_by_parent.pop(eid, None)
        if children:
            for child_id in children:
                child = self.nodes.get(child_id)
                if child is not None and child.parent_entity_id == eid:
                    child.parent_entity_id = None
                    child.socket_id = None

    def reparent(
        self,
        entity_id: str,
        new_parent_id: Optional[str],
        new_socket_id: Optional[str] = None,
    ) -> None:
        """Move a node to a new parent, or detach if new_parent_id is None."""
        eid = str(entity_id)
        node = self.nodes.get(eid)
        if node is None:
            return
        self._remove_from_parent_index(eid, node.parent_entity_id)
        node.parent_entity_id = str(new_parent_id) if new_parent_id else None
        node.socket_id = str(new_socket_id) if new_socket_id else None
        if node.parent_entity_id:
            self.children_by_parent.setdefault(node.parent_entity_id, set()).add(eid)

    # ------------------------------------------------------------------
    # Dirty-flag propagation (R2)
    # ------------------------------------------------------------------

    def mark_dirty_up(self, entity_id: str, *, max_depth: int = 64) -> None:
        """Mark entity_id and all its ancestors as dirty.

        Call this after any channel mutation on a node so the reducer knows
        which subtrees to re-process.  Walks up through parent_entity_id links
        until reaching a root (no parent) or max_depth is exceeded.
        """
        eid = str(entity_id)
        visited: Set[str] = set()
        depth = 0
        while eid and depth < max_depth:
            if eid in visited:
                break  # Cycle guard.
            visited.add(eid)
            node = self.nodes.get(eid)
            if node is None:
                break
            node.dirty = True
            eid = str(node.parent_entity_id or "")
            depth += 1

    def mark_clean(self, entity_id: str) -> None:
        """Clear the dirty flag for a single node.

        Call this after the reducer has processed a node and its result has
        been committed.  Does not propagate to ancestors or descendants.
        """
        node = self.nodes.get(str(entity_id))
        if node is not None:
            node.dirty = False

    def mark_subtree_clean(self, root_entity_id: str, *, max_depth: int = 64) -> None:
        """Clear dirty flags for root_entity_id and all its descendants.

        Used by the reducer to mark a processed subtree as clean after a full
        reduction pass.
        """
        queue = [str(root_entity_id)]
        visited: Set[str] = set()
        depth = 0
        while queue and depth < max_depth:
            eid = queue.pop(0)
            if eid in visited:
                continue
            visited.add(eid)
            node = self.nodes.get(eid)
            if node is not None:
                node.dirty = False
            children = self.children_by_parent.get(eid)
            if children:
                queue.extend(sorted(children))
            depth += 1

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_node(self, entity_id: str) -> Optional[EntityGraphNode]:
        """Return the node for entity_id, or None if not registered."""
        return self.nodes.get(str(entity_id))

    def get_children(
        self,
        parent_id: str,
        *,
        socket_id: Optional[str] = None,
    ) -> List[str]:
        """Return entity_ids of all children of parent_id.

        If socket_id is provided, only children on that socket are returned.
        """
        pid = str(parent_id)
        children = self.children_by_parent.get(pid)
        if not children:
            return []
        if socket_id is None:
            return list(children)
        sid = str(socket_id)
        return [
            eid for eid in children
            if self.nodes.get(eid) is not None and self.nodes[eid].socket_id == sid
        ]

    def get_by_semantic_id(self, semantic_id: str) -> Optional[str]:
        """Return entity_id for a semantic_id, or None if not registered."""
        return self.semantic_index.get(str(semantic_id))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _remove_from_parent_index(
        self,
        entity_id: str,
        parent_entity_id: Optional[str],
    ) -> None:
        if not parent_entity_id:
            return
        children = self.children_by_parent.get(parent_entity_id)
        if children:
            children.discard(entity_id)
            if not children:
                self.children_by_parent.pop(parent_entity_id, None)
