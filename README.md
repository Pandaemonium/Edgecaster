# Edgecaster

Turn-based roguelike where you weave fractal runes, mutate them with body-based and tool-based geometry, and use them to survive a strange fractal world.

## Quick Start

- Python 3.10+ recommended.
- Install dependencies from the repo root:
  ```bash
  pip uninstall -y pygame
  pip install -r requirements.txt
  ```
- Run the game from the repo root:
  ```bash
  python -m edgecaster.main
  ```
- On Windows, `run_edgecaster.bat` launches the same entrypoint with a crash-pause wrapper.

## Repo Layout

- `edgecaster/` - main game package
- `tests/` - pytest suite
- `vision_documents/` - architecture, vision, planning, and documentation-review material
- `info_docs/` - handoff and supporting notes
- `assets/` - icons, music, and sfx
- top-level prototype/reference files:
  - `fractal_lab.py`
  - `distorted_Julia.py`
  - `edgecaster_mvp.py`

## Useful Commands

- Run the game:
  ```bash
  python -m edgecaster.main
  ```
- Run tests:
  ```bash
  python -m pytest
  ```
- Verified on 2026-04-10:
  - `python -m pytest --collect-only -q` collected 515 tests

## Read First

If you are trying to understand or change the repo, start here:

1. `AGENTS.md`
2. `vision_documents/INDEX.md`
3. `vision_documents/architecture.txt`
4. `vision_documents/the_yoga.txt`
5. `vision_documents/spring_cleaning.txt`

## Architecture Notes

- `ARCHITECTURE.md` is the short summary.
- `vision_documents/architecture.txt` is the detailed current-state reference.
- `vision_documents/the_yoga.txt` is the north-star document.

## Contributing

See `CONTRIBUTING.md` for workflow expectations and doc-maintenance rules.
