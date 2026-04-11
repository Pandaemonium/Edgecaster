# Scenes Index

Purpose: map the main scene roles inside `edgecaster/scenes/`.
Status: active-plan
Last verified: 2026-04-10
Canonical for: scenes-folder navigation
Related docs: `edgecaster/scenes/AGENTS.md`, `vision_documents/architecture.txt`
Related code: `edgecaster/scenes/`
Supersedes: none

## Framework

- `base.py` - scene base classes, panel/popup behavior, widget routing
- `manager.py` - scene stack, shared options, audio coordination, keybinding load
- `game_input.py` - pygame input to `GameCommand`

## Main Gameplay

- `dungeon.py` - main live gameplay scene and `DungeonUIState`

## Heavy Overlay / Interaction Scenes

- `inventory_scene.py` - inventory and look flow, body zoom, drag/drop, preview camera state
- `chakra_scene.py` - chakra selection, realign mode, preview, commit/cancel flow
- `world_map_scene.py` - world map rendering, zoom, cached overmap handoff, fast travel
- `merchant_scene.py` - trade UI

## Editors

- `fractal_editor_scene.py`
- `branch_editor_scene.py`
- `blade_editor_scene.py`

## Menus, Popups, And Support

- `main_menu.py`
- `options_scene.py`
- `keybinds_scene.py`
- `dialogue_scene.py`
- `character_creation_scene.py`
- `urgent_message_scene.py`
- `quantity_prompt_scene.py`
- `quest_scene.py`
- `factions_scene.py`
- `gods_scene.py`
- `wish_scene.py`
- `pause_menu_scene.py`
- `saved_games_scene.py`
- `audio_manager.py`
- `spatial_music.py`

## Maintenance Rules

- Update this file when scene ownership changes or when a new major scene becomes part of the common read path.
- Keep this grouped by role, not alphabetically.
