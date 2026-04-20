# Systems Index

Purpose: map the main module clusters in `edgecaster/systems/` so agents can find the right ownership area quickly.
Status: active-plan
Last verified: 2026-04-20 (updated with trade/query-surface notes)
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

- `chakras.py` *(legacy body-schema math still lives here, but `build_chakra_generator_seed_for_actor(...)` is now the preferred actor-oriented entrypoint for runtime/scene generator seed reads)*
- `chakra_effects.py`
- `chakra_items.py` *(component-first chakra read/write helpers; `effective_chakra_view(...)` / `effective_chakra_projection(...)` are the preferred runtime query surfaces while `ChakraState` remains compatibility vocabulary)*
- `chakra_content.py`
- `gods.py`
- `god_abilities.py`
- `seal_trials.py`
- `rune_anchor_sieges.py`
- `rune_audio.py`

### World, Entities, And Observation

- `coords.py`
- `entity_ops.py`
- `entity_identity.py` *(canonical `stable_int_hash` FNV-1a; shared by aggregate_resolution, poi_worldgen, site_placement)*
- `entity_body.py`
- `body_view_queries.py` *(shared read-only body/entity view queries for Chakra Scene, Inventory Scene, and other UI callers that should not own schema/graph fallback ladders)*
- `entity_graph_ops.py`
- `entity_snapshots.py`
- `entity_lifecycle.py`
- `entity_geometry.py`
- `attention.py`
- `aggregate_resolution.py`
- `world_entity_index.py`
- `zones.py`
- `active_zones.py` *(extracted from game.py; active zone coordinate queries, prewarm, move_actor_to_abs)*
- `session.py` *(extracted from game.py; current_level / ensure_player_level_binding / get_player / is_player_alive)*
- `render_query.py`

### Spawning, Sites, And Worldgen Runtime

- `spawning.py`
- `ambient_spawns.py`
- `poi_registry.py`
- `poi_worldgen.py` *(unified POI realization entrypoint; `poi_spawning.py` deleted 2026-04-13; tagged [LEGACY_DELETE] pending full resolver/entity-graph pipeline convergence)*
- `site_placement.py`
- `sites.py` *(legacy — no longer the main source of truth for prototype-driven site placement; prefer `site_placement.py` + `content/site_types.yaml`)*
- `overmap.py`
- `legendaries.py`
- `difficulty.py`

### Inventory, Equipment, Economy, And Social Systems

- `inventory.py` *(graph-first inventory queries, recursive inventory-tree walkers, and shared add/remove helpers; list caches remain a compatibility bridge while callers migrate off direct `game.inventories` reads)*
- `equipment.py`
- `equip_rules.py`
- `item_grants.py`
- `trade.py` *(merchant/player inventory reads now go through the shared inventory query surface; direct list mutation is compatibility-only during the remaining cache bridge period)*
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
