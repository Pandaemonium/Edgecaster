# Tests Index

Purpose: map the major test clusters in `tests/` so agents can find the right regression surface quickly.
Status: active-plan
Last verified: 2026-04-20
Canonical for: tests-folder navigation
Related docs: `tests/AGENTS.md`, `vision_documents/architecture.txt`, `vision_documents/spring_cleaning.txt`
Related code: `tests/`
Supersedes: none

## Core Clusters

### Action And Turn Flow

- `test_action_runner.py`
- `test_actions_lineage.py`
- `test_scheduling.py`
- `test_params.py`

### Patterns, Chakras, And Rune Runtime

- `test_pattern_ops.py`
- `test_pattern_runtime.py`
- `test_character_creation_scene.py`
- `test_chakra_scene.py`
- `test_chakra_component.py`
- `test_chakra_items_state_bridge.py`
- `test_chakra_unlock_queries.py`
- `test_entity_geometry.py`
- `test_gods.py`
- `test_lorenz_aura.py`
- `test_rune_anchor_sieges.py`

### Combat, Inventory, And Economy

- `test_events.py`
- `test_combat.py`
- `test_inventory.py`
- `test_inventory_scene.py`
- `test_merchant_scene.py`
- `test_trade.py`

### Spawning, POIs, And World Structure

- `test_spawning.py`
- `test_poi_spawning_cutover.py`
- `test_starttsgard_runtime.py`
- `test_legendaries.py`
- `test_world_hierarchy_content.py`
- `test_aggregate_resolution.py`

### Entities, Coordinates, And Observation

- `test_attention_suppression.py`
- `test_attention_store.py`
- `test_entity_body_expansion.py`
- `test_entity_graph_store.py`
- `test_entity_graph_ops.py`
- `test_entity_lifecycle.py`
- `test_world_entity_index.py`
- `test_yoga_coordinates.py`
- `test_zones.py`
- `test_footprints.py`
- `test_ai_footprints.py`

### Broad Regression Coverage

- `test_game_refactor.py`
- `test_overmap.py`

## Maintenance Rules

- Update this file when a new test file becomes the main home for a subsystem.
- Update this file when ownership shifts enough that the current grouping becomes misleading.
- Keep the grouping practical: this is a navigation map, not a complete inventory of every assertion.
