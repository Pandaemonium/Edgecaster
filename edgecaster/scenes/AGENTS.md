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

## Widget Routing Contract

Widgets must not push or pop scenes directly.  The correct pattern is:

1. Widget fires a callback or sets a "pending intent" field on the scene.
2. The scene's update/event loop reads pending intents and calls `manager.push(...)` or `manager.set_scene(...)`.

This keeps `SceneManager` mutations in one place per scene and avoids re-entrant stack changes during event dispatch.  See `inventory_scene.py` for the canonical example of pending-action flushing.

## New Scene Shape vs. Legacy `run()` Loop

Two patterns exist:

**Preferred** — live-loop scenes (`uses_live_loop = True`):
- Override `handle_event(event, manager)`, `update(dt_ms, manager)`, `render(renderer, manager)`.
- The `SceneManager` drives the loop; the scene never owns a pygame event loop.
- Most newer scenes (menus, dialogues, chakra, merchant, inventory, dungeon) use this pattern.

**Legacy** — `run()` loop scenes:
- The scene owns its own `while True` pygame event loop inside `run()`.
- Do not copy this pattern for new scenes.
- Current legacy `run()` scenes (as of 2026-04-10): `world_map_scene.py`, `fractal_editor_scene.py`, `branch_editor_scene.py`, `blade_editor_scene.py`.

## Inventory Scene — Dual-Zoom Invariants

`inventory_scene.py` has two zoom systems that must not regress independently:

**Diagrammatic zoom** (scene open/close):
- The entire inventory UI eases in from the source glyph's world position.
- Entity preview glyph must render at full opacity for the full transition duration.
- At the end of the transition the settled framing must match exactly — no position or scale discontinuity.
- "Fly-in from (0, 0)" means the source glyph was initialized from zone-local rather than ABS coordinates (yoga violation).

**Deep body zoom** (double-click body node dive):
- The preview glyph and the body-skeleton node overlay must remain pixel-aligned at every zoom depth.
- Animated by `_body_zoom_anim` state; do not update node projection independently during an active animation.
- `BodyViewState` is the single authoritative bundle of camera state + projected node positions.
- `PreviewCameraCache` caches the LoD0 framing so diagrammatic zoom lands pixel-identically on the settled view.

## `cache_items_scene.py` Role

`CacheItemsScene` is a lightweight `PopupMenuScene` for picking up items from the ground, a chest, or another container.  It is distinct from the full inventory scene: no body view, no equip slots, no drag.  Entry point: `dungeon.py` pushes it when the player steps onto or examines a tile with multiple items.

## Keybinding Rule

Do not hardwire keys directly in scenes.  All player-facing actions must go through `game_input.py DEFAULT_BINDINGS` so they appear in the Controls menu.  See `architecture.txt` section 2 for the full keybinding contract.

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
