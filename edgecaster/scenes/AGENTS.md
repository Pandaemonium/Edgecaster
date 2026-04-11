# Scenes Guide

Purpose: guide work inside `edgecaster/scenes/`, where scene flow, UI state, and player-input interpretation live.

## Scope

This file applies to `edgecaster/scenes/`.

## What Belongs Here

- scene stack transitions and window/popup behavior
- scene-owned UI state and view models
- command routing from `GameInput` / `GameCommand`
- widget composition and scene-level callbacks
- scene-specific presentation choices and audio requests

## What Does Not Belong Here

- core gameplay rules better owned by `edgecaster/systems/`
- low-level rendering primitives or surface caches
- direct ownership of prototype/content loading
- hidden scene-manager mutations from widgets

## Local Invariants

- Scenes own UI state, command routing, transitions, and calls into systems.
- Prefer `GameInput` / `GameCommand` for new player-facing input routing.
- `target_cursor_abs` is canonical in dungeon targeting flows; avoid introducing new local-coordinate truth.
- Widgets should emit callbacks or pending scene-owned intents; scenes decide scene-stack changes.
- Prefer `PanelScene` or live-loop patterns for new scenes. Legacy `run()` loops are existing debt, not the preferred direction.
- Keep scene logic consistent with `vision_documents/the_yoga.txt` and `vision_documents/architecture.txt`.

## High-Risk Files

- `dungeon.py`
- `inventory_scene.py`
- `chakra_scene.py`
- `world_map_scene.py`
- `manager.py`
- `base.py`

## Read First

1. `INDEX.md`
2. `../../vision_documents/architecture.txt`
3. `../../vision_documents/the_yoga.txt`
4. the scene file you are editing and the systems it calls into

## Maintenance Rules

- Update `INDEX.md` when a scene changes role, when a new framework/helper file appears, or when the default reading path changes.
- Update `../../vision_documents/architecture.txt` when scene/render/system ownership boundaries move.
- If input routing changes, check whether `game_input.py`, scene docs, and keybind-related docs all still agree.
