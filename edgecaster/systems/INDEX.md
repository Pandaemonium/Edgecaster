# Systems Index

Purpose: map the main module clusters in `edgecaster/systems/` so agents can find the right ownership area quickly.
Status: active-plan
Last verified: 2026-04-23 (updated with Track D render/attention SpatialIndex reader migration)
Canonical for: systems-folder navigation and ownership hints
Related docs: `edgecaster/systems/AGENTS.md`, `vision_documents/architecture.txt`, `vision_documents/spring_cleaning.txt`
Related code: `edgecaster/systems/`
Supersedes: none

## Read First

- `AGENTS.md`
- `../../vision_documents/architecture.txt`
- this file

## Core Clusters

### Actions, Input, And Turn Flow

- `actions.py`
- `action_runner.py`
- `abilities.py`
- `scheduling.py`
- `targeting.py`
- `previews.py`
- `tooltips.py`

### Combat And Ability Resolution

- `combat.py`
- `combat_actions.py`
- `damage_policy.py`
- `blade_runtime.py`
- `pattern_runtime.py`
- `pattern_state.py` *(extracted from game.py; canonical rune pattern state bridge — ABS-space, per-depth)*
- `pattern_ops.py`
- `lorenz_aura.py`
- `fern_growth.py`

### Chakra, Gods, And Rune Systems

- `chakras.py` *(legacy body-schema math still lives here, but actor-facing reads are getting thinner: `build_chakra_generator_seed_for_actor(...)` is the preferred runtime/scene generator-seed entrypoint, `get_active_chakra_generator_graph_for_entity(...)` centralizes the shared body-spec fallback graph read for unexpanded actors, `can_unlock_full_chakra_id_from_unlocked(...)` / `can_unlock_chakra_for_entity_from_unlocked(...)` now cover unlock checks without forcing larger facade snapshots, and `list_visible_chakra_nodes_for_entity_from_unlocked(...)` gives scenes a shared ordered body-spec node list instead of a local visibility ladder)*
- `chakra_effects.py`
- `chakra_items.py` *(component-first chakra read/write helpers; `effective_chakra_view(...)` / `effective_chakra_projection(...)` are the preferred runtime query surfaces, `structural_chakra_projection(...)` / `structural_chakra_view(...)` are the no-item-overlay runtime reads for lifecycle/seed callers, `coerce_chakra_view_state(...)` is the preferred bridge from snapshot-like input onto the thin query model, and write helpers now mutate component authority directly instead of maintaining a parallel actor-side chakra cache)*
- `chakra_content.py`
- `gods.py`
- `god_abilities.py`
- `seal_trials.py`
- `rune_anchor_sieges.py`
- `rune_audio.py`

### World, Entities, And Observation

- `coords.py`
- `entity_ops.py`
  *(shared realized-entity query surface plus tile/footprint/status helpers; Track B callers in
  simulation, scenes, and render code should prefer this over direct `level.actors` /
  `level.entities` reads)*
- `entity_identity.py` *(canonical `stable_int_hash` FNV-1a; shared by aggregate_resolution, poi_worldgen, site_placement)*
- `entity_body.py`
- `body_view_queries.py` *(shared read-only body/entity view queries for Chakra Scene, Inventory Scene, and other UI callers that should not own schema/graph fallback ladders; schema fallbacks now annotate explicit `zoomable` metadata so scenes do not need to re-resolve authored schemas just to decide branch behavior, branch metadata helpers provide shared gating-chain / child-count reads for tooltips and similar UI affordances, and `visible_body_nodes_for_owner(...)` now centralizes unlocked-branch filtering for body-node list views)*
- `entity_graph_ops.py`
- `entity_snapshots.py` *(shared deterministic-entity snapshot bridge; snapshot persist/apply is now entity_id-authoritative and no longer threads legacy `lineage_id` parameters through the shared runtime bridge)*
- `entity_lifecycle.py`
- `entity_geometry.py`
- `spatial_index.py` *(shared ABS rect / semantic / kind / realization-state index with filtered rect queries and tag queries; currently mirrors attention-staged, world-proxy, and POIRegistry entities while legacy stores are migrated; render, attention sync, inspection, distant look, spatial music, legendary-lair/rune-anchor lookup, dialogue site lookup, and POIRegistry compatibility spatial reads now prefer it first)*
- `attention.py` *(attention lifecycle and render candidate assembly; Track D readers now query `SpatialIndex` before legacy attention/world-index fallbacks, staged-entity reads now go through `AttentionCellStore` helper methods instead of open-coding direct `.entities` access, and aggregate/detail promotion now uses explicit stage/mirror/promote/drop helper steps instead of store-shaped local ad hoc logic)*
- `aggregate_resolution.py`
- `world_entity_index.py`
- `zones.py`
- `active_zones.py` *(extracted from game.py; active zone coordinate queries, prewarm, move_actor_to_abs)*
- `session.py` *(extracted from game.py; current_level / ensure_player_level_binding / get_player / is_player_alive)*
- `render_query.py`

### Spawning, Sites, And Worldgen Runtime

- `spawning.py`
- `ambient_spawns.py`
- `poi_registry.py` *(transitional POI spec/content-state registry; mirrors POI specs into `SpatialIndex`, and its remaining spatial query methods are now compatibility wrappers over that shared index when attached while content-state persistence remains here)*
- `poi_worldgen.py` *(unified POI realization entrypoint; `poi_spawning.py` deleted 2026-04-13; tagged [LEGACY_DELETE] pending full resolver/entity-graph pipeline convergence)*
- `site_placement.py`
- `sites.py` *(legacy — no longer the main source of truth for prototype-driven site placement; prefer `site_placement.py` + `content/site_types.yaml`)*
- `overmap.py`
- `legendaries.py`
- `difficulty.py`

### Inventory, Equipment, Economy, And Social Systems

- `inventory.py` *(graph-authoritative inventory queries, recursive inventory-tree walkers, equipment socket containment, and shared add/remove helpers)*
- `equipment.py`
- `equip_rules.py`
- `item_grants.py`
- `trade.py` *(merchant/player inventory reads go through the shared inventory query surface)*
- `reputation.py`
- `quests.py`
- `factions.py`

### Diagnostics And Support

- `inspection.py`
- `telemetry.py`
- `perf_profiler.py`
- `lighting.py`
- `footprints.py`
- `params.py`
- `deferred.py`

## High-Churn Modules

These modules are especially worth reading carefully before editing:

- `attention.py`
- `combat_actions.py`
- `actions.py`
- `blade_runtime.py`
- `pattern_runtime.py`
- `seal_trials.py`
- `rune_anchor_sieges.py`

## Maintenance Rules

- Update this file when a new system module is added.
- Update this file when ownership boundaries move or a module changes clusters.
- Update related tests and `vision_documents/architecture.txt` when a structural change affects how agents should navigate the systems layer.
