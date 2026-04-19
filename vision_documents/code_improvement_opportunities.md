# Code Improvement Opportunities: Entity + Chakra Unification

Based on the goals established in the `entity_chakra_unification_master_plan.txt` and an analysis of the active codebase (specifically targeting `# [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]` markers), the following areas present concrete opportunities for improvement and cleanup. The primary objective of these improvements is to solidify the **single, unified hierarchical entity graph** architecture by eliminating temporary migration bridges and parallel ontologies.

## 1. Eliminate Parallel Entity Stores
Currently, several structures maintain duplicate or parallel sources of truth outside the unified entity graph. 
*   **`LevelState.actors` and `LevelState.entities` (`game.py`)**: These parallel dictionaries should be replaced entirely by unified entity graph storage.
*   **`WorldEntityIndex` (`world_entity_index.py`, `game.py`)**: Operating as a macro-scale parallel cache. Macro entities should be folded directly into the unified graph, and queries should read from a shared realization path.
*   **`AttentionCellStore` (`attention.py`, `game.py`)**: Used as a transitional attention cache. The end-state should rely on the unified graph and a unified quadtree index.
*   **`POIRegistry` (`game.py`)**: Currently a transitional semantic registry. This should be replaced by `semantic_id` queries executed directly against the entity graph.

## 2. Solidify Graph-Authoritative Inventories
While new NPC and player spawning paths now set inventory graph authority, older paths and systems still rely on list-backed storage.
*   **`self.inventories` dict (`game.py`)**: An ad-hoc dictionary mapping owner IDs to a list of carried entities. Inventory containment should strictly be a subset of containment graph queries.
*   **Trade Subsystem (`trade.py`)**: Trade logic still targets list-backed stores for transfers and splits. Final cutover must ensure trades interact directly with the graph-authoritative storage.

## 3. Deprecate `ChakraState` and `body_schema` Walkers
The older actor-centric schema system exists as a fallback but creates significant architectural friction for generalized entity interactions.
*   **Gameplay/Pattern Evaluation (`chakras.py`, `chakra_items.py`, `pattern_runtime.py`)**: These systems still use the `ChakraState` facade and manual `body_schema` recursive walkers to answer queries. These must be migrated to use `entity_geometry` and `entity_body` queries, reading from the true `ChakraComponent` data.
*   **UI Scene Rendering (`chakra_scene.py`, `inventory_scene.py`)**: The UI still relies on body schema walkers to render silhouettes and zoom pathways. These should be cut over to graph/geometry queries once the actor-body graph becomes the absolute UI authority.

## 4. Converge POI Realization Pipeline
*   **`poi_worldgen.py`**: Although it successfully replaced the deprecated `poi_spawning.py`, its orchestration logic still sits conceptually outside the canonical attention/entity-graph resolver. The logic here needs to be folded completely into the standard realization pipeline so that buildings and sites spawn identically to everything else.

## 5. Finalize Immutable `entity_id` Persistence
The bridge between legacy lineage identity and strict entity ID identity is still maintained in multiple places to avoid data loss during the migration.
*   **Fallback Persistence Hooks (`game.py`, `entity_graph_ops.py`, `attention.py`)**: Wrappers and write-through helpers are maintaining backwards compatibility by mirroring containment/ownership metadata into legacy `entity_state` fields or relying on `lineage` fallback reads. These bridges should be collapsed down to strict `entity_id`-only interactions.
*   **Snapshot Payloads (`entity_snapshots.py`)**: The existing `last_known_*` payload shape is a migration bridge. This must be upgraded to typed entity/component persistence deltas.

## 6. Promote Transient Effects to First-Class Entities
*   **Ad-hoc LevelState trackers (`game.py`)**: Runtime states like `choking_vines_state`, `rune_choking_vines_state`, and `thrown_knives_state` are currently tracked explicitly as parallel variables inside `LevelState`. According to the new ontology, these transient effects should be spun up as first-class deterministic entities with standard Time-To-Live (TTL) states and resolved using standard entity graph logic.