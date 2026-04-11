# Render Guide

Purpose: guide work inside `edgecaster/render/`, which implements projection, drawing, and visual caches.

## Scope

This file applies to `edgecaster/render/`.

## What Belongs Here

- world-to-screen projection
- surface generation and drawing
- visual caches
- terrain and overmap rendering helpers

## What Does Not Belong Here

- gameplay-rule decisions
- scene-stack mutation
- entity or zone realization
- ownership of player input policy

## Local Invariants

- Keep the “TV unplug” rule in mind: if the renderer disappears, game rules should still make sense.
- Renderer code may project, draw, and cache; it should not mutate gameplay truth.
- Prefer canonical camera and ABS-transform helpers over ad hoc coordinate math.
- Keep overmap fast paths and fallbacks behaviorally aligned.
- Cache keys must include all visual inputs that affect output.

## High-Risk Files

- `ascii.py` - very large and still carrying transitional UI/camera bridge state
- `overmap_lod.py` - snapped overmap LOD helpers whose assumptions affect zoom behavior

## Read First

1. `INDEX.md`
2. `../../vision_documents/architecture.txt`
3. `../../vision_documents/the_yoga.txt`
4. the calling scene and any helper module you touch

## Maintenance Rules

- Update `INDEX.md` when renderer responsibilities shift or a helper becomes important enough to be part of the default reading path.
- Update `../../vision_documents/architecture.txt` when renderer boundaries materially change.
