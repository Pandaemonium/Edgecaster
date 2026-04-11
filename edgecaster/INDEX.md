# Edgecaster Package Index

Purpose: map the main package-level areas and support modules inside `edgecaster/`.
Status: active-plan
Last verified: 2026-04-10
Canonical for: package-level navigation
Related docs: `edgecaster/AGENTS.md`, `vision_documents/architecture.txt`, `vision_documents/the_yoga.txt`
Related code: `edgecaster/`
Supersedes: none

## Read First

1. `AGENTS.md`
2. `../vision_documents/architecture.txt`
3. this file
4. the local `AGENTS.md` and `INDEX.md` in the directory you are editing

## Main Areas

- `systems/` - gameplay and simulation logic
- `scenes/` - scene stack, UI state, and command routing
- `render/` - renderer implementation and overmap terrain helpers
- `content/` - YAML/Python content definitions and loaders
- `state/` - dataclasses and persistent-ish state structures
- `ui/` - reusable widget primitives and HUD components
- `patterns/` - geometry and generator helpers (see local `AGENTS.md` for vertex-ordering and baseline contracts)
- `devtools/` - editor/tool support
- `enemies/` - enemy factory bridge

## Top-Level Support Modules

- `game.py` - main orchestrator and state holder
- `main.py` - process entrypoint
- `engine.py` - high-level loop ownership
- `prototypes.py` - unified prototype bucket and resolution logic
- `spawn_factory.py` - runtime object construction
- `mapgen.py` - local terrain generation and lab/basic generation
- `mapgen_sites.py` - legendary lairs plus mixed/legacy site helpers
- `climate.py` - climate fields and 21-biome classification
- `biome.py` - older compatibility biome layer
- `overmap_accel.py` - fast overmap sampling path
- `camera.py` - canonical camera state and transforms

## Risk Notes

- `game.py`, `render/ascii.py`, `scenes/dungeon.py`, and `scenes/inventory_scene.py` are high-blast-radius files.
- `mapgen_sites.py` contains useful lair generation plus stale or transitional site code; read carefully before editing.
- Fast worldgen/render paths in `overmap_accel.py` must stay aligned with scalar/canonical logic.

## Maintenance Rules

- Update this file when top-level package ownership shifts or support modules materially change role.
- Update this file when a directory gets a new local `AGENTS.md` or `INDEX.md` that should be part of the default reading path.
