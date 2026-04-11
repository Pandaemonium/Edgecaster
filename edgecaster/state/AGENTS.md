# State Guide

Purpose: guide work inside `edgecaster/state/`, where dataclasses and persistent-ish state structures live.

## Scope

This file applies to `edgecaster/state/`.

## What Belongs Here

- dataclasses and lightweight state containers
- serialization-friendly structures
- state-layer invariants that other modules consume

## What Does Not Belong Here

- YAML loading
- renderer behavior
- scene-stack logic
- gameplay systems with heavy runtime behavior

## Local Invariants

- `Entity.abs_pos` is canonical; `Entity.pos` is a loaded-zone cache.
- `World` is a local tile cache, not the whole world ontology.
- `POISpec` and `ABSRect` are ABS-space truth for POI definitions.
- Keep this directory side-effect-light and dataclass-centric.
- `LevelState` still living in `game.py` is a migration holdout; do not assume the state layer is fully centralized yet.

## Read First

1. `INDEX.md`
2. `../../vision_documents/architecture.txt`
3. the systems or loaders that consume the state structure you are editing

## Maintenance Rules

- Update `INDEX.md` when a new state structure appears or when a state module changes role.
- Update `../../vision_documents/architecture.txt` when state authority boundaries shift.
