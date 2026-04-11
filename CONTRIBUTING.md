# Contributing

Thanks for helping build Edgecaster.

## Workflow

- Use feature branches and keep commits scoped.
- Include a short summary of what changed and what you verified.
- Prefer updating the existing local pattern instead of introducing a parallel architecture.

## Environment

- Python 3.10+ recommended.
- Install dependencies from the repo root:
  ```bash
  pip uninstall -y pygame
  pip install -r requirements.txt
  ```

## Running And Testing

- Run the game from the repo root:
  ```bash
  python -m edgecaster.main
  ```
- Run tests from the repo root:
  ```bash
  python -m pytest
  ```
- Good smoke-test path:
  - launch the game
  - create a character
  - place and activate a rune
  - move between zones or stairs without crashes

## Documentation Expectations

- Read `AGENTS.md` before making structural changes.
- Treat `vision_documents/architecture.txt` as the detailed current-state architecture reference.
- Treat `vision_documents/the_yoga.txt` as the north-star document.
- Treat `vision_documents/spring_cleaning.txt` as the canonical active cleanup plan.
- If code changes alter structure, ownership, commands, tests, or architecture boundaries, update the relevant docs in the same pass.
- Update `vision_documents/INDEX.md` when document status or reading order changes.
- Update local `AGENTS.md` and `INDEX.md` files when the area you changed has them.

## Coding Style

- Prefer descriptive variable names over terse abbreviations.
- Prefer relatively verbose comments when they clarify intent, invariants, or tricky behavior.
- Keep code ASCII-only unless there is a clear reason not to.
- Follow existing patterns for scenes, systems, state, content, and rendering.

## Reporting Issues

- Include repro steps, expected behavior, actual behavior, and logs or tracebacks when relevant.
