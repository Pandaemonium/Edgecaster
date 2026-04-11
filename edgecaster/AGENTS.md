# Edgecaster Package Guide

Purpose: orient agents working inside the main game package.

## Scope

This file applies to `edgecaster/` unless a deeper `AGENTS.md` overrides it.

## Package Role

`edgecaster/` contains the runtime game package:

- entrypoints and orchestration
- scene flow
- rendering
- gameplay systems
- persistent state containers
- content loaders and data
- worldgen helpers

## Core Invariants

- `edgecaster/game.py` is the main orchestrator and state holder, but new mechanics should usually live in `edgecaster/systems/`.
- `edgecaster/render/` is the view layer and should not become a hidden simulation layer.
- Absolute-space reasoning is the long-term source of truth; zone and chunk structures are caches and projections.
- Content should prefer the prototype/spawn pipeline instead of ad hoc construction.
- Keep the repo aligned with `vision_documents/the_yoga.txt` and `vision_documents/architecture.txt`.

## Read Order

For package-level orientation:

1. `../vision_documents/architecture.txt`
2. `../vision_documents/the_yoga.txt`
3. `../vision_documents/spring_cleaning.txt`
4. `INDEX.md`
5. `scenes/manager.py`
6. `scenes/dungeon.py`
7. `game.py`
8. local `AGENTS.md` and `INDEX.md` files in the subdirectory you are editing

## High-Value Areas

- `systems/` - gameplay and simulation logic
- `scenes/` - scene flow and UI state
- `render/` - renderer implementation
- `content/` - content data and loaders
- `state/` - state containers and data models

## Top-Level Support Modules

- `prototypes.py` - unified prototype bucket, inheritance, and body-schema resolution
- `spawn_factory.py` - runtime `Entity`/`Actor` construction from resolved specs
- `mapgen.py` - local zone generation and lab/basic-world helpers
- `mapgen_sites.py` - legendary lair generation plus mixed-staleness site helpers; treat carefully
- `climate.py` - 21-biome climate field and biome classification logic
- `biome.py` - older compatibility-facing biome layer still used by some paths
- `overmap_accel.py` - fast overmap sampling path that must stay aligned with canonical rules

## Maintenance Rules

- Update `INDEX.md` when package-level support modules, ownership notes, or reading order change.
- Update the relevant local `AGENTS.md` and `INDEX.md` files when structure, ownership, or testing guidance changes.
- If package boundaries shift, update `vision_documents/architecture.txt`.
- If a change alters the recommended reading path, update the relevant index file in the same pass.

## Style Guidance

- Prefer descriptive variable names.
- Prefer relatively verbose comments when they clarify intent or invariants.
- Keep architecture documentation close to the current code.
