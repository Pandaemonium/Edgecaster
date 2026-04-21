"""
Chakra System - Body-Based Fractal Pattern Generation
======================================================

This module implements a chakra system where body nodes act as energy
centers that can be unlocked, activated, and aligned to generate unique
fractal patterns for spellcasting.

Core Concepts:
--------------
1. **Chakras**: Each node in an actor's body entity tree (torso, hand, finger,
   etc.) can function as a chakra — an energy point that contributes to pattern
   generation.  The authoritative runtime representation is ChakraComponent on
   each body-node entity; the legacy body_schema dict is a fallback path for
   contexts where the entity tree has not been realized.

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

Runtime Usage (entity path, preferred for spawned actors):
----------------------------------------------------------
    from edgecaster.systems import chakra_items
    from edgecaster.systems.chakras import list_unlockable_chakras_for_entity_from_unlocked

    # Ask what the player can unlock (returns list of full_ids).
    unlocked = chakra_items.effective_unlocked_nodes(game, actor)
    unlockable = list_unlockable_chakras_for_entity_from_unlocked(actor, unlocked)

    # Unlock and deactivate.
    chakra_items.unlock_actor_chakra(actor, "arm.wrist", game=game)
    chakra_items.toggle_actor_chakra(actor, "arm.wrist", active=False, game=game)

    # Generate pattern seed through the actor-oriented helper.
    from edgecaster.systems.chakras import build_chakra_generator_seed_for_actor
    seed = build_chakra_generator_seed_for_actor(actor, game=game)

Migration note:
- The geometry/pattern code in this module is still valuable, but it is too
  actor/body-schema specific to be the final substrate.
- The entity path (ChakraComponent + entity_geometry) is now the preferred
  runtime substrate. The body-schema path remains as a fallback for the
  seed-builder when no realized entity tree is available.
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


# =============================================================================
# BRANCH ROOT DETECTION
# =============================================================================

def is_branch_root(proto_id: str) -> bool:
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
        return False

    if not spec:
        return False

    # Check if this proto has a body schema with a root
    body = spec.get("body", {})
    if not isinstance(body, dict):
        return False

    root = body.get("root")
    return bool(root)


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


def list_unlockable_chakras_for_entity_from_unlocked(
    owner_ent: Any,
    unlocked_node_ids: Any,
) -> List[str]:
    """Return unlockable chakra ids using only the current unlocked-node set.

    This is the thinner runtime unlock query for callers that do not need a
    full ChakraState projection. It stays on the shared entity-body unlock
    logic while avoiding ChakraState construction on frequently queried paths.
    """
    unlocked: Set[str] = set()
    try:
        for raw in (unlocked_node_ids or []):
            value = str(raw or "").strip()
            if value:
                unlocked.add(value)
    except Exception:
        unlocked = set()

    state = ChakraState(unlocked=set(unlocked), active=set())

    try:
        from edgecaster.systems import entity_body as entity_body_system

        specs = entity_body_system.build_body_node_specs(owner_ent)
    except Exception:
        specs = {}

    if not specs:
        return []

    locked: List[Tuple[str, int]] = []
    for spec in specs.values():
        full_id = str(getattr(spec, "full_id", "") or "")
        if not full_id or full_id in unlocked:
            continue
        locked.append((full_id, int(full_id.count("."))))

    locked.sort(key=lambda row: (row[1], chakra_display_name(row[0]), row[0]))
    return [
        full_id
        for (full_id, _depth) in locked
        if can_unlock_full_chakra_id(state, full_id)
    ]


# =============================================================================
# CHAKRA OPERATIONS
# =============================================================================

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


def _build_seed_from_realized_body_tree(
    chakra_state: ChakraState,
    *,
    actor: Any,
    game: Any,
    require_root: bool,
) -> Optional[ChakraGeneratorSeed]:
    """Build a seed directly from the realized body-node entity tree.

    This path keeps the live entity tree authoritative for geometry while still
    using the explicit ChakraState argument for active-node and alignment
    semantics. That avoids preview/runtime drift while the actor-side
    ChakraState facade is still being migrated away elsewhere.
    """
    if actor is None or game is None:
        return None

    graph = getattr(game, "entity_graph", None)
    if graph is None:
        return None

    actor_id = str(getattr(actor, "entity_id", "") or getattr(actor, "id", "") or "").strip()
    if not actor_id:
        return None

    try:
        from edgecaster.systems import entity_lifecycle as entity_lifecycle_system
    except Exception:
        return None

    try:
        root_child_ids = list(graph.get_children(actor_id, socket_id="body"))
    except Exception:
        root_child_ids = []
    if not root_child_ids:
        return None

    alignments = getattr(chakra_state, "alignments", {}) or {}
    if not isinstance(alignments, dict):
        alignments = {}

    positions_all: Dict[str, Vec2] = {}
    full_edges: List[Tuple[str, str]] = []
    queue = list(root_child_ids)
    visited: Set[str] = set()

    while queue:
        entity_id = str(queue.pop(0))
        if not entity_id or entity_id in visited:
            continue
        visited.add(entity_id)

        try:
            child_ids = list(graph.get_children(entity_id, socket_id="body"))
        except Exception:
            child_ids = []
        queue.extend(child_ids)

        runtime_obj = entity_lifecycle_system.find_runtime_entity(game, entity_id)
        if runtime_obj is None:
            continue

        tags = getattr(runtime_obj, "tags", None) or {}
        if not isinstance(tags, dict) or not tags.get("body_node"):
            continue

        full_id = str(tags.get("body_full_id", "") or "").strip()
        if not full_id:
            continue

        pos: Optional[Vec2] = None
        raw_float_pos = tags.get("body_float_pos")
        if isinstance(raw_float_pos, (tuple, list)) and len(raw_float_pos) >= 2:
            try:
                pos = (float(raw_float_pos[0]), float(raw_float_pos[1]))
            except Exception:
                pos = None
        if pos is None:
            raw_abs_pos = getattr(runtime_obj, "abs_pos", None)
            if isinstance(raw_abs_pos, (tuple, list)) and len(raw_abs_pos) >= 2:
                try:
                    pos = (float(raw_abs_pos[0]), float(raw_abs_pos[1]))
                except Exception:
                    pos = None
        if pos is None:
            continue

        try:
            local_scale = float(tags.get("body_local_scale", 1.0) or 1.0)
        except Exception:
            local_scale = 1.0

        raw_align = alignments.get(full_id)
        if isinstance(raw_align, (tuple, list)) and len(raw_align) >= 2:
            try:
                pos = (
                    float(pos[0]) + float(raw_align[0]) * local_scale * 0.5,
                    float(pos[1]) + float(raw_align[1]) * local_scale * 0.5,
                )
            except Exception:
                pass

        positions_all[full_id] = pos

        parent_full_id = str(tags.get("body_parent_full_id", "") or "").strip()
        if parent_full_id:
            full_edges.append((parent_full_id, full_id))

    if not positions_all:
        return None

    active_set = set(getattr(chakra_state, "active", set()) or set())
    root_id = getattr(chakra_state, "pattern_root", None)
    if root_id not in active_set:
        if require_root:
            raise ValueError("Select an active chakra as the pattern root first.")
        active_sorted = sorted(active_set)
        if not active_sorted:
            raise ValueError("Need at least 2 active chakras to form a generator.")
        root_id = active_sorted[0]

    active_nodes, compact_edges = get_compact_active_graph(
        full_edges,
        active_set,
        root_id=root_id,
    )
    positions = {
        node_id: positions_all[node_id]
        for node_id in active_nodes
        if node_id in positions_all
    }

    if root_id not in positions:
        raise ValueError("Pattern root not found in chakra pattern.")

    candidates = [node_id for node_id in active_nodes if node_id != root_id and node_id in positions]
    if not candidates:
        raise ValueError("Need at least 2 connected chakras to form a generator.")

    rx, ry = positions[root_id]

    def _distance_squared(node_id: str) -> float:
        px, py = positions[node_id]
        dx = px - rx
        dy = py - ry
        return dx * dx + dy * dy

    terminus_id = max(candidates, key=_distance_squared)
    verts, edges, node_order, base_len = normalized_custom_graph_from_positions(
        positions,
        compact_edges,
        root_id=str(root_id),
        terminus_id=str(terminus_id),
    )
    return ChakraGeneratorSeed(
        positions=positions,
        compact_edges=compact_edges,
        root_id=str(root_id),
        terminus_id=str(terminus_id),
        verts=verts,
        edges=edges,
        node_order=node_order,
        base_len=base_len,
    )


def _build_seed_from_body_specs(
    chakra_state: ChakraState,
    *,
    actor: Any,
    require_root: bool,
) -> Optional[ChakraGeneratorSeed]:
    """Build a seed from deterministic body-node specs without realizing entities.

    This is the preferred actor fallback when no realized body tree or richer
    geometry query is available yet. It keeps actor-oriented seed generation on
    the shared `entity_body` substrate instead of re-walking raw body_schema
    dicts inside this module.
    """
    if actor is None:
        return None

    try:
        from edgecaster.systems import entity_body as entity_body_system

        specs = entity_body_system.build_body_node_specs(actor)
    except Exception:
        specs = {}
    if not isinstance(specs, dict) or not specs:
        return None

    alignments = getattr(chakra_state, "alignments", {}) or {}
    if not isinstance(alignments, dict):
        alignments = {}

    positions_all: Dict[str, Vec2] = {}
    full_edges: List[Tuple[str, str]] = []

    for spec in specs.values():
        full_id = str(getattr(spec, "full_id", "") or "").strip()
        if not full_id:
            continue
        try:
            pos = (
                float(getattr(spec, "abs_pos", (0.0, 0.0))[0]),
                float(getattr(spec, "abs_pos", (0.0, 0.0))[1]),
            )
        except Exception:
            continue
        try:
            local_scale = float(getattr(spec, "local_scale", 1.0) or 1.0)
        except Exception:
            local_scale = 1.0

        raw_align = alignments.get(full_id)
        if isinstance(raw_align, (tuple, list)) and len(raw_align) >= 2:
            try:
                pos = (
                    float(pos[0]) + float(raw_align[0]) * local_scale * 0.5,
                    float(pos[1]) + float(raw_align[1]) * local_scale * 0.5,
                )
            except Exception:
                pass

        positions_all[full_id] = pos

        parent_full_id = str(getattr(spec, "parent_full_id", "") or "").strip()
        if parent_full_id:
            full_edges.append((parent_full_id, full_id))

    if not positions_all:
        return None

    active_set = set(getattr(chakra_state, "active", set()) or set())
    root_id = getattr(chakra_state, "pattern_root", None)
    if root_id not in active_set:
        if require_root:
            raise ValueError("Select an active chakra as the pattern root first.")
        active_sorted = sorted(active_set)
        if not active_sorted:
            raise ValueError("Need at least 2 active chakras to form a generator.")
        root_id = active_sorted[0]

    active_nodes, compact_edges = get_compact_active_graph(
        full_edges,
        active_set,
        root_id=root_id,
    )
    positions = {
        node_id: positions_all[node_id]
        for node_id in active_nodes
        if node_id in positions_all
    }

    if root_id not in positions:
        raise ValueError("Pattern root not found in chakra pattern.")

    candidates = [node_id for node_id in active_nodes if node_id != root_id and node_id in positions]
    if not candidates:
        raise ValueError("Need at least 2 connected chakras to form a generator.")

    rx, ry = positions[root_id]

    def _distance_squared(node_id: str) -> float:
        px, py = positions[node_id]
        dx = px - rx
        dy = py - ry
        return dx * dx + dy * dy

    terminus_id = max(candidates, key=_distance_squared)
    verts, edges, node_order, base_len = normalized_custom_graph_from_positions(
        positions,
        compact_edges,
        root_id=str(root_id),
        terminus_id=str(terminus_id),
    )
    return ChakraGeneratorSeed(
        positions=positions,
        compact_edges=compact_edges,
        root_id=str(root_id),
        terminus_id=str(terminus_id),
        verts=verts,
        edges=edges,
        node_order=node_order,
        base_len=base_len,
    )


def build_chakra_generator_seed(
    chakra_state: ChakraState,
    *,
    body_schema: Optional[Dict[str, Any]] = None,
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
    # Realized actor-body tree path: prefer body-node entities when available.
    # This keeps runtime casts and the chakra-scene preview on the shared entity
    # substrate, while still honoring the explicit ChakraState argument for
    # active nodes, alignments, and pattern-root choice.
    if actor is not None and game is not None:
        try:
            body_tree_seed = _build_seed_from_realized_body_tree(
                chakra_state,
                actor=actor,
                game=game,
                require_root=require_root,
            )
            if body_tree_seed is not None:
                return body_tree_seed
        except ValueError:
            # If the live body tree cannot satisfy the request yet, keep falling
            # through so generic component or schema-based fallback can still
            # rescue partially migrated actors.
            pass

    # Entity tree path: try query_normalized_pattern for entities whose
    # chakra_component carries positioned nodes.  For body-anatomy actors this
    # works when body nodes were expanded at spawn (Batch 1 A1) and the
    # external_child_root active flags were synced from ChakraState (Batch 2 A3).
    # Actors with only one active chakra (< 2 nodes) fall through to the
    # body-schema path below; so do actors with deeper body trees (>1 level).
    # Body-anatomy node offsets are sub-tile floats that collapse to the same
    # integer grid cell — entity abs_pos alone is not sufficient geometry.
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
                    from edgecaster.systems import entity_geometry as entity_geometry_system

                    # Geometry query: succeeds when the chakra_component has ≥2 active
                    # nodes with distinct positions; returns empty if not (falls through).
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

    # Shared deterministic body-spec path for actor anatomy when the body tree
    # is not realized yet. This keeps actor-oriented seed generation on the
    # same substrate used by entity expansion and body-view queries instead of
    # dropping straight to raw body_schema recursion.
    if actor is not None:
        try:
            body_spec_seed = _build_seed_from_body_specs(
                chakra_state,
                actor=actor,
                require_root=require_root,
            )
            if body_spec_seed is not None:
                return body_spec_seed
        except ValueError:
            pass

    # Schema fallback path
    if not body_schema and actor is not None:
        try:
            from edgecaster.prototypes import resolve_body_schema
            body_schema = resolve_body_schema(actor)
        except Exception:
            body_schema = getattr(actor, "body_schema", {})

    if not body_schema:
        body_schema = {}

    positions, compact_edges, root_id, terminus_id = get_active_chakra_generator_graph(
        body_schema,
        chakra_state,
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


def build_chakra_generator_seed_for_actor(
    actor: Any,
    *,
    game: Any | None = None,
    chakra_state: Any | None = None,
    require_root: bool = True,
) -> ChakraGeneratorSeed:
    """Build a chakra generator seed through the preferred actor query path."""
    if actor is None:
        raise ValueError("No actor to generate pattern from.")

    state_like = chakra_state
    if state_like is None:
        try:
            from edgecaster.systems import chakra_items as chakra_items_system

            state_like = chakra_items_system.effective_chakra_view(game, actor)
        except Exception:
            state_like = None

    if state_like is None:
        try:
            from edgecaster.systems import chakra_items as chakra_items_system

            state_like = chakra_items_system.structural_chakra_view(actor)
        except Exception:
            state_like = None

    if state_like is None:
        raise ValueError("No chakra state found.")

    return build_chakra_generator_seed(
        state_like,
        require_root=require_root,
        actor=actor,
        game=game,
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



def get_resonance_modifiers(bonuses: List[str]) -> ChakraModifiers:
    """Aggregate modifiers from active resonance bonuses."""
    mods = ChakraModifiers()
    for bonus in bonuses:
        effect = RESONANCE_EFFECTS.get(bonus)
        if effect:
            mods.apply(effect)
    return mods



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
