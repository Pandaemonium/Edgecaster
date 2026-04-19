from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


Vec2 = Tuple[float, float]


@dataclass
class ChakraNode:
    node_id: str
    kind: str = "core"
    active: bool = True
    abs_pos: Optional[Vec2] = None
    channels: Dict[str, float] = field(default_factory=dict)
    tags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "node_id": str(self.node_id),
            "kind": str(self.kind or "core"),
            "active": bool(self.active),
            "channels": {str(k): float(v) for k, v in (self.channels or {}).items()},
            "tags": dict(self.tags or {}),
        }
        if self.abs_pos is not None:
            out["abs_pos"] = [float(self.abs_pos[0]), float(self.abs_pos[1])]
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChakraNode":
        raw_pos = data.get("abs_pos")
        abs_pos: Optional[Vec2] = None
        if isinstance(raw_pos, (list, tuple)) and len(raw_pos) >= 2:
            abs_pos = (float(raw_pos[0]), float(raw_pos[1]))
        channels_raw = data.get("channels")
        channels: Dict[str, float] = {}
        if isinstance(channels_raw, dict):
            for k, v in channels_raw.items():
                try:
                    channels[str(k)] = float(v)
                except Exception:
                    continue
        return cls(
            node_id=str(data.get("node_id") or ""),
            kind=str(data.get("kind") or "core"),
            active=bool(data.get("active", True)),
            abs_pos=abs_pos,
            channels=channels,
            tags=dict(data.get("tags") or {}),
        )


@dataclass
class ChakraEdge:
    # Unification note: keep the bootstrap edge record small, but Phase 2/3
    # needs explicit geometry semantics and composed propagation metadata here
    # so rune/fractal systems can query more than plain parent/child links.
    edge_id: str
    src_node_id: str
    dst_node_id: str
    kind: str = "contains"
    propagation: str = "automatic"
    weight: float = 1.0
    tags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_id": str(self.edge_id),
            "src_node_id": str(self.src_node_id),
            "dst_node_id": str(self.dst_node_id),
            "kind": str(self.kind or "contains"),
            "propagation": str(self.propagation or "automatic"),
            "weight": float(self.weight),
            "tags": dict(self.tags or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChakraEdge":
        return cls(
            edge_id=str(data.get("edge_id") or ""),
            src_node_id=str(data.get("src_node_id") or ""),
            dst_node_id=str(data.get("dst_node_id") or ""),
            kind=str(data.get("kind") or "contains"),
            propagation=str(data.get("propagation") or "automatic"),
            weight=float(data.get("weight", 1.0) or 1.0),
            tags=dict(data.get("tags") or {}),
        )


@dataclass
class ChakraComponent:
    root_node_id: str
    nodes: Dict[str, ChakraNode] = field(default_factory=dict)
    edges: Dict[str, ChakraEdge] = field(default_factory=dict)
    recursion_depth_cap: int = 7
    tags: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_node_id": str(self.root_node_id),
            "nodes": {str(k): v.to_dict() for k, v in (self.nodes or {}).items()},
            "edges": {str(k): v.to_dict() for k, v in (self.edges or {}).items()},
            "recursion_depth_cap": int(self.recursion_depth_cap),
            "tags": dict(self.tags or {}),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChakraComponent":
        nodes_raw = data.get("nodes")
        nodes: Dict[str, ChakraNode] = {}
        if isinstance(nodes_raw, dict):
            for k, v in nodes_raw.items():
                if isinstance(v, dict):
                    node = ChakraNode.from_dict(v)
                    if not node.node_id:
                        node.node_id = str(k)
                    nodes[str(node.node_id)] = node

        edges_raw = data.get("edges")
        edges: Dict[str, ChakraEdge] = {}
        if isinstance(edges_raw, dict):
            for k, v in edges_raw.items():
                if isinstance(v, dict):
                    edge = ChakraEdge.from_dict(v)
                    if not edge.edge_id:
                        edge.edge_id = str(k)
                    edges[str(edge.edge_id)] = edge

        return cls(
            root_node_id=str(data.get("root_node_id") or ""),
            nodes=nodes,
            edges=edges,
            recursion_depth_cap=int(data.get("recursion_depth_cap", 7) or 7),
            tags=dict(data.get("tags") or {}),
        )


def default_core_node_id(entity_id: str) -> str:
    return f"{str(entity_id)}:core"


def default_core_component(
    *,
    entity_id: str,
    max_hp: Optional[float] = None,
    mass: float = 1.0,
) -> ChakraComponent:
    core_id = default_core_node_id(str(entity_id))
    # Unification note: hp/mass are the universal baseline channels. We still
    # allow hp to be omitted when callers truly have no meaningful value yet,
    # but higher-level layout recipes should steadily eliminate that gap.
    channels: Dict[str, float] = {"mass": float(mass)}
    if max_hp is not None:
        channels["hp"] = float(max_hp)
    node = ChakraNode(node_id=core_id, kind="core", active=True, channels=channels)
    return ChakraComponent(
        root_node_id=core_id,
        nodes={core_id: node},
        edges={},
        recursion_depth_cap=7,
        tags={},
    )


# =============================================================================
# Component mutation helpers
#
# These are the authoritative write operations for ChakraComponent state.
# New code should write through these helpers rather than mutating
# component.nodes directly so every write follows consistent node
# initialization and the presence-equals-unlocked invariant is upheld.
#
# Unification note (Phase 2B → Phase 8):
# Writing through these helpers makes ChakraComponent the sole write authority.
# ChakraState is now a read-only derived view: rebuilt on demand by
# effective_chakra_view / effective_chakra_projection in chakra_items.py.
# =============================================================================

def unlock_node(comp: ChakraComponent, node_id: str, *, active: bool = False) -> bool:
    """Add a node to the component as unlocked.

    Presence in comp.nodes is the canonical definition of "unlocked."
    Returns True if the node was newly added, False if already present.
    """
    nid = str(node_id)
    if nid in comp.nodes:
        return False
    comp.nodes[nid] = ChakraNode(
        node_id=nid,
        kind="compat",
        active=bool(active),
        channels={},
        tags={"compat_unlocked": True},
    )
    return True


def set_node_active(comp: ChakraComponent, node_id: str, *, active: bool) -> None:
    """Set the active flag for a node.

    Creates the node as unlocked when not yet in the component. This allows
    toggling nodes that were unlocked via legacy ChakraState paths before the
    component was fully populated.
    """
    nid = str(node_id)
    if nid not in comp.nodes:
        comp.nodes[nid] = ChakraNode(
            node_id=nid,
            kind="compat",
            active=bool(active),
            channels={},
            tags={"compat_unlocked": True},
        )
    else:
        comp.nodes[nid].active = bool(active)


def set_node_charge(comp: ChakraComponent, node_id: str, charge: float) -> None:
    """Set the 'charge' channel for a node.

    Creates the node as unlocked-but-inactive when not yet in the component.
    This handles charge ticks on nodes whose unlock happened via legacy paths.
    """
    nid = str(node_id)
    if nid not in comp.nodes:
        comp.nodes[nid] = ChakraNode(
            node_id=nid,
            kind="compat",
            active=False,
            channels={"charge": float(charge)},
            tags={"compat_unlocked": True},
        )
    else:
        node = comp.nodes[nid]
        if not isinstance(node.channels, dict):
            node.channels = {}
        node.channels["charge"] = float(charge)


def get_node_charge(comp: ChakraComponent, node_id: str) -> float:
    """Return the 'charge' channel value for a node, defaulting to 0.0."""
    nid = str(node_id)
    node = comp.nodes.get(nid)
    if node is None:
        return 0.0
    channels = node.channels if not isinstance(node, dict) else node.get("channels")
    if not isinstance(channels, dict):
        return 0.0
    try:
        return float(channels.get("charge", 0.0))
    except Exception:
        return 0.0


def coerce_chakra_component(
    raw: Any,
    *,
    entity_id: str,
    max_hp: Optional[float] = None,
    mass: float = 1.0,
) -> ChakraComponent:
    # Unification note: this currently normalizes today's minimal component
    # payloads. As ChakraComponent becomes authoritative, also normalize edge
    # geometry payloads, propagation-policy composition, and deterministic edge
    # ordering here instead of scattering that work across callers.
    if isinstance(raw, ChakraComponent):
        comp = raw
    elif isinstance(raw, dict):
        comp = ChakraComponent.from_dict(raw)
    else:
        comp = ChakraComponent(root_node_id="", nodes={}, edges={}, recursion_depth_cap=7, tags={})

    core_id = str(comp.root_node_id or default_core_node_id(entity_id))
    comp.root_node_id = core_id
    if core_id not in comp.nodes:
        comp.nodes[core_id] = ChakraNode(node_id=core_id, kind="core", active=True, channels={})

    core = comp.nodes[core_id]
    if not isinstance(core.channels, dict):
        core.channels = {}
    core.channels.setdefault("mass", float(mass))
    if max_hp is not None:
        core.channels.setdefault("hp", float(max_hp))
    if not core.kind:
        core.kind = "core"
    core.active = bool(getattr(core, "active", True))
    comp.recursion_depth_cap = max(1, min(7, int(getattr(comp, "recursion_depth_cap", 7) or 7)))
    return comp
