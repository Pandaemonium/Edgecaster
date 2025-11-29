# Edgecaster (prototype)

Turn-based roguelike prototype with fractal runes and an ASCII pygame renderer.

## Project layout
- `v1/Edgecaster/edgecaster/`: game package (game loop, renderer, fractal builders, mapgen, state).
- `v1/Edgecaster/requirements.txt`: dependencies (pygame-ce).
- `fractal_lab.py`: standalone fractal editor from earlier prototyping (not wired into the game package yet).

## Prerequisites
- Python 3.10+ recommended.
- Install dependencies:
  ```bash
  pip uninstall -y pygame  # optional, to avoid conflicts
  pip install -r requirements.txt
  ```

## Running
From `v1/Edgecaster`:
```bash
python -m edgecaster.main
```

## Controls (current slice)
- Movement: arrows / WASD (10 ticks).
- Place terminus: ability 1 (click button or press `1`), then click a tile or move cursor (arrows) and Enter/Space. Range 5; resolves as a slow action (5 ticks).
- Fractal ops: abilities 2–5 (subdivide, koch, branch, extend) — 10 ticks each.
- Activate rune: ability 6 (all vertices) or 7 (seed + neighbors), or `F`. Costs 1 mana per vertex; 10 ticks.
- Reset rune: ability 8.
- Meditate: ability 9 (+10 mana, 100 ticks).
- Stairs: stand on `<` or `>` and press `.`/`>` or `,`/`<`, or click your tile while on stairs.
- ESC: cancel targeting or exit.

## Notes
- Runes are anchored to the tile where you placed the terminus; they stay on that level when you change floors.
- Each level stores its own monsters/runes. HUD shows tick count and level index (upper-right), HP/Mana bars (top-left), and ability bar (bottom).

## TODO ideas
- Range indicator during placement.
- Enemy HP bars and more AI behaviors.
- More fractal generators and pattern selection.
- Save/load and seed controls.

