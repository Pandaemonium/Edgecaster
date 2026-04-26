# Edgecaster Agent Guide

Purpose: shared repository guidance for coding agents and maintainers working in this repo.

## Scope

This file applies repo-wide unless a deeper `AGENTS.md` overrides or extends it.

## Read This First

For orientation, read these in roughly this order:

1. `vision_documents/INDEX.md`
2. `vision_documents/the_yoga.txt`
3. `vision_documents/aesthetics.md`
4. `vision_documents/lore_bible.md`
5. `vision_documents/factions_and_nations.md`
6. `vision_documents/architecture.txt`
7. `vision_documents/spring_cleaning.txt`
8. `README.md`
9. local `AGENTS.md` and `INDEX.md` files in the area you are changing

## Canonical Docs

- `vision_documents/architecture.txt` is the canonical current-state architecture reference.
- `vision_documents/the_yoga.txt` is canonical north-star guidance for architecture and world-model doctrine.
- `vision_documents/aesthetics.md` is canonical north-star guidance for tone, style, naming, visual language, and writing voice.
- `vision_documents/lore_bible.md` is canonical north-star guidance for world truths, public knowledge, and hidden lore.
- `vision_documents/factions_and_nations.md` is canonical north-star guidance for cultural, national, and faction differentiation.
- `vision_documents/spring_cleaning.txt` is canonical active-planning guidance, specifically used to note down things that should be refactored or cleaned up later.
- `vision_documents/elflore.md` is the canonical active-planning reference for current elven lore and cult-system direction.
- `vision_documents/documentation_review_plan.md` is the canonical plan for the documentation-review effort.
- `ARCHITECTURE.md` should stay short and act as a summary/redirect, not the full architecture source of truth.

## Documentation Maintenance Rules

- Code wins by default when docs drift.
- If a code change moves responsibilities, changes architecture boundaries, or changes the runtime path, update the relevant docs in the same pass.
- If a change affects documentation status or the map of `vision_documents/`, update `vision_documents/INDEX.md`.
- When local `INDEX.md` files exist for the area you changed, update them when structure, ownership, commands, or tests change.
- When local `AGENTS.md` files exist for the area you changed, update them when local invariants or workflow expectations change.
- When a stale doc contains useful material, merge the useful material forward before archiving the stale source.
- When you introduce or discover a compatibility bridge, obsolete subsystem, or dead migration shim, mark it aggressively for deletion in code comments and log it in `vision_documents/spring_cleaning.txt`.
- Prefer deleting dead legacy code as soon as it is safe instead of leaving compatibility scaffolding in place.
- When logging deletion candidates in `vision_documents/spring_cleaning.txt`, note both what should be deleted and the condition or phase when it becomes safe to remove.

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
- Follow `the_yoga.txt`, `aesthetics.md`, and `lore_bible.md` for gameplay, world, and aesthetic direction.
- Prefer extending existing patterns over inventing a parallel architecture.
- Keep the codebase trim: do not normalize legacy bloat. Use explicit markers such as `# [LEGACY_DELETE][ENTITY_CHAKRA][PHASE_N]` for temporary bridges and remove them once safe.

## Operational Notes

- Primary run command: `python -m edgecaster.main`
- Primary test command: `python -m pytest`
- Be careful around large runtime artifacts such as `telemetry.ndjson`; do not treat them as source documentation.

## Debug Logging

Runtime debug output goes to `C:\Games\Edgecaster\debug.log` (created/cleared at game start).

**Writing to the debug log from a system:**

```python
def _dbg(game: Any, msg: str) -> None:
    try:
        dbg = getattr(game, "_debug", None)
        if callable(dbg):
            dbg(msg)
    except Exception:
        pass
```

Call `_dbg(game, "[my_system] some message")` wherever you need trace output.
The `game._debug(msg)` method appends to the log file; it is always safe to call and
silently does nothing if unavailable (e.g. in tests without a real Game instance).

**Do not write debug output to `game.log`** — that is the player-visible message log
displayed in the dungeon UI. Use `game._debug()` for anything intended for developer
eyes only.

**Prefix convention:** prefix messages with `[system_name]` so they are easy to grep,
e.g. `[dismember]`, `[attention]`, `[combat]`.

**Cleanup:** remove debug calls once the feature is stable. They are cheap but add
noise to the log file during normal play.

## Claude Compatibility

- Keep `CLAUDE.md` files aligned with `AGENTS.md` files by using `@AGENTS.md` imports.
- When adding a new shared `AGENTS.md` in a subdirectory, add a sibling `CLAUDE.md` that imports it.
