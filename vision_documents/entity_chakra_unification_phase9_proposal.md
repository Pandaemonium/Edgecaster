# Entity + Chakra Unification: Phase 9 & Simplification Proposal

Audience: Core Collaborators
Status: Draft / Proposal (noncanonical; useful pieces incorporated into the master plan on 2026-04-19)
Related docs: `entity_chakra_unification_master_plan.txt`, `entity_chakra_unification_overview.md`

Note (2026-04-19):
- Useful parts of this proposal were folded into
  `entity_chakra_unification_master_plan.txt`.
- The master plan is the canonical execution document; this file remains
  proposal/backlog material rather than an active contract.

This document outlines two major proposals to further elevate and simplify the Entity + Chakra unification effort. It identifies a lingering contradiction in the current data contracts and proposes a path to resolve it, followed by a proposed "Phase 9" to bring the remaining metaphysical and environmental systems into the unified graph.

---

## Proposal 1: Dissolve the "Second Ontology" (Flatter Data Contracts)

### The Lingering Contradiction
The unification vision clearly states: **"Every chakra IS an entity... Chakras are not a second ontology."** 
However, the current master plan defines the target state for a `ChakraComponent` as a sub-graph containing its own `ChakraNode` and `ChakraEdge` records. 

Because recent progress has successfully made body parts (like hands and arms) into **real child `Entity` records in the `EntityGraph`**, keeping `ChakraNode` and `ChakraEdge` inside a `ChakraComponent` creates a redundant, parallel graph:
1. **The Entity Graph:** Knows that the "Hand Entity" is a child of the "Arm Entity" (via `parent_entity_id` / `socket_id`).
2. **The Chakra Graph:** Knows that the "Hand Node" connects to the "Arm Node" (via `ChakraEdge` records inside a `ChakraComponent`).

Maintaining both means we must constantly synchronize the `EntityGraph` topology with the `ChakraComponent` topology.

### The Elegant Solution: The Entity Graph IS the Chakra Graph
To achieve true unification, the `ChakraComponent` should be stripped of its graph responsibilities. 

1. **Nodes become Entities:** The `Entity` itself acts as the single point of truth in space. We don't need a `ChakraNode`.
2. **Edges become EntityGraph Relations:** Routing, resonance flow, and structural connections become explicitly typed edges in the `EntityGraph` (e.g., `relation_type="chakra_flow"`) or simply rely on the existing hierarchical `parent_entity_id` and `socket_id`.
3. **ChakraComponent flattens to `ChakraChannels`:** The component on the Entity drops the dictionary of nodes and edges entirely. It becomes a simple, flat dataclass holding only the scalar values for that specific entity:

```python
@dataclass
class ChakraChannels:
    active: bool
    mass: float
    hp: float
    coherence: float
    resonance: float
    alignment_offset: Tuple[float, float]
    # No nodes, no edges. Just the raw stats for this entity.
```

### Benefits of this Simplification:
*   **Zero Synchronization:** When a limb is severed or an item is unequipped, detaching the `Entity` from the `EntityGraph` instantly updates the chakra topology. There is no secondary `ChakraComponent` graph to patch or rebuild.
*   **Simpler Reducer Pipeline:** The reducer doesn't traverse a bespoke component dictionary. It simply traverses the `EntityGraph` (which it already does to find children), reads the `ChakraChannels` from each entity, and applies the rules.
*   **True Scale Invariance:** A city district (Entity) connected to a city (Entity) via the EntityGraph inherently supports the exact same channel reduction logic as a finger attached to a hand.

---

## Proposal 2: Phase 9 - Metaphysical & Environment Unification

Based on the core principle—**"Everything with meaningful gameplay identity is an entity"**—there are several major systems currently bypassing the entity graph. Treating these as later-slice targets can continue the unification effort without forcing them into one monolithic phase.

### 1. Transient Environmental & Fractal Effects
*   **Current State:** `LevelState` in `game.py` holds hardcoded ad-hoc fields for active environmental patterns: `fern_active`, `fern_growth_tips`, `fern_accum` (Barnsley fern mechanics), and `acidic_pattern` (Corrosive Melt state).
*   **Unified Vision:** Active fractal/weather patterns should be spawned as transient `Entity` objects in the `EntityGraph`. Their spread, density, and duration are tracked as `ChakraChannels` on the entity, and their area-of-effect is mapped via edges to the tiles or actors they are currently touching.

### 2. Gods and Favor (`gods.py`)
*   **Current State:** `game.py` tracks Gods via a parallel registry (`self.god_registry`) and tracks the player's devotion via a flat dictionary (`self.god_favor`).
*   **Unified Vision:** The master plan notes that "Factions become entities." Gods should receive the exact same treatment. A God is a macro-Entity in the world graph. A player's "Favor" with a god is simply a channel value on an edge connecting the Player Entity to the God Entity.

### 3. Regional Danger & Difficulty
*   **Current State:** `game.py` tracks `self.zone_difficulty_overrides` in a separate dictionary, and `LevelState` holds a scalar `danger_value`.
*   **Unified Vision:** As the world map (Continents -> Regions -> Cities -> Zones) becomes a unified Entity hierarchy, "Danger" or "Difficulty" shouldn't be an overriding dictionary. It becomes a `ChakraChannel` on the Region/Zone Entity. The reducer pipeline could even calculate it dynamically based on the aggregate danger of the enemy entities currently contained within that graph node.

### 4. Bespoke Quest/Room States (`LabState`)
*   **Current State:** `LevelState` holds a specific `lab_state: Optional["LabState"]`.
*   **Unified Vision:** Custom quest rooms or unique mechanics shouldn't require custom dataclass fields on the generic level object. The "Lab" should just be an Entity (like a Building or Room) whose component channels or tags track the state of its internal puzzle. This ensures it automatically benefits from the unified save/load/persistence framework.

### 5. Fractal Blade Runtime State (`blade_states`)
*   **Current State:** `game.py` tracks `self.blade_states` as a separate dictionary mapping actor IDs to `BladeState` objects.
*   **Unified Vision:** A fractal blade should just be an Entity (item) equipped by the Actor. The `BladeState` (its current reach, damage, and phantom nodes) should simply be channels or tags on that Entity, fully managed by the standard `EntityGraph`.

### 6. Projectile & Siege States (`thrown_knives_state`, `rune_anchor_siege`, `seal_trial`)
*   **Current State:** Stored as bespoke fields directly on `LevelState` in `game.py`.
*   **Unified Vision:** Thrown knives are physical objects moving through space and should be spawned as transient Entities with a velocity/trajectory component. Siege and trial states should similarly be macro-Entities spawned into the zone that track their own progression, rather than cluttering the generic level state object.
