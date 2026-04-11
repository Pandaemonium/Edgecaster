# Edgecaster Agent Guide

Purpose: shared repository guidance for coding agents and maintainers working in this repo.

## Scope

This file applies repo-wide unless a deeper `AGENTS.md` overrides or extends it.

## Read This First

For orientation, read these in roughly this order:

1. `vision_documents/INDEX.md`
2. `vision_documents/architecture.txt`
3. `vision_documents/the_yoga.txt`
4. `vision_documents/spring_cleaning.txt`
5. `README.md`
6. local `AGENTS.md` and `INDEX.md` files in the area you are changing

## Canonical Docs

- `vision_documents/architecture.txt` is the canonical current-state architecture reference.
- `vision_documents/the_yoga.txt` is canonical north-star guidance.
- `vision_documents/spring_cleaning.txt` is canonical active-planning guidance.
- `vision_documents/documentation_review_plan.md` is the canonical plan for the documentation-review effort.
- `ARCHITECTURE.md` should stay short and act as a summary/redirect, not the full architecture source of truth.

## Documentation Maintenance Rules

- Code wins by default when docs drift.
- If a code change moves responsibilities, changes architecture boundaries, or changes the runtime path, update the relevant docs in the same pass.
- If a change affects documentation status or the map of `vision_documents/`, update `vision_documents/INDEX.md`.
- When local `INDEX.md` files exist for the area you changed, update them when structure, ownership, commands, or tests change.
- When local `AGENTS.md` files exist for the area you changed, update them when local invariants or workflow expectations change.
- When a stale doc contains useful material, merge the useful material forward before archiving the stale source.

## Documentation Taxonomy

Use these labels consistently in planning and vision docs:

- `north-star`
- `active-plan`
- `historical`
- `scratch`
- `current-state reference` for docs like `architecture.txt`

Outdated planning material belongs under `vision_documents/archived/`.

## Repo Map

- `edgecaster/` - main game package
- `tests/` - pytest coverage for systems and refactor invariants
- `vision_documents/` - architecture, vision, planning, and doc-review material
- `info_docs/` - handoff and supporting notes
- `assets/` - icons, music, and sfx
- top-level prototype/reference files:
  - `fractal_lab.py`
  - `distorted_Julia.py`
  - `edgecaster_mvp.py`

## High-Level Engineering Guidance

- Prefer descriptive variable names over terse abbreviations.
- Prefer relatively verbose comments when they help explain intent, invariants, or tricky behavior.
- Keep documentation close to the current code rather than preserving stale speculation.
- Follow `the_yoga.txt` for gameplay and aesthetic direction.
- Prefer extending existing patterns over inventing a parallel architecture.

## Operational Notes

- Primary run command: `python -m edgecaster.main`
- Primary test command: `python -m pytest`
- Be careful around large runtime artifacts such as `telemetry.ndjson`; do not treat them as source documentation.

## Claude Compatibility

- Keep `CLAUDE.md` files aligned with `AGENTS.md` files by using `@AGENTS.md` imports.
- When adding a new shared `AGENTS.md` in a subdirectory, add a sibling `CLAUDE.md` that imports it.
