# Vision Documents Guide

Purpose: guide maintenance of `vision_documents/` so it stays legible, current, and useful for AI agents.

## Scope

This file applies to `vision_documents/` and its subdirectories.

## Canonical Docs In This Folder

- `architecture.txt` - canonical current-state architecture reference
- `map_generation_strategy.txt` - detailed current-state worldgen reference
- `the_yoga.txt` - canonical north-star document
- `aesthetics.md` - canonical tone and style guide
- `lore_bible.md` - canonical world lore and public-knowledge guide
- `factions_and_nations.md` - canonical culture and nation guide
- `spring_cleaning.txt` - canonical active-plan document
- `elflore.md` - canonical active-plan reference for current elven design
- `documentation_review_plan.md` - canonical documentation-review plan
- `INDEX.md` - the folder map and classification ledger for `vision_documents/`

## Folder Responsibilities

`vision_documents/` should contain:

- canonical vision and architecture references
- active plans still guiding near-term work
- feature-level north-star documents that remain useful
- carefully archived historical or scratch material

It should not become a dumping ground for duplicate handoff notes or abandoned one-off fragments that never get classified.

## Classification Rules

Use these statuses when reviewing docs:

- `north-star`
- `active-plan`
- `historical`
- `scratch`
- `current-state reference` where that is more accurate

If a document is stale but still contains useful material:

1. merge the useful material into a canonical doc or newer plan
2. update links or metadata if needed
3. move the stale source into `archived/`

## Index Maintenance Rules

- Update `INDEX.md` whenever a document in this folder is added, removed, renamed, reclassified, archived, or promoted to canonical status.
- Update `INDEX.md` when the recommended reading order changes.
- Do not mark a document as canonical in `INDEX.md` unless it is actively trusted.
- If classification is unclear after checking the code and canonical docs, leave a note and escalate instead of guessing.

## Architecture Maintenance Rules

- `architecture.txt` should document the current code, not an imagined future architecture.
- If code changes make `architecture.txt` stale, update it in the same pass or leave a clear follow-up note.
- `ARCHITECTURE.md` at repo root should stay a short summary and redirect.

## Metadata Rules

Important docs should gradually adopt metadata fields such as:

- `Purpose`
- `Status`
- `Last verified`
- `Canonical for`
- `Related docs`
- `Related code`
- `Related tests`
- `Supersedes`
- `Superseded by`

## Style Guidance

- Prefer clear headings and direct language over poetic ambiguity.
- Keep big-picture vision distinct from short-term planning.
- Prefer one maintained canonical doc over several overlapping half-canonical notes.
- Preserve useful history, but move it out of the active path.

## Claude Compatibility

- Keep the sibling `CLAUDE.md` file as a thin import shim to this file.
