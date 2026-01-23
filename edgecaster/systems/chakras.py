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
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# Type alias for 2D vectors
Vec2 = Tuple[float, float]


# =============================================================================
# CHAKRA STATE
# =============================================================================

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

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for saving."""
        return {
            "unlocked": list(self.unlocked),
            "active": list(self.active),
            "alignments": {k: list(v) for k, v in self.alignments.items()},
            "generators": dict(self.generators),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChakraState":
        """Deserialize from dict."""
        if not data:
            return cls()
        return cls(
            unlocked=set(data.get("unlocked", ["torso"])),
            active=set(data.get("active", ["torso"])),
            alignments={
                k: tuple(v) for k, v in data.get("alignments", {}).items()
            },
            generators=dict(data.get("generators", {})),
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

    # Get prototype data
    if proto_index is None:
        try:
            from edgecaster.prototypes import resolve_proto
            spec = resolve_proto(proto_id)
        except Exception:
            return False
    else:
        spec = proto_index.get(proto_id, {})

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


def lock_chakra(chakra_state: ChakraState, node_id: str) -> bool:
    """
    Lock a previously unlocked chakra.

    This removes it from both unlocked and active sets.
    Used for temporary chakra effects or curses.

    Returns:
        True if was unlocked (and is now locked), False otherwise
    """
    if node_id not in chakra_state.unlocked:
        return False

    chakra_state.unlocked.discard(node_id)
    chakra_state.active.discard(node_id)
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


def set_chakra_alignment(
    chakra_state: ChakraState,
    node_id: str,
    dx: float,
    dy: float
) -> None:
    """
    Set the alignment offset for a chakra.

    Alignment offsets adjust the chakra's position from its default
    body layout position, allowing fine-tuning of the generated pattern.

    Args:
        chakra_state: The chakra state to modify
        node_id: The chakra to align
        dx: X-axis offset (typically -0.5 to 0.5)
        dy: Y-axis offset (typically -0.5 to 0.5)
    """
    chakra_state.alignments[node_id] = (float(dx), float(dy))


def clear_chakra_alignment(chakra_state: ChakraState, node_id: str) -> None:
    """Remove any alignment offset for a chakra."""
    chakra_state.alignments.pop(node_id, None)


# =============================================================================
# ALIGNMENT WOBBLE (DEXTERITY-BASED)
# =============================================================================

def apply_alignment_wobble(
    chakra_state: ChakraState,
    dexterity: int = 0,
    seed: Optional[int] = None
) -> None:
    """
    Apply random wobble to all active chakra alignments based on dexterity.

    Higher dexterity = less wobble = more precise pattern casting.
    This simulates the "shakiness" of channeling energy through chakras.

    Args:
        chakra_state: The chakra state to modify
        dexterity: Actor's dexterity stat (0-20 typical range)
        seed: Optional random seed for reproducible wobble

    Wobble formula:
        max_wobble = 0.3 - (dexterity * 0.015)
        Clamped to [0.02, 0.3] range

    At 0 dex: up to 0.30 units of wobble (very shaky)
    At 10 dex: up to 0.15 units of wobble (moderate)
    At 20 dex: up to 0.02 units of wobble (very precise)
    """
    if seed is not None:
        random.seed(seed)

    # Calculate max wobble based on dexterity
    max_wobble = 0.3 - (dexterity * 0.015)
    max_wobble = max(0.02, min(0.3, max_wobble))

    for node_id in chakra_state.active:
        # Get existing alignment or (0, 0)
        existing = chakra_state.alignments.get(node_id, (0.0, 0.0))

        # Add random wobble
        dx = existing[0] + random.uniform(-max_wobble, max_wobble)
        dy = existing[1] + random.uniform(-max_wobble, max_wobble)

        chakra_state.alignments[node_id] = (dx, dy)


# =============================================================================
# POSITION EXTRACTION
# =============================================================================

def get_chakra_world_positions(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    base_scale: float = 5.0,
    include_inactive: bool = False
) -> Dict[str, Vec2]:
    """
    Convert chakra node positions to world-space coordinates.

    This walks the body tree recursively, accumulating position and scale
    transforms to produce final world positions for each chakra.

    The algorithm:
    1. Start at root node at origin (0, 0) with base_scale
    2. For each child node:
       - Position = parent_pos + (child_layout * current_scale)
       - Scale = current_scale * child_size
    3. Apply alignment offsets from chakra_state

    Args:
        body_schema: The actor's body schema
        chakra_state: Current chakra state (for alignments)
        base_scale: World-space scale factor (default 5.0 tiles)
        include_inactive: If True, include all unlocked chakras, not just active

    Returns:
        Dict mapping node_id to (x, y) world position

    Example:
        positions = get_chakra_world_positions(actor.body_schema, actor.chakra_state)
        # {"torso": (0, 0), "shoulder": (-1.5, 0.8), "shoulder_m": (1.5, 0.8), ...}
    """
    nodes = _get_nodes(body_schema)
    root_id = _get_root_node_id(body_schema)

    if not nodes or not root_id:
        return {}

    # Determine which nodes to include
    target_nodes = chakra_state.active if not include_inactive else chakra_state.unlocked

    positions: Dict[str, Vec2] = {}

    def walk(node_id: str, parent_pos: Vec2, current_scale: float) -> None:
        """Recursively walk the tree, computing positions."""
        if node_id not in nodes:
            return

        node = nodes[node_id]
        layout = _get_node_layout(node)
        size = _get_node_size(node)

        # Compute this node's position
        x = parent_pos[0] + (layout[0] * current_scale)
        y = parent_pos[1] + (layout[1] * current_scale)

        # Apply alignment offset if present
        if node_id in chakra_state.alignments:
            align = chakra_state.alignments[node_id]
            x += align[0] * current_scale * 0.5  # Scale alignment by current zoom
            y += align[1] * current_scale * 0.5

        # Store if this is a target node
        if node_id in target_nodes:
            positions[node_id] = (x, y)

        # Recurse to children with scaled-down coordinate system
        child_scale = current_scale * size
        for child_id in _get_node_children(node):
            walk(child_id, (x, y), child_scale)

    # Start walk from root
    walk(root_id, (0.0, 0.0), base_scale)

    return positions


# =============================================================================
# PATTERN GENERATION
# =============================================================================

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

    nodes = _get_nodes(body_schema)
    positions = get_chakra_world_positions(body_schema, chakra_state, base_scale)

    if not positions:
        # Return empty pattern
        return Pattern()

    pattern = Pattern()
    node_to_idx: Dict[str, int] = {}

    # Add vertices for each active chakra
    for node_id, pos in positions.items():
        idx = pattern.add_vertex(pos, color="chakra", power=1.0)
        node_to_idx[node_id] = idx

    # Add edges following body tree structure
    # An edge exists between parent and child if BOTH are active
    for node_id in positions:
        parent_id = _find_parent_node_id(nodes, node_id)
        if parent_id and parent_id in node_to_idx:
            pattern.add_edge(
                node_to_idx[parent_id],
                node_to_idx[node_id],
                color="chakra",
                weight=1.0
            )

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
# UTILITY FUNCTIONS
# =============================================================================

def count_active_chakras(chakra_state: ChakraState) -> int:
    """Count the number of active chakras."""
    return len(chakra_state.active)


def count_unlocked_chakras(chakra_state: ChakraState) -> int:
    """Count the number of unlocked chakras."""
    return len(chakra_state.unlocked)


def get_unlockable_chakras(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState,
    proto_index: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Get list of chakras that can currently be unlocked.

    Returns node IDs for chakras that:
    1. Exist in body schema
    2. Are not yet unlocked
    3. Have all gating prerequisites met
    """
    nodes = _get_nodes(body_schema)
    unlockable: List[str] = []

    for node_id in nodes:
        if can_unlock_chakra(body_schema, chakra_state, node_id, proto_index):
            unlockable.append(node_id)

    return unlockable


def get_chakra_info(
    body_schema: Dict[str, Any],
    node_id: str
) -> Dict[str, Any]:
    """
    Get descriptive information about a chakra node.

    Returns dict with:
    - name: Display name
    - proto: Prototype ID
    - position: (x, y) layout position
    - size: Scale factor
    - children: List of child node IDs
    """
    nodes = _get_nodes(body_schema)
    node = nodes.get(node_id)

    if not node:
        return {"name": node_id, "proto": "", "position": (0, 0), "size": 0.5, "children": []}

    return {
        "name": node_id.replace("_", " ").title(),
        "proto": node.get("proto", ""),
        "position": _get_node_layout(node),
        "size": _get_node_size(node),
        "children": _get_node_children(node),
    }


# =============================================================================
# RESONANCE BONUSES (Optional Enhancement)
# =============================================================================

def check_resonance_bonuses(
    body_schema: Dict[str, Any],
    chakra_state: ChakraState
) -> List[str]:
    """
    Check for special chakra combinations that grant bonuses.

    Resonance occurs when certain chakra patterns are active together.

    Returns:
        List of resonance bonus IDs that are currently active

    Current resonance patterns:
    - "bilateral_arms": Both arm chakras active (shoulder + shoulder_m)
    - "full_hand": All 5 finger chakras active
    - "grounded": Both leg chakras active (thigh + thigh_m)
    - "centered": Torso + chest + back all active
    """
    bonuses: List[str] = []
    active = chakra_state.active

    # Bilateral arms (top-level "arm" nodes or sub-schema "shoulder" nodes)
    if ("arm" in active and "arm_m" in active) or ("shoulder" in active and "shoulder_m" in active):
        bonuses.append("bilateral_arms")

    # Full hand (any hand) - sub-schema node IDs
    hand_fingers = {"thumb", "index", "middle", "ring", "pinky"}
    if hand_fingers.issubset(active):
        bonuses.append("full_hand")

    # Mirrored hand
    hand_fingers_m = {"thumb_m", "index_m", "middle_m", "ring_m", "pinky_m"}
    if hand_fingers_m.issubset(active):
        bonuses.append("full_hand_m")

    # Grounded (top-level "leg" nodes or sub-schema "thigh" nodes)
    if ("leg" in active and "leg_m" in active) or ("thigh" in active and "thigh_m" in active):
        bonuses.append("grounded")

    # Centered - "body" is the torso/core at top level
    if "body" in active:
        bonuses.append("centered")

    return bonuses
