# UI Guide

Purpose: guide work inside `edgecaster/ui/`, which holds reusable widget and HUD components.

## Scope

This file applies to `edgecaster/ui/`.

## What Belongs Here

- reusable widget layout/draw/hit-test components
- HUD components shared across scenes
- ability-bar view/model/hit-test glue

## What Does Not Belong Here

- direct `SceneManager` manipulation
- scene-stack policy
- gameplay rules
- ad hoc scene-specific hacks that should stay local to one scene

## Local Invariants

- Widgets are reusable view/hit-test components.
- Prefer callbacks or scene-owned pending intents over direct scene-stack mutation.
- Keep layout and hitboxes stable across hover, paging, labels, and dynamic content.
- Preserve the `AbilityBarState` / `AbilityBarRenderer` / `AbilityBarWidget` split.

## Read First

1. `INDEX.md`
2. `../../vision_documents/architecture.txt`
3. the scene(s) that use the widget you are changing

## Maintenance Rules

- Update `INDEX.md` when new reusable widget families appear or when ownership shifts.
- If UI changes alter scene responsibilities, update the relevant scene docs too.
