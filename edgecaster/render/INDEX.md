# Render Index

Purpose: map the main renderer files in `edgecaster/render/`.
Status: active-plan
Last verified: 2026-04-10
Canonical for: render-folder navigation
Related docs: `edgecaster/render/AGENTS.md`, `vision_documents/architecture.txt`
Related code: `edgecaster/render/`
Supersedes: none

## Main Files

- `ascii.py` - primary dungeon/world renderer and remaining HUD/camera bridge surface
- `overmap_lod.py` - snapped terrain LoD grid helper
- `terrain_tiles.py` - procedural terrain tile surface bank

## Maintenance Rules

- Update this file when the renderer is split further or when a helper becomes part of the normal read path.
