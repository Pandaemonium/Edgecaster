"""
Chakra System - Body-Based Fractal Pattern Generation
======================================================

This module implements a chakra system where body schema nodes act as energy
centers that can be unlocked, activated, and aligned to generate unique
fractal patterns for spellcasting.

Core Concepts:
--------------
1. **Chakras**: Each node in an actor's body schema (torso, hand, finger, etc.)
   can function as a chakra - an energy point that contributes to pattern generation.

2. **Unlocking**: Chakras must be unlocked before they can be activated. Unlocking
   is gated by "branch roots" - nodes that are the entry points to sub-schemas.
   For example, you need to unlock "shoulder" before you can unlock any arm chakras.

3. **Activation**: Unlocked chakras can be toggled active/inactive. Only active
   chakras contribute vertices to your pattern.

4. **Alignment**: Each chakra has a position offset (alignment) that can be tuned.
   This affects the shape of the generated pattern. Higher dexterity = more precise
   alignment with less random wobble.

5. **Pattern Generation**: Active chakra positions become pattern vertices.
   Body tree edges between active chakras become pattern segments. These segments
   can then be fractally iterated using generators (Koch, Branch, etc.).

Gating Rules:
-------------
- Branch roots are nodes whose `proto` defines its own `body.root` (a sub-schema).
- To unlock a chakra, ALL branch roots in its ancestry must be unlocked.
- Intermediate nodes within the same schema do NOT gate each other.

Example gating chain for "knuckle_2" (second finger joint):
  torso (body root) → shoulder (arm root) → wrist (hand root) → [knuckle_1 is NOT a gate]

So knuckle_2 requires: {torso, shoulder, wrist} unlocked, but NOT knuckle_1.

Usage:
------
    from edgecaster.systems.chakras import (
        ChakraState,
        can_unlock_chakra,
        can_unlock_chakra_for_entity,
        unlock_chakra,
        toggle_chakra_active,
        generate_chakra_pattern,
    )

    # Check if player can unlock "wrist" chakra
    if can_unlock_chakra(actor.body_schema, actor.chakra_state, "wrist"):
        unlock_chakra(actor.chakra_state, "wrist")

    # Activate it
    toggle_chakra_active(actor.chakra_state, "wrist", active=True)

    # Generate pattern from active chakras
    pattern = generate_chakra_pattern(actor.body_schema, actor.chakra_state)

Migration note:
- The geometry/pattern code in this module is still valuable, but it is too
  actor/body-schema specific to be the final substrate.
- During unification, extract the geometry/pattern builders so any entity's
  ChakraComponent can feed them; keep ChakraState here as a compatibility layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# Type alias for 2D vectors
Vec2 = Tuple[float, float]


# =============================================================================
# CHAKRA STATE
# =============================================================================

# [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
# ChakraState remains only as a compatibility facade for actor-centric callers.
# The long-term runtime authority should be actor/body entities plus
# ChakraComponent geometry queried through shared entity/chakra APIs.
@dataclass
class ChakraState:
    """
    Tracks chakra unlocks, activations, and alignments for an actor.

    This is the primary state object stored on each actor that determines
    their chakra configuration and resulting pattern shape.

    Attributes:
        unlocked: Set of node_id strings that have been unlocked. Unlocked
                  chakras CAN be activated but aren't necessarily active.

        active: Set of node_id strings currently active. Active chakras
                contribute vertices to the generated pattern.

        alignments: Dict mapping node_id to (dx, dy) position offsets.
                    These offsets adjust the chakra's position from its
                    default body layout position, allowing pattern tuning.

        generators: Dict mapping node_id to generator_id. Optional per-chakra
                    fractal generator preferences (e.g., "koch", "branch").

    Example:
        # A player with torso and both shoulders active:
        state = ChakraState(
            unlocked={"torso", "shoulder", "shoulder_m"},
            active={"torso", "shoulder", "shoulder_m"},
            alignments={"shoulder": (0.1, -0.05)},  # Slightly adjusted
        )
    """

    # Unlocked chakras (can be activated)
    # Note: "body" is the root node in the human body schema (represents torso/core)
    unlocked: Set[str] = field(default_factory=lambda: {"body"})

    # Currently active chakras (contribute to pattern)
    active: Set[str] = field(default_factory=lambda: {"body"})

    # Position offsets for alignment tuning: node_id -> (dx, dy)
    alignments: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    # Per-chakra generator preferences: node_id -> generator_id
    generators: Dict[str, str] = field(default_factory=dict)

    # Per-chakra charge level (0..max). Populated lazily.
    charges: Dict[str, float] = field(default_factory=dict)

    # Optional root for chakra pattern generation (must be active).
    # If None, we fall back to the body root or any active chakra.
    pattern_root: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for saving."""
        return {
            "unlocked": list(self.unlocked),
            "active": list(self.active),
            "alignments": {k: list(v) for k, v in self.alignments.items()},
            "generators": dict(self.generators),
            "charges": dict(self.charges),
            "pattern_root": self.pattern_root,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChakraState":
        """Deserialize from dict."""
        if not data:
            return cls()
        return cls(
            unlocked=set(data.get("unlocked", ["body"])),
            active=set(data.get("active", ["body"])),
            alignments={
                k: tuple(v) for k, v in data.get("alignments", {}).items()
            },
            generators=dict(data.get("generators", {})),
            charges={k: float(v) for k, v in data.get("charges", {}).items()},
            pattern_root=data.get("pattern_root"),
        )


# =============================================================================
# BODY SCHEMA HELPERS
# =============================================================================

def _get_nodes(body_schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Extract the nodes dict from a body schema.

    Body schemas can have nodes stored directly or nested under 'body'.
    This helper normalizes access.
    """
    if not body_schema:
        return {}

    # Check for nested 'body' key first (common in resolved schemas)
    body = body_schema.get("body", body_schema)
    if isinstance(body, dict):
        nodes = body.get("nodes", {})
        if isinstance(nodes, dict):
            return nodes

    # Fallback: check if nodes is directly on schema
    nodes = body_schema.get("nodes", {})
    return nodes if isinstance(nodes, dict) else {}


def _get_root_node_id(body_schema: Dict[str, Any]) -> Optional[str]:
    """
    Get the root node ID from a body schema.

    Returns the ID of the entry-point node (e.g., "torso" for body schema).
    """
    if not body_schema:
        return None

    body = body_schema.get("body", body_schema)
    if isinstance(body, dict):
        root = body.get("root")
        if root:
            return str(root)

    return body_schema.get("root")


def _get_node_layout(node: Dict[str, Any]) -> Tuple[float, float]:
    """
    Extract layout position from a node.

    Returns (x, y) tuple, defaulting to (0, 0) if not specified.
    """
    layout = node.get("layout", {})
    if not isinstance(layout, dict):
        return (0.0, 0.0)

    x = float(layout.get("x", 0.0))
    y = float(layout.get("y", 0.0))
    return (x, y)


def _get_node_size(node: Dict[str, Any]) -> float:
    """
    Extract size/scale factor from a node.

    Size determines how much sub-schemas are scaled when drilling down.
    Returns value between 0.0 and 1.0, defaulting to 0.5.
    """
    props = node.get("props", {})
    if not isinstance(props, dict):
        return 0.5

    size = props.get("size", 0.5)
    return float(size) if isinstance(size, (int, float)) else 0.5


def _get_node_children(node: Dict[str, Any]) -> List[str]:
    """
    Get list of child node IDs from a node.
    """
    children = node.get("children", [])
    if isinstance(children, list):
        return [str(c) for c in children if c]
    return []


def _find_parent_node_id(
    nodes: Dict[str, Dict[str, Any]],
    target_id: str
) -> Optional[str]:
    """
    Find the parent node ID for a given node.

    Walks through all nodes checking their children lists.
    Returns None if target is root or not found.
    """
    for node_id, node in nodes.items():
        children = _get_node_children(node)
        if target_id in children:
            return node_id
    return None


# =============================================================================
# BRANCH ROOT DETECTION
# =============================================================================

def is_branch_root(proto_id: str, proto_index: Optional[Dict[str, Any]] = None) -> bool:
    """
    Check if a proto_id represents a branch root (entry to a sub-schema).

    A branch root is a proto that defines its own body schema with a root node.
    For example:
      - "arm" is a branch root (has body.root = "shoulder")
      - "hand" is a branch root (has body.root = "wrist")
      - "finger" is a branch root (has body.root = "knuckle_1")
      - "elbow" is NOT a branch root (no body.root defined)

    Args:
        proto_id: The prototype ID to check (e.g., "hand", "finger")
        proto_index: Optional resolved prototype index. If not provided,
                     will attempt to import from prototypes module.

    Returns:
        True if this proto defines a sub-schema (has body.root)
    """
    if not proto_id:
        return False

    # Branch roots should be detected from the *raw* proto, not the resolved
    # proto. Resolved protos inherit parent fields (including "body"), which
    # makes non-branch nodes (e.g., "knee") incorrectly look like branch roots.
    # That causes infinite-looking nested paths like "leg.knee.thigh.knee".
    try:
        from edgecaster.prototypes import get_raw_proto, base_proto_id
        pid = base_proto_id(str(proto_id))
        spec = get_raw_proto(pid)
    except Exception:
        # Fallback to provided index when raw lookup isn't available.
        if proto_index is None:
            return False
        spec = proto_index.get(str(proto_id), {})

    if not spec:
        return False

    # Check if this proto has a body schema with a root
    body = spec.get("body", {})
    if not isinstance(body, dict):
        return False

    root = body.get("root")
    return bool(root)


def get_gating_chain(
    body_schema: Dict[str, Any],
    target_node_id: str,
    proto_index: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Get the list of branch root node IDs that gate access to a target node.

    This walks up the body tree from the target node to the root, collecting
    any nodes that are branch roots (i.e., nodes whose proto has body.root).

    The gating chain represents the "prerequisite" chakras that must be
    unlocked before the target chakra can be unlocked.

    Args:
        body_schema: The actor's body schema dict
        target_node_id: The node we want to find the gating chain for
        proto_index: Optional prototype index for branch root checks

    Returns:
        List of node IDs that are branch roots in the ancestry of target.
        Ordered from root to target (topological order).

    Example:
        # For "knuckle_2" in a human body:
        chain = get_gating_chain(body_schema, "knuckle_2")
        # Returns: ["torso", "shoulder", "wrist"]
        # (palm/knuckle_1 are NOT included because they're not branch roots)
    """
    nodes = _get_nodes(body_schema)
    if not nodes or target_node_id not in nodes:
        return []

    # Build ancestry path from target to root
    ancestry: List[str] = []
    current = target_node_id
    visited: Set[str] = set()

    while current:
        if current in visited:
            break  # Prevent infinite loops
        visited.add(current)
        ancestry.append(current)
        current = _find_parent_node_id(nodes, current)

    # Reverse to get root-to-target order
    ancestry.reverse()

    # Filter to only branch roots (excluding the target itself)
    gating_chain: List[str] = []
    for node_id in ancestry:
        if node_id == target_node_id:
            continue  # Don't include target in its own gating chain

        node = nodes.get(node_id, {})
        proto = node.get("proto", "")

        if is_branch_root(proto, proto_index):
            gating_chain.append(node_id)

    return gating_chain


def collect_all_chakra_nodes(
    body_schema: Dict[str, Any],
    unlocked: Set[str],
    prefix: str = "",
    depth: int = 0,
    max_depth: Optional[int] = None,
    proto_index: Optional[Dict[str, Any]] = None,
    recursion_guard: Optional[Set[str]] = None,
) -> List[Tuple[str, str, int]]:
    """
    Recursively collect all chakra nodes, expanding sub-schemas when their
    branch root is unlocked.

    IMPORTANT: Once a branch root is unlocked, ALL nodes in its sub-schema
    become available immediately. There's no sequential gating within a
    sub-schema - only the "local root" (branch root) gates access.

    For example, once "arm" is unlocked:
    - ALL arm sub-nodes are available: shoulder, upper_arm, elbow, forearm, hand
    - You don't need to unlock shoulder before upper_arm

    Args:
        body_schema: The body schema to traverse
        unlocked: Set of already-unlocked chakra node IDs (may include prefixed IDs)
        prefix: Prefix for node IDs in this schema (e.g., "arm." for arm sub-schema)
        depth: Current nesting depth (0 = top-level)
        max_depth: Optional recursion depth limit (None = unbounded)
        proto_index: Optional prototype index cache
        recursion_guard: Proto-id guard for cycle detection in recursive schemas

    Returns:
        List of tuples: (full_node_id, display_name, depth)
        - full_node_id: e.g., "arm", "arm.shoulder", "arm.hand.wrist"
        - display_name: Human-readable name
        - depth: Nesting depth for sorting

    Example:
        # With "body" and "arm" unlocked:
        nodes = collect_all_chakra_nodes(body_schema, {"body", "arm"})
        # Returns all arm sub-nodes immediately available
    """
    if max_depth is not None and depth > max_depth:
        return []

    result: List[Tuple[str, str, int]] = []
    nodes = _get_nodes(body_schema)
    guard = set(recursion_guard or ())

    for node_id, node_spec in nodes.items():
        # Build full ID with prefix
        full_id = f"{prefix}{node_id}" if prefix else node_id

        # Get prototype ID for branch root check
        proto_id = node_spec.get("proto", node_id) if isinstance(node_spec, dict) else node_id

        # Build display name from node_id
        if node_id.endswith("_m"):
            base = node_id[:-2].replace("_", " ").title()
            display_name = f"{base} (Mirror)"
        else:
            display_name = node_id.replace("_", " ").title()

        result.append((full_id, display_name, depth))

        # If this node is a branch root AND it's unlocked, expand its sub-schema
        # ALL nodes in the sub-schema become available (no sequential gating)
        if is_branch_root(proto_id, proto_index) and full_id in unlocked:
            if str(proto_id) in guard:
                # Prevent infinite recursion if a schema references itself.
                continue
            try:
                from edgecaster.prototypes import resolve_body_schema
                sub_schema = resolve_body_schema(proto_id)
                if sub_schema and isinstance(sub_schema, dict) and sub_schema.get("nodes"):
                    # Recursively collect sub-schema nodes
                    # Pass unlocked so nested branch roots still require unlocking
                    sub_nodes = collect_all_chakra_nodes(
                        sub_schema,
                        unlocked,
                        prefix=f"{full_id}.",
                        depth=depth + 1,
                        max_depth=max_depth,
                        proto_index=proto_index,
                        recursion_guard=guard | {str(proto_id)},
                    )
                    result.extend(sub_nodes)
            except Exception:
                # If sub-schema resolution fails, just skip expansion
                pass

    return result


def chakra_display_name(full_id: str) -> str:
    """
    Convert a full chakra node id into a readable label.

    Examples:
      - "arm" -> "Arm"
      - "arm_m" -> "Arm (Mirror)"
      - "arm.hand.thumb" -> "Arm > Hand > Thumb"
      - "leg_m.knee" -> "Leg (Mirror) > Knee"
    """
    parts = str(full_id or "").split(".")
    out: List[str] = []
    for p in parts:
        if p.endswith("_m"):
            base = p[:-2].replace("_", " ").title()
            out.append(f"{base} (Mirror)")
        else:
            out.append(p.replace("_", " ").title())
    return " > ".join(out)


def _unlock_prereqs_for_full_id(full_id: str) -> List[str]:
    """
    Return required prefix ids for a full chakra id.

    Example:
      "arm.hand.thumb.knuckle_1" ->
        ["arm", "arm.hand", "arm.hand.thumb"]
    """
    parts = str(full_id or "").split(".")
    if len(parts) <= 1:
        return []
    out: List[str] = []
    for i in range(len(parts) - 1):
        out.append(".".join(parts[: i + 1]))
    return out


def can_unlock_full_chakra_id(chakra_state: ChakraState, full_id: str) -> bool:
    """
    Check unlockability for a full (possibly prefixed) chakra id.

    We treat all prefix segments as branch-root gates. That matches how nested
    chakra schemas are traversed in the scene and in generation.
    """
    if not full_id:
        return False
    if full_id in chakra_state.unlocked:
        return False
    for req in _unlock_prereqs_for_full_id(full_id):
        if req not in chakra_state.unlocked:
            return False
    return True


def list_unlockable_chakras(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
) -> List[str]:
    """
    Return all currently unlockable chakra ids (full/prefixed ids).

    This is the canonical query for "what can I unlock right now?" and is shared
    by class progression and NPC unlock UI so both use identical gating logic.
    """
    all_nodes = collect_all_chakra_nodes(body_schema, chakra_state.unlocked)
    locked = [
        (full_id, depth)
        for (full_id, _display_name, depth) in all_nodes
        if full_id not in chakra_state.unlocked
    ]

    # Stable deterministic order:
    #   1) shallower nodes first
    #   2) readable label
    #   3) full id
    locked.sort(key=lambda row: (row[1], chakra_display_name(row[0]), row[0]))

    return [
        full_id
        for (full_id, _depth) in locked
        if can_unlock_full_chakra_id(chakra_state, full_id)
    ]


def list_unlockable_chakras_for_entity(
    owner_ent: Any,
    chakra_state: ChakraState,
) -> List[str]:
    """Return unlockable chakra ids using deterministic body-node specs.

    This is the runtime-facing unlock query for real actor/entities. It uses
    `entity_body.build_body_node_specs(...)` instead of re-walking `body_schema`
    directly, which keeps the unlock flow aligned with the new body-entity
    hierarchy. The old `list_unlockable_chakras(body_schema, ...)` helper stays
    available for pre-runtime/editor flows that still only have authored schema
    data, such as character creation previews.
    """
    try:
        from edgecaster.systems import entity_body as entity_body_system

        specs = entity_body_system.build_body_node_specs(owner_ent)
    except Exception:
        specs = {}

    if not specs:
        try:
            from edgecaster.prototypes import resolve_body_schema

            return list_unlockable_chakras(resolve_body_schema(owner_ent), chakra_state)
        except Exception:
            return []

    locked: List[Tuple[str, int]] = []
    for spec in specs.values():
        full_id = str(getattr(spec, "full_id", "") or "")
        if not full_id or full_id in chakra_state.unlocked:
            continue
        locked.append((full_id, int(full_id.count("."))))

    locked.sort(key=lambda row: (row[1], chakra_display_name(row[0]), row[0]))
    return [
        full_id
        for (full_id, _depth) in locked
        if can_unlock_full_chakra_id(chakra_state, full_id)
    ]


def can_unlock_chakra_for_entity(
    owner_ent: Any,
    chakra_state: ChakraState,
    full_id: str,
) -> bool:
    """Entity-aware unlock check using body-node specs.

    This is the runtime-facing prerequisite gate for real actor/entities.
    It uses ``entity_body.build_body_node_specs`` to verify the node exists
    in the body tree, then delegates to ``can_unlock_full_chakra_id`` for
    the dot-prefix prerequisite logic.

    Falls back to the body-schema path (``can_unlock_chakra``) when specs
    are unavailable so that pre-runtime and editor flows continue to work.

    Args:
        owner_ent: The actor or entity whose body tree to query.
        chakra_state: Current chakra state.
        full_id: The fully-qualified node id to check (e.g. ``"arm.wrist"``).

    Returns:
        True if the node exists, is not yet unlocked, and all dot-prefix
        prerequisites are already unlocked.
    """
    try:
        from edgecaster.systems import entity_body as entity_body_system

        specs = entity_body_system.build_body_node_specs(owner_ent)
    except Exception:
        specs = {}

    if not specs:
        # Fall back to body-schema path for pre-runtime / editor contexts.
        try:
            from edgecaster.prototypes import resolve_body_schema

            return can_unlock_chakra(resolve_body_schema(owner_ent), chakra_state, full_id)
        except Exception:
            return False

    # Node must be present in the body tree.
    if full_id not in specs:
        return False

    # Delegate prereq logic to the full-id helper (uses dot-prefix decomposition).
    return can_unlock_full_chakra_id(chakra_state, full_id)


# =============================================================================
# CHAKRA OPERATIONS
# =============================================================================

def can_unlock_chakra(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    node_id: str,
    proto_index: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Check if a chakra can be unlocked.

    A chakra can be unlocked if:
    1. It exists in the body schema
    2. It's not already unlocked
    3. All branch roots in its gating chain are unlocked

    Args:
        body_schema: The actor's body schema
        chakra_state: Current chakra state
        node_id: The node/chakra to check
        proto_index: Optional prototype index

    Returns:
        True if the chakra can be unlocked

    Example:
        # Can we unlock the "wrist" chakra?
        if can_unlock_chakra(actor.body_schema, actor.chakra_state, "wrist"):
            print("Yes! All prerequisites met.")
    """
    nodes = _get_nodes(body_schema)

    # Must exist in body schema
    if node_id not in nodes:
        return False

    # Already unlocked
    if node_id in chakra_state.unlocked:
        return False

    # Check gating chain - all branch roots must be unlocked
    gating_chain = get_gating_chain(body_schema, node_id, proto_index)
    for gate_id in gating_chain:
        if gate_id not in chakra_state.unlocked:
            return False

    return True


def unlock_chakra(
    chakra_state: ChakraState,
    node_id: str,
    auto_activate: bool = False
) -> bool:
    """
    Unlock a chakra, allowing it to be activated.

    Note: This does NOT check prerequisites - use can_unlock_chakra() first
    if you need to validate the unlock is legal.

    Args:
        chakra_state: The chakra state to modify
        node_id: The chakra to unlock
        auto_activate: If True, also activate the chakra immediately

    Returns:
        True if newly unlocked, False if already unlocked
    """
    if node_id in chakra_state.unlocked:
        return False

    chakra_state.unlocked.add(node_id)

    if auto_activate:
        chakra_state.active.add(node_id)

    return True


def toggle_chakra_active(
    chakra_state: ChakraState,
    node_id: str,
    active: Optional[bool] = None
) -> bool:
    """
    Toggle a chakra's active state.

    Active chakras contribute to pattern generation. A chakra must be
    unlocked before it can be activated.

    Args:
        chakra_state: The chakra state to modify
        node_id: The chakra to toggle
        active: If provided, set to this state. If None, toggle current state.

    Returns:
        The new active state (True if active, False if inactive)
    """
    # Must be unlocked to activate
    if node_id not in chakra_state.unlocked:
        return False

    if active is None:
        # Toggle
        if node_id in chakra_state.active:
            chakra_state.active.discard(node_id)
            return False
        else:
            chakra_state.active.add(node_id)
            return True
    elif active:
        chakra_state.active.add(node_id)
        return True
    else:
        chakra_state.active.discard(node_id)
        return False


# =============================================================================
# POSITION EXTRACTION
# =============================================================================
#
# [LEGACY_DELETE][ENTITY_CHAKRA][BODY_SCHEMA]
# These recursive body-schema readers are now a compatibility bridge. The
# long-term runtime/query path should come from realized actor/body entities via
# `entity_body.py` + `entity_geometry.py`, with body_schema retained as authoring
# input instead of the main gameplay traversal substrate.

def get_chakra_connections_recursive(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    prefix: str = "",
    depth: int = 0,
    max_depth: Optional[int] = None,
    recursion_guard: Optional[Set[str]] = None,
) -> List[Tuple[str, str]]:
    """
    Build parent-child connections across the full body schema tree, including
    unlocked sub-schemas. This mirrors the Chakra scene's connection logic.
    """
    if max_depth is not None and depth > max_depth:
        return []

    nodes = _get_nodes(body_schema)
    if not nodes:
        return []

    root_id = _get_root_node_id(body_schema)
    root_full = f"{prefix}{root_id}" if (root_id and (prefix or root_id)) else root_id

    # Keep insertion order stable while deduping.
    edges: List[Tuple[str, str]] = []
    edge_keys: Set[Tuple[str, str]] = set()

    def add_edge(a: str, b: str) -> None:
        if not a or not b or a == b:
            return
        key = (a, b)
        if key in edge_keys:
            return
        edge_keys.add(key)
        edges.append(key)

    # Track which local nodes have an explicit parent edge in this schema.
    # Some sub-schemas (notably face) contain nodes that are not listed in
    # any `children` array. Without this fallback, valid chakras such as
    # `...face.eye` become unreachable and are dropped from generator seeds.
    has_parent_local: Set[str] = set()
    guard = set(recursion_guard or ())

    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            continue

        full_id = f"{prefix}{node_id}" if prefix else node_id

        # Edges to children within this schema
        children = node.get("children", [])
        if isinstance(children, list):
            for child_id in children:
                if child_id:
                    child_full_id = f"{prefix}{child_id}" if prefix else str(child_id)
                    add_edge(full_id, child_full_id)
                    has_parent_local.add(str(child_id))

        # If this is an unlocked branch root, connect to sub-schema root and recurse
        proto_id = node.get("proto", node_id)
        if is_branch_root(proto_id) and full_id in chakra_state.unlocked:
            if str(proto_id) in guard:
                continue
            try:
                from edgecaster.prototypes import resolve_body_schema
                sub_schema = resolve_body_schema(proto_id)
                if sub_schema and isinstance(sub_schema, dict):
                    sub_root = sub_schema.get("root")
                    if sub_root:
                        sub_root_full = f"{full_id}.{sub_root}"
                        add_edge(full_id, sub_root_full)
                    for sub_a, sub_b in get_chakra_connections_recursive(
                        sub_schema,
                        chakra_state,
                        prefix=f"{full_id}.",
                        depth=depth + 1,
                        max_depth=max_depth,
                        recursion_guard=guard | {str(proto_id)},
                    ):
                        add_edge(sub_a, sub_b)
            except Exception:
                pass

    # Fallback wiring: connect any local node with no declared parent to the
    # local schema root. This preserves 1:1 chakra<->schema-node behavior even
    # when authoring data omits some child links.
    if root_id and root_full:
        for local_id in nodes.keys():
            sid = str(local_id)
            if sid == root_id:
                continue
            if sid in has_parent_local:
                continue
            add_edge(root_full, f"{prefix}{sid}" if prefix else sid)

    return edges


def get_all_chakra_positions_recursive(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    base_scale: float = 1.0,
    prefix: str = "",
    parent_pos: Vec2 = (0.0, 0.0),
    parent_scale: float = 1.0,
    depth: int = 0,
    max_depth: Optional[int] = None,
    recursion_guard: Optional[Set[str]] = None,
) -> Dict[str, Tuple[Vec2, str, float, Vec2]]:
    """
    Recursively get positions for ALL chakra nodes, including sub-schemas.

    When a branch root is unlocked, this expands into its sub-schema and
    positions those nodes relative to the branch root's position.

    Args:
        body_schema: The body schema to traverse
        chakra_state: Current chakra state
        base_scale: Scale factor for this schema level
        prefix: Prefix for node IDs (e.g., "arm." for arm sub-schema)
        parent_pos: Parent node's position for offset calculation
        parent_scale: Parent's scale for composing transforms
        depth: Current recursion depth
        max_depth: Optional recursion depth limit (None = unbounded)
        recursion_guard: Proto-id guard for cycle detection in recursive schemas

    Returns:
        Dict mapping full_node_id to (position, state, local_scale, base_position)
        - position: (x, y) in unit coordinates (includes alignment offsets)
        - state: "locked", "unlocked", or "active"
        - local_scale: scale used for this node's layout (for alignment math)
        - base_position: (x, y) without alignment offsets
    """
    if max_depth is not None and depth > max_depth:
        return {}

    nodes = _get_nodes(body_schema)
    root_id = _get_root_node_id(body_schema)

    if not nodes:
        return {}

    result: Dict[str, Tuple[Vec2, str]] = {}
    guard = set(recursion_guard or ())
    visited_local: Set[str] = set()

    # Track which local nodes are explicitly listed as children. Any local node
    # not in this set (and not the schema root) is treated as an implicit child
    # of the local root, so it still participates in positioning/generation.
    has_parent_local: Set[str] = set()
    for _nid, _node in nodes.items():
        if not isinstance(_node, dict):
            continue
        for _cid in _get_node_children(_node):
            has_parent_local.add(str(_cid))

    def get_node_state(full_id: str) -> str:
        """Determine chakra state for a node."""
        if full_id in chakra_state.active:
            return "active"
        elif full_id in chakra_state.unlocked:
            return "unlocked"
        else:
            return "locked"

    def walk(node_id: str, pos: Vec2, scale: float) -> None:
        """Recursively walk the tree, computing positions."""
        if node_id not in nodes:
            return
        if node_id in visited_local:
            return
        visited_local.add(node_id)

        node = nodes[node_id]
        layout = _get_node_layout(node)
        size = _get_node_size(node)

        # Compute this node's base position (pre-alignment)
        x = pos[0] + (layout[0] * scale)
        y = pos[1] + (layout[1] * scale)
        base_pos = (x, y)

        # Build full ID with prefix
        full_id = f"{prefix}{node_id}" if prefix else node_id

        # Apply alignment offset if present
        if full_id in chakra_state.alignments:
            align = chakra_state.alignments[full_id]
            x += align[0] * scale * 0.5
            y += align[1] * scale * 0.5

        # Store this node's position and state
        state = get_node_state(full_id)
        result[full_id] = ((x, y), state, scale, base_pos)

        # Check if this is a branch root that's unlocked
        proto_id = node.get("proto", node_id) if isinstance(node, dict) else node_id
        if is_branch_root(proto_id) and full_id in chakra_state.unlocked:
            if str(proto_id) in guard:
                # Prevent infinite recursion if a schema references itself.
                return
            try:
                from edgecaster.prototypes import resolve_body_schema
                sub_schema = resolve_body_schema(proto_id)
                if sub_schema and isinstance(sub_schema, dict) and sub_schema.get("nodes"):
                    # Recursively get sub-schema positions
                    # Position sub-schema relative to this node
                    # NOTE: Only apply the parent scale once. The previous logic
                    # multiplied by (scale * size) twice, which made deeper
                    # chakras appear much smaller than intended.
                    sub_result = get_all_chakra_positions_recursive(
                        sub_schema,
                        chakra_state,
                        base_scale=1.0,
                        prefix=f"{full_id}.",
                        parent_pos=(x, y),
                        parent_scale=scale * size,
                        depth=depth + 1,
                        max_depth=max_depth,
                        recursion_guard=guard | {str(proto_id)},
                    )
                    result.update(sub_result)
            except Exception:
                pass

        # Recurse to children within this schema.
        #
        # IMPORTANT: Do NOT compound scale by node size for *in-schema* children.
        # The layout coordinates already describe relative spacing within the
        # schema's own coordinate system. Compounding size here causes deep
        # chains (arm → hand → finger) to collapse into near-zero distances,
        # which makes the chakra generator degenerate into a single line.
        #
        # Size should only affect how *sub-schemas* embed inside a branch root.
        child_scale = scale
        for child_id in _get_node_children(node):
            walk(child_id, (x, y), child_scale)

    # Start walk from root (or from parent_pos if nested)
    if root_id and root_id in nodes:
        walk(root_id, parent_pos, base_scale * parent_scale)
        root_full = f"{prefix}{root_id}" if prefix else root_id
        root_pos_entry = result.get(root_full)
        if root_pos_entry is not None:
            root_pos_u = root_pos_entry[0]
            root_scale = root_pos_entry[2]
            # Include orphan local nodes that have no explicit parent in
            # children-links by attaching them to the local root.
            for orphan_id in sorted(nodes.keys()):
                sid = str(orphan_id)
                if sid == root_id:
                    continue
                if sid in has_parent_local:
                    continue
                if sid in visited_local:
                    continue
                walk(sid, root_pos_u, root_scale)
    else:
        # No root, walk all nodes
        for node_id in nodes:
            walk(node_id, parent_pos, base_scale * parent_scale)

    return result


# =============================================================================
# PATTERN GENERATION
# =============================================================================

def get_connected_active_nodes(
    edges: List[Tuple[str, str]],
    active: Set[str],
    root_id: Optional[str] = None,
) -> Set[str]:
    """Return active nodes plus ancestors needed for a connected subgraph.

    If root_id is provided, only active nodes that can trace ancestry to
    root_id are included. This keeps the chakra graph connected to the
    chosen pattern root and excludes unrelated branches.

    NOTE:
    The chakra graph is a tree (with sub-schema edges), but the pattern
    root can be *any* active node. When the root is not an ancestor of
    another active node (e.g., root=elbow, active=calf), we still want
    that node if it is reachable through the graph. That means we must
    treat edges as **undirected** and include the full path from the
    root to each active node (including intermediate connectors).
    """
    if not active:
        return set()

    # Build undirected adjacency so we can reach nodes across branches.
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    # If a root is provided, include the path from root to each active node.
    if root_id is not None:
        if root_id not in adj:
            return set()

        # BFS to build a parent tree from the chosen root.
        parent: Dict[str, Optional[str]] = {root_id: None}
        stack = [root_id]
        while stack:
            cur = stack.pop()
            for nb in adj.get(cur, []):
                if nb not in parent:
                    parent[nb] = cur
                    stack.append(nb)

        expanded: Set[str] = set()
        for node in list(active):
            if node not in parent:
                continue
            cur = node
            while cur is not None:
                expanded.add(cur)
                if cur == root_id:
                    break
                cur = parent.get(cur)
        # Always include the root itself.
        expanded.add(root_id)
        return expanded

    # No root: keep legacy behavior (active + ancestors to schema root).
    parent_map: Dict[str, str] = {}
    for parent, child in edges:
        if child not in parent_map:
            parent_map[child] = parent

    expanded: Set[str] = set()
    for node in list(active):
        cur = node
        chain: List[str] = [cur]
        while cur in parent_map:
            cur = parent_map[cur]
            chain.append(cur)
        expanded.update(chain)

    return expanded


def get_compact_active_graph(
    edges: List[Tuple[str, str]],
    active: Set[str],
    root_id: Optional[str] = None,
) -> Tuple[Set[str], List[Tuple[str, str]]]:
    """Return (active_nodes, compact_edges) for the chakra graph.

    This keeps only ACTIVE chakras as vertices while still preserving
    connectivity by *compressing* paths through inactive nodes.

    Example:
      arm -- upper_arm -- elbow -- forearm -- hand -- thumb

    If only {arm, hand, thumb} are active, the compact graph becomes:
      arm -- hand -- thumb

    This prevents inactive "connector" chakras from creating extra
    vertices in the pattern while still keeping branches connected.

    If root_id is provided, we only keep active nodes reachable from
    that root (using the full graph, including inactive nodes).
    """
    if not active:
        return set(), []

    # Build undirected adjacency for traversal.
    adj: Dict[str, List[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)

    # Filter active set to nodes reachable from root (if root is valid).
    active_set = set(active)
    if root_id and root_id in adj:
        seen: Set[str] = {root_id}
        queue = [root_id]
        while queue:
            cur = queue.pop()
            for nxt in adj.get(cur, []):
                if nxt in seen:
                    continue
                seen.add(nxt)
                queue.append(nxt)
        active_set = {n for n in active_set if n in seen}

    if not active_set:
        return set(), []

    # Choose a root for orientation. This prevents cycles and keeps
    # branching behavior intuitive (each active node links to its
    # nearest *active ancestor* toward the root).
    if root_id and root_id in active_set:
        root = root_id
    else:
        root = next(iter(active_set))

    # Build parent map via BFS from the chosen root.
    parent: Dict[str, str] = {}
    queue = [root]
    seen = {root}
    while queue:
        cur = queue.pop(0)
        for nxt in adj.get(cur, []):
            if nxt in seen:
                continue
            seen.add(nxt)
            parent[nxt] = cur
            queue.append(nxt)

    # Recompute active_set to only include nodes reachable from root.
    active_set = {n for n in active_set if n in seen}
    if not active_set:
        return set(), []

    # Connect each active node to its nearest active ancestor.
    compact_edges: Set[Tuple[str, str]] = set()
    for node in active_set:
        if node == root:
            continue
        cur = node
        anc = parent.get(cur)
        while anc is not None and anc not in active_set:
            cur = anc
            anc = parent.get(cur)
        if anc is None:
            continue
        key = (node, anc) if node <= anc else (anc, node)
        compact_edges.add(key)

    return active_set, list(compact_edges)


def get_active_chakra_generator_graph(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    *,
    base_scale: float = 1.0,
    require_root: bool = True,
) -> Tuple[Dict[str, Vec2], List[Tuple[str, str]], str, str]:
    """Build the active chakra graph used by generator/preview code.

    Returns:
        (positions, compact_edges, root_id, terminus_id)
        - positions: node_id -> (x, y) for active nodes reachable from root
        - compact_edges: active-only compressed edges
        - root_id: selected active root
        - terminus_id: active node furthest from root by Euclidean distance

    Notes:
    - There is no implicit "body root" fallback.
    - If `require_root=True`, `pattern_root` must be set and active.
    """
    all_positions = get_all_chakra_positions_recursive(
        body_schema,
        chakra_state,
        base_scale=base_scale,
    )
    full_edges = get_chakra_connections_recursive(body_schema, chakra_state)

    root_id = getattr(chakra_state, "pattern_root", None)
    if root_id not in chakra_state.active:
        if require_root:
            raise ValueError("Select an active chakra as the pattern root first.")
        active_sorted = sorted(chakra_state.active)
        if not active_sorted:
            raise ValueError("Need at least 2 active chakras to form a generator.")
        root_id = active_sorted[0]

    active_nodes, compact_edges = get_compact_active_graph(
        full_edges,
        chakra_state.active,
        root_id=root_id,
    )
    positions: Dict[str, Vec2] = {
        node_id: pos_u
        for node_id, (pos_u, _state, _scale, _base_pos) in all_positions.items()
        if node_id in active_nodes
    }

    if root_id not in positions:
        raise ValueError("Pattern root not found in chakra pattern.")

    candidates = [nid for nid in active_nodes if nid != root_id and nid in positions]
    if not candidates:
        raise ValueError("Need at least 2 connected chakras to form a generator.")

    rx, ry = positions[root_id]

    def d2(nid: str) -> float:
        x, y = positions[nid]
        dx = x - rx
        dy = y - ry
        return dx * dx + dy * dy

    terminus_id = max(candidates, key=d2)
    return positions, compact_edges, str(root_id), str(terminus_id)


@dataclass(frozen=True)
class ChakraGeneratorSeed:
    """
    Canonical chakra generator seed used by both runtime casting and UI preview.

    Keeping these fields in a single object prevents preview/runtime drift and
    gives downstream systems a stable place to attach per-vertex chakra metadata.
    """

    # Active chakra graph in schema space.
    positions: Dict[str, Vec2]
    compact_edges: List[Tuple[str, str]]
    root_id: str
    terminus_id: str

    # Normalized custom-graph data (exact input to CustomGraphGenerator).
    verts: List[Vec2]
    edges: List[Tuple[int, int]]
    node_order: List[str]
    base_len: float


def build_chakra_generator_seed(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    *,
    base_scale: float = 1.0,
    require_root: bool = True,
    actor: Any | None = None,
    game: Any | None = None,
) -> ChakraGeneratorSeed:
    """
    Build the canonical chakra generator seed.

    This is the single source of truth for:
    - root/terminus selection
    - active compact graph extraction
    - normalized custom-graph conversion
    """
    # Phase B1: Entity tree path.
    # Step 1 — Pre-warm: if this actor has an expandable body schema, realize
    # body-node children now so B2 unlock/active lookups find them in the graph.
    # Body-anatomy node offsets are sub-tile floats (< 0.5 tile) that collapse
    # to the same integer grid cell, so entity abs_pos cannot serve as geometry.
    # Step 2 — Geometry query: try query_normalized_pattern for entities that
    # have explicit, world-scale chakra_component abs_pos (sites, structures,
    # non-body actors).  Coincident positions cause normalization to fail, which
    # is caught internally; the legacy body-schema path below handles that case.
    if actor is not None and game is not None:
        try:
            root_entity_id = str(getattr(actor, "entity_id", "") or getattr(actor, "id", "") or "").strip()
        except Exception:
            root_entity_id = ""
        if root_entity_id:
            try:
                has_component = getattr(actor, "chakra_component", None) is not None
            except Exception:
                has_component = False
            if has_component:
                try:
                    from edgecaster.systems import entity_body as entity_body_system
                    from edgecaster.systems import entity_geometry as entity_geometry_system
                    from edgecaster.systems import entity_lifecycle as entity_lifecycle_system

                    # Pre-warm: realize body-node children for B2 state reads.
                    if entity_body_system.can_expand_entity(actor):
                        entity_lifecycle_system.expand_entity(
                            game, root_entity_id, reason="chakra_seed"
                        )

                    # Geometry query: succeeds for explicit world-scale components;
                    # silently returns empty for sub-tile body anatomy.
                    query = entity_geometry_system.query_normalized_pattern(
                        game,
                        root_entity_id,
                        helper_id="seed_pattern",
                        realize_policy="allow",
                    )
                    verts = list(query.get("verts") or [])
                    edges = list(query.get("edges") or [])
                    node_order = list(query.get("node_order") or [])
                    if verts and edges and node_order:
                        positions = {
                            str(node_id): tuple(query["positions"][str(node_id)])
                            for node_id in node_order
                            if str(node_id) in query.get("positions", {})
                        }
                        compact_edges = [
                            (str(a), str(b))
                            for a, b in (query.get("compact_edges") or [])
                        ]
                        return ChakraGeneratorSeed(
                            positions=positions,
                            compact_edges=compact_edges,
                            root_id=str(query.get("root_id") or ""),
                            terminus_id=str(query.get("terminus_id") or ""),
                            verts=[(float(x), float(y)) for (x, y) in verts],
                            edges=[(int(a), int(b)) for (a, b) in edges],
                            node_order=[str(node_id) for node_id in node_order],
                            base_len=float(query.get("base_len", 0.0) or 0.0),
                        )
                except Exception:
                    pass

    # Body-schema geometry path: authoritative source for chakra pattern positions.
    # Body anatomy offsets are sub-tile floats; these float positions are what
    # the pattern generator requires.  This path remains the geometry source for
    # body-anatomy actors until B2 wires unlock/active authority to body-node
    # entity channels and a float-resolution geometry query replaces the schema
    # traversal.
    # [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
    positions, compact_edges, root_id, terminus_id = get_active_chakra_generator_graph(
        body_schema,
        chakra_state,
        base_scale=base_scale,
        require_root=require_root,
    )
    verts, edges, node_order, base_len = normalized_custom_graph_from_positions(
        positions,
        compact_edges,
        root_id=root_id,
        terminus_id=terminus_id,
    )
    return ChakraGeneratorSeed(
        positions=positions,
        compact_edges=compact_edges,
        root_id=root_id,
        terminus_id=terminus_id,
        verts=verts,
        edges=edges,
        node_order=node_order,
        base_len=base_len,
    )


def normalized_custom_graph_from_positions(
    positions: Dict[str, Vec2],
    compact_edges: List[Tuple[str, str]],
    *,
    root_id: str,
    terminus_id: str,
) -> Tuple[List[Vec2], List[Tuple[int, int]], List[str], float]:
    """Convert active chakra graph to a normalized CustomGraphGenerator shape.

    The output baseline is:
    - root vertex at (0, 0)
    - terminus vertex at (1, 0)

    Returns:
        (verts, edges, node_order, base_len)
    """
    if root_id not in positions or terminus_id not in positions:
        raise ValueError("Root/terminus missing from active chakra positions.")

    # Unification note: the normalization math here is already substrate-worthy.
    # Once non-actor entities expose geometry-rich ChakraComponents, lift this to
    # a shared helper that reads node/edge geometry directly instead of relying
    # on body_schema + legacy ChakraState inputs.
    # Stable order: root first, terminus last, everything else deterministic.
    middle = sorted([nid for nid in positions.keys() if nid not in {root_id, terminus_id}])
    node_order = [root_id] + middle + [terminus_id]

    # Build raw vertex list in chosen order.
    raw_verts: List[Vec2] = [positions[nid] for nid in node_order]

    root_idx = 0
    term_idx = len(node_order) - 1
    rx, ry = raw_verts[root_idx]
    tx, ty = raw_verts[term_idx]
    bx = tx - rx
    by = ty - ry
    base_len = math.hypot(bx, by)
    if base_len <= 1e-9:
        raise ValueError("Root and terminus overlap; cannot normalize generator.")

    ang = math.atan2(by, bx)
    cos_a = math.cos(-ang)
    sin_a = math.sin(-ang)

    verts: List[Vec2] = []
    for vx, vy in raw_verts:
        dx = vx - rx
        dy = vy - ry
        nx = dx * cos_a - dy * sin_a
        ny = dx * sin_a + dy * cos_a
        nx /= base_len
        ny /= base_len
        verts.append((nx, ny))

    # Pin baseline endpoints exactly.
    verts[root_idx] = (0.0, 0.0)
    verts[term_idx] = (1.0, 0.0)

    # Index mapping for edge remap.
    idx_by_node = {nid: i for i, nid in enumerate(node_order)}
    edge_keys: Set[Tuple[int, int]] = set()
    out_edges: List[Tuple[int, int]] = []
    for a_id, b_id in compact_edges:
        if a_id not in idx_by_node or b_id not in idx_by_node:
            continue
        ia = idx_by_node[a_id]
        ib = idx_by_node[b_id]
        if ia == ib:
            continue
        k = (ia, ib) if ia <= ib else (ib, ia)
        if k in edge_keys:
            continue
        edge_keys.add(k)
        out_edges.append((ia, ib))

    if not out_edges:
        raise ValueError("Need at least 2 connected chakras to form a generator.")

    return verts, out_edges, node_order, base_len


def chakras_to_seed_pattern(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    base_scale: float = 5.0
) -> "Pattern":
    """
    Create an initial pattern from active chakras.

    Each active chakra becomes a vertex. Body tree edges between active
    chakras become pattern edges. This "seed pattern" can then be
    fractally iterated to create complex shapes.

    Args:
        body_schema: The actor's body schema
        chakra_state: Current chakra state
        base_scale: World-space scale factor

    Returns:
        A Pattern object with vertices at chakra positions and edges
        following the body tree structure.
    """
    # Import here to avoid circular dependency
    from edgecaster.state.patterns import Pattern

    # Unification note: this still pulls connectivity from body_schema. The
    # end-state equivalent should accept any entity/chakra root and build the
    # seed from shared ChakraComponent geometry so sites, items, and anatomy all
    # speak the same API.
    # Build full positions map (includes inactive + sub-schema nodes).
    all_positions = get_all_chakra_positions_recursive(
        body_schema,
        chakra_state,
        base_scale=base_scale,
    )

    # Build the full chakra graph and then compact it so only ACTIVE
    # chakras become vertices (inactive connectors are compressed away).
    edges = get_chakra_connections_recursive(body_schema, chakra_state)
    root_id = getattr(chakra_state, "pattern_root", None)
    if root_id not in chakra_state.active:
        root_id = None
    active_nodes, compact_edges = get_compact_active_graph(
        edges,
        chakra_state.active,
        root_id=root_id,
    )

    positions = {
        node_id: pos_u
        for node_id, (pos_u, _state, _scale, _base_pos) in all_positions.items()
        if node_id in active_nodes
    }

    if not positions:
        # Return empty pattern
        return Pattern()

    import json

    pattern = Pattern()
    node_to_idx: Dict[str, int] = {}
    node_order: List[str] = []

    # Add vertices for each active chakra (including sub-schema nodes)
    for node_id, pos in positions.items():
        idx = pattern.add_vertex(pos, color="chakra", power=1.0, tags={"chakra_node": node_id})
        node_to_idx[node_id] = idx
        node_order.append(node_id)

    # Add edges following body tree structure (including sub-schemas).
    # An edge exists between parent and child if BOTH endpoints are in the
    # connected active set (so chains stay connected).
    for parent_id, child_id in compact_edges:
        if parent_id in node_to_idx and child_id in node_to_idx:
            pattern.add_edge(
                node_to_idx[parent_id],
                node_to_idx[child_id],
                color="chakra",
                weight=1.0,
            )

    # Store vertex -> chakra node mapping so downstream systems can
    # reliably identify the chosen root by node id.
    try:
        pattern.meta["chakra_nodes"] = json.dumps(node_order)
    except Exception:
        pass

    return pattern


def generate_chakra_pattern(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    iterations: int = 2,
    generator_id: str = "koch",
    base_scale: float = 5.0
) -> "Pattern":
    """
    Generate a full fractal pattern from chakra configuration.

    This is the main entry point for pattern generation. It:
    1. Creates a seed pattern from active chakras
    2. Applies fractal iteration using the specified generator
    3. Returns the final complex pattern

    Args:
        body_schema: The actor's body schema
        chakra_state: Current chakra state
        iterations: Number of fractal iterations (1-4 typical)
        generator_id: Which fractal generator to use:
                      - "koch": Classic Koch snowflake bumps
                      - "branch": Tree-like branching
                      - "zigzag": Zigzag subdivision
                      - "subdivide": Simple subdivision
        base_scale: World-space scale factor

    Returns:
        A Pattern with fractally-iterated vertices and edges

    Example:
        pattern = generate_chakra_pattern(
            actor.body_schema,
            actor.chakra_state,
            iterations=2,
            generator_id="koch"
        )
        # Pattern now has complex fractal geometry based on chakra positions
    """
    from edgecaster.patterns.builder import (
        apply_chain,
        KochGenerator,
        BranchGenerator,
        ZigzagGenerator,
        SubdivideGenerator,
    )

    # Unification note: keep the fractal iteration stage reusable, but make the
    # seed input come from shared chakra graph queries rather than actor-only
    # state once ChakraComponent becomes the main runtime substrate.
    # Get seed pattern from chakras
    seed = chakras_to_seed_pattern(body_schema, chakra_state, base_scale)

    if not seed.vertices or not seed.edges:
        return seed  # No iteration possible without edges

    # Select generator based on ID
    generators = {
        "koch": KochGenerator(height_factor=0.25),
        "branch": BranchGenerator(branch_count=2, angle_deg=45, length_factor=0.5),
        "zigzag": ZigzagGenerator(parts=4, amplitude_factor=0.15),
        "subdivide": SubdivideGenerator(parts=3),
    }

    generator = generators.get(generator_id, generators["koch"])

    # Clamp iterations to reasonable range
    iterations = max(0, min(4, iterations))

    if iterations == 0:
        return seed

    # Apply fractal iteration
    steps = [(generator, iterations)]
    return apply_chain(seed, steps, max_segments=5000)


# =============================================================================
# RESONANCE BONUSES (Optional Enhancement)
# =============================================================================

# Chakra charge tuning (feel free to tweak)
CHARGE_MAX_BASE = 1.0
CHARGE_GAIN_PER_TICK = 0.004  # base gain per tick while charging
CHARGE_DECAY_PER_TICK = 0.006  # decay per tick when not charging or inactive
CHARGE_CONSUME_ACTIVATE = 0.35  # spend on activation actions (Activate R/N)
CHARGE_CONSUME_GENERATOR = 0.20  # spend on generator actions (chakra apply)

# Charge -> modifier scales
CHARGE_DAMAGE_SCALE = 0.25  # avg_charge * this applied to damage
CHARGE_RADIUS_BONUS = 0.5   # avg_charge * this added to radius
CHARGE_MANA_DISCOUNT = 0.10  # avg_charge * this reduces mana cost multiplier
CHARGE_AMP_SCALE = 0.30     # avg_charge * this increases chakra generator amplitude


@dataclass
class ChakraModifiers:
    """Aggregate modifiers derived from resonances + charge."""
    mana_cost_mult: float = 1.0
    damage_mult: float = 1.0
    radius_bonus: float = 0.0
    neighbor_depth_bonus: int = 0
    chakra_amp_mult: float = 1.0

    # Charge mechanics
    charge_gain_mult: float = 1.0
    charge_cap_bonus: float = 0.0
    charge_consume_mult: float = 1.0

    def apply(self, other: "ChakraModifiers") -> None:
        """Merge another modifier set into this one."""
        self.mana_cost_mult *= other.mana_cost_mult
        self.damage_mult *= other.damage_mult
        self.radius_bonus += other.radius_bonus
        self.neighbor_depth_bonus += int(other.neighbor_depth_bonus)
        self.chakra_amp_mult *= other.chakra_amp_mult
        self.charge_gain_mult *= other.charge_gain_mult
        self.charge_cap_bonus += other.charge_cap_bonus
        self.charge_consume_mult *= other.charge_consume_mult


RESONANCE_EFFECTS: Dict[str, ChakraModifiers] = {
    # Arms in harmony: cheaper, steadier activations.
    "bilateral_arms": ChakraModifiers(mana_cost_mult=0.90, damage_mult=1.05),
    # Grounding legs: broader radius, steadier control.
    "grounded": ChakraModifiers(radius_bonus=0.5, mana_cost_mult=0.95),
    # Centered torso: stronger output + more stable generator.
    "centered": ChakraModifiers(damage_mult=1.10, chakra_amp_mult=1.10),
    # Full hand(s): faster charge + slightly deeper neighbor reach.
    "full_hand": ChakraModifiers(charge_gain_mult=1.25, charge_cap_bonus=0.15, neighbor_depth_bonus=1),
    "full_hand_m": ChakraModifiers(charge_gain_mult=1.25, charge_cap_bonus=0.15, neighbor_depth_bonus=1),
}

def check_resonance_bonuses_from_active_nodes(active_node_ids: Set[str]) -> List[str]:
    """Return active resonance bonus ids from normalized active node ids."""
    bonuses: List[str] = []
    active = {str(node_id or "") for node_id in (active_node_ids or set()) if str(node_id or "")}

    def _has(node_id: str) -> bool:
        """True if any active node matches node_id or a prefixed sub-schema id."""
        if node_id in active:
            return True
        suffix = f".{node_id}"
        return any(a.endswith(suffix) for a in active)

    # Bilateral arms (top-level or sub-schema shoulder)
    if (_has("arm") and _has("arm_m")) or (_has("shoulder") and _has("shoulder_m")):
        bonuses.append("bilateral_arms")

    # Full hand (any hand) - sub-schema node IDs
    hand_fingers = {"thumb", "index", "middle", "ring", "pinky"}
    if all(_has(fid) for fid in hand_fingers):
        bonuses.append("full_hand")

    # Mirrored hand
    hand_fingers_m = {"thumb_m", "index_m", "middle_m", "ring_m", "pinky_m"}
    if all(_has(fid) for fid in hand_fingers_m):
        bonuses.append("full_hand_m")

    # Grounded (top-level "leg" nodes or sub-schema "thigh" nodes)
    if (_has("leg") and _has("leg_m")) or (_has("thigh") and _has("thigh_m")):
        bonuses.append("grounded")

    # Centered - torso/core
    if _has("body"):
        bonuses.append("centered")

    return bonuses


def check_resonance_bonuses(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState
) -> List[str]:
    """
    Compatibility wrapper for resonance checks.

    `body_schema` is retained here so older callers do not break, but current
    resonance rules only depend on the active node ids. Runtime code should
    prefer `check_resonance_bonuses_from_active_nodes(...)`.
    """
    _ = body_schema
    active_nodes = set(getattr(chakra_state, "active", set()) or set())
    return check_resonance_bonuses_from_active_nodes(active_nodes)


def get_resonance_modifiers(bonuses: List[str]) -> ChakraModifiers:
    """Aggregate modifiers from active resonance bonuses."""
    mods = ChakraModifiers()
    for bonus in bonuses:
        effect = RESONANCE_EFFECTS.get(bonus)
        if effect:
            mods.apply(effect)
    return mods


def get_average_charge(chakra_state: ChakraState, nodes: Optional[Set[str]] = None) -> float:
    """Average charge across the given nodes (defaults to active)."""
    targets = nodes or chakra_state.active
    if not targets:
        return 0.0
    vals = [float(chakra_state.charges.get(n, 0.0)) for n in targets]
    return sum(vals) / max(1, len(vals))


def apply_charge_to_modifiers(mods: ChakraModifiers, avg_charge: float) -> ChakraModifiers:
    """Apply charge-based scaling to modifiers (returns new object)."""
    out = ChakraModifiers()
    out.apply(mods)

    # Charge improves output while reducing mana cost a bit.
    out.damage_mult *= 1.0 + avg_charge * CHARGE_DAMAGE_SCALE
    out.radius_bonus += avg_charge * CHARGE_RADIUS_BONUS
    out.mana_cost_mult *= max(0.5, 1.0 - avg_charge * CHARGE_MANA_DISCOUNT)
    out.chakra_amp_mult *= 1.0 + avg_charge * CHARGE_AMP_SCALE
    return out


def tick_chakra_charge(
    chakra_state: ChakraState,
    delta: int,
    *,
    charging: bool,
    dex: int = 0,
) -> None:
    """Update chakra charge values in-place."""
    if delta <= 0:
        return

    active_nodes = set(getattr(chakra_state, "active", set()) or set())
    bonuses = check_resonance_bonuses_from_active_nodes(active_nodes)
    mods = get_resonance_modifiers(bonuses)

    gain = CHARGE_GAIN_PER_TICK * mods.charge_gain_mult
    decay = CHARGE_DECAY_PER_TICK
    if dex > 0:
        gain *= 1.0 + float(dex) * 0.01

    cap = CHARGE_MAX_BASE + mods.charge_cap_bonus

    # Ensure dictionary exists
    if chakra_state.charges is None:
        chakra_state.charges = {}

    if charging:
        for node_id in chakra_state.active:
            cur = float(chakra_state.charges.get(node_id, 0.0))
            chakra_state.charges[node_id] = min(cap, cur + gain * delta)
        # Decay inactive nodes to zero
        for node_id in list(chakra_state.charges.keys()):
            if node_id not in chakra_state.active:
                cur = float(chakra_state.charges.get(node_id, 0.0))
                cur = max(0.0, cur - decay * delta)
                chakra_state.charges[node_id] = cur
    else:
        # No charging state: decay all
        for node_id in list(chakra_state.charges.keys()):
            cur = float(chakra_state.charges.get(node_id, 0.0))
            cur = max(0.0, cur - decay * delta)
            chakra_state.charges[node_id] = cur


def consume_chakra_charge(
    chakra_state: ChakraState,
    amount: float,
    nodes: Optional[Set[str]] = None,
) -> None:
    """Consume charge from the given nodes (defaults to active)."""
    if amount <= 0:
        return
    targets = nodes or chakra_state.active
    if not targets:
        return
    for node_id in targets:
        cur = float(chakra_state.charges.get(node_id, 0.0))
        chakra_state.charges[node_id] = max(0.0, cur - amount)
