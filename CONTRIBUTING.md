# Contributing

Thanks for helping build Edgecaster! A few lightweight guidelines to keep us aligned.

## Workflow
- Use feature branches; open PRs against `main`.
- Keep commits scoped and readable; include a short summary of changes and any testing performed.
- Avoid committing the outer `Edgecaster/` repo; work inside `v1/Edgecaster` (outer repo is ignored in `.gitignore`).

## Environment
- Python 3.10+.
- Install deps from `v1/Edgecaster/requirements.txt` (pygame-ce).
- Optional: uninstall `pygame` first to avoid conflicts: `pip uninstall -y pygame`.

## Running / smoke test
- From `v1/Edgecaster`: `python -m edgecaster.main`.
- Basic sanity: launch, create a character, place a rune, activate it, go up/down stairs without crashes.

## Coding conventions
- Keep code ASCII-only unless needed.
- Favor small, clear functions; add brief comments for non-obvious logic (e.g., coherence/strength fizzle).
- Follow existing patterns for scenes (`edgecaster/scenes`), game state (`edgecaster/game.py`), and renderer (`edgecaster/render`).

## Branch/PR hygiene
- Rebase or merge from `main` regularly to minimize conflicts.
- If adding assets/data, keep paths organized under the relevant package.
- Update docs if behavior/controls change: `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`.

## Reporting issues
- Include repro steps, expected vs actual, and logs/tracebacks where relevant.
