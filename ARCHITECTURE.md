# Architecture

This file is the short summary.

For the detailed current-state map, read:

- `vision_documents/architecture.txt`
- `vision_documents/map_generation_strategy.txt`
- `vision_documents/INDEX.md`

## High-Level Runtime Path

- `run_edgecaster.bat` -> `py -m edgecaster.main`
- `edgecaster/main.py` -> `edgecaster/engine.py`
- `edgecaster/engine.py` -> `edgecaster/scenes/manager.py`
- live gameplay centers on `edgecaster/scenes/dungeon.py` and `edgecaster/game.py`

## Main Boundaries

- `edgecaster/scenes/` owns scene flow and UI state
- `edgecaster/render/` is the view layer
- `edgecaster/systems/` owns gameplay and simulation logic
- `edgecaster/state/` owns dataclasses and persistent-ish state containers
- `edgecaster/content/` owns prototype and content data

## Canonical Docs

- `vision_documents/architecture.txt` - current-state architecture reference
- `vision_documents/the_yoga.txt` - north-star architectural doctrine
- `vision_documents/spring_cleaning.txt` - active cleanup and refactor plan

## Documentation Rule

When code and docs disagree, the code wins by default and `vision_documents/architecture.txt` should be updated to match the current system.
