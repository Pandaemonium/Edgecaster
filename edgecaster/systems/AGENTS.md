# Systems Guide

Purpose: guide work inside `edgecaster/systems/`, where most gameplay and simulation logic lives.

## Scope

This file applies to `edgecaster/systems/`.

## What Belongs Here

- gameplay rules
- action execution and cooldown logic
- combat and damage policy
- spawning, POI realization, and world-entity staging
- simulation helpers for chakras, gods, rune systems, economy, and progression
- query and coordination helpers that keep `game.py` thinner

## What Does Not Belong Here

- scene flow and widget state
- low-level renderer behavior
- raw dataclass definitions better suited to `edgecaster/state/`
- one-off content definitions that belong in `edgecaster/content/`

## Local Invariants

- Prefer adding or extending a system module over growing `game.py`.
- Keep `game.py` as the orchestrator, not the home for new subsystems.
- Shared policy belongs in reusable modules such as `damage_policy.py`, not duplicated per action.
- If a mechanic crosses multiple systems, document the ownership split in `INDEX.md`.
- Add or update focused tests when changing shared behavior.

## Read This First

1. `INDEX.md`
2. `../../vision_documents/architecture.txt`
3. `../../vision_documents/spring_cleaning.txt`
4. the specific modules and tests touched by your change

## Maintenance Rules

- Update `INDEX.md` when modules are added, renamed, or materially change ownership.
- Update `INDEX.md` when a new cluster emerges or an old one stops being a useful grouping.
- Update `../../vision_documents/architecture.txt` when major responsibility boundaries move.
- If a change needs a new system and a matching test area, add both in the same pass when practical.

## Style Guidance

- Prefer descriptive names over compressed shorthand.
- Use comments to explain why a rule exists, not just what the code is doing.
- Keep behavior centralized when several abilities or systems share the same policy.
