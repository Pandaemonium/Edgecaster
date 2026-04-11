# State Index

Purpose: map the main state structures in `edgecaster/state/`.
Status: active-plan
Last verified: 2026-04-10
Canonical for: state-folder navigation
Related docs: `edgecaster/state/AGENTS.md`, `vision_documents/architecture.txt`
Related code: `edgecaster/state/`
Supersedes: none

## Core Runtime Structures

- `entities.py` - `Entity` with canonical `abs_pos` and footprint fields
- `actors.py` - `Actor`, `Stats`, and actor-specialized fields
- `world.py` - local tile-grid cache for loaded zones
- `patterns.py` - `Pattern`, `Vertex`, `Edge`, and geometry provenance

## POI, Site, And Quest State

- `pois.py` - ABS-space POI definitions and content-state tracking
- `sites.py` - site placement products and site-type config dataclasses
- `quests.py` - quest state dataclasses

## Chakra And Persistence

- `chakra_component.py` - typed chakra component graph structures
- `saves.py` - save scaffolding
- `factions.py` - small faction-state helpers

## Maintenance Rules

- Update this file when a new state module becomes part of the normal read path or when a module changes role.
