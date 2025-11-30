# Edgecaster

Turn-based roguelike where you weave fractal runes and ignite them. ASCII-style renderer (pygame-ce), scene system, and a full character creator (stats + custom fractal).

## Premise & goals (short)
- You are the last Patterncaster; ancient seals failed and demons infiltrate the nations.
- Travel the world, remake seals, and push back demonic influence.
- Fractal-first magic: draw/transform runes, then activate them for varied effects.
- Factions + reputation (Caves of Qud vibe); mix of handcrafted and procgen NPCs and lairs; open-world aspirations.

## Project layout
- `v1/Edgecaster/edgecaster/`: game package (game loop, renderer, patterns, mapgen, scenes).
- `v1/Edgecaster/requirements.txt`: dependencies (pygame-ce).
- `fractal_lab.py`: early standalone fractal lab (reference only).

## Prerequisites
- Python 3.10+ recommended.
- Install deps (from repo root):
  ```bash
  cd v1/Edgecaster
  pip uninstall -y pygame  # optional, to avoid conflicts
  pip install -r requirements.txt
  ```

## Running
From `v1/Edgecaster`:
```bash
python -m edgecaster.main
```

## Current features
- Scene manager (character creation → dungeon; death returns you to creation).
- Character creation with classes (presets for generator/illuminator/stats) and full point-buy + custom fractal drawing.
- Fractal system: place terminus (range 8), apply generators (subdivide/koch/branch/zigzag/custom/extend), activate radius or neighbors, reset, meditate.
- Stats & gating: INT/RES gate param tiers; coherence/strength fizzles when overloading patterns/activations; XP/level ups boost HP/MP.
- Zoomable ASCII renderer; ability bar with icons; hover previews for activation damage/fail%; pulsing placement range.
- Stairs up/down with level indicator; enemies (imps) with spotting log; wait action on numpad 5.

## Controls (current)
- Movement: arrows / WASD / numpad (diagonals). Numpad 5 = wait.
- Zoom: mouse wheel (cursor-centered; map glyphs scale, UI text fixed).
- Place terminus: ability 1 or `1`; click/keys to choose tile; slow 5 ticks; range 8; pulsing ring shown.
- Fractal ops: abilities on the bar (subdivide, koch, branch, zigzag, extend, custom). ~10 ticks.
- Activate: radius or neighbors (button or hotkey). Costs 1 mana per vertex; previews damage/fail on hover; time at click.
- Reset rune: button.
- Meditate: restores mana (100 ticks).
- Stairs: stand on `<` or `>` and press `.`/`>` or `,`/`<`, or click your tile while on stairs.
- ESC: cancel targeting/dialog, or exit.

## Notes
- Runes are anchored to the tile where you placed the terminus; they stay on that level.
- Each level stores its own monsters/runes; HUD shows tick/level, HP/MP/XP/stats, coherence info, ability bar.

## Contributing (short)
- Use feature branches; open PRs against `main`.
- Python 3.10+, prefer keeping pygame-ce pinned from `requirements.txt`.
- Run `python -m edgecaster.main` as a smoke test before PRs.
- Keep the outer `Edgecaster/` repo ignored; work in `v1/Edgecaster`.

See `CONTRIBUTING.md`, `ARCHITECTURE.md`, and `CHANGELOG.md` for more detail.
