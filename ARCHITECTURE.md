# Architecture (short)

## High-level flow
- Scene manager (`edgecaster/scenes`): starts at character creation → dungeon. Death returns to creation.
- Renderer (`edgecaster/render/ascii.py`) owns the pygame loop, input handling, and UI.
- Game state (`edgecaster/game.py`) holds levels, actors, pattern state, params, stats, scheduling, and combat.

## Core systems
- **Patterns & generators** (`edgecaster/patterns`): line-based patterns; generators (subdivide, koch, branch, zigzag, custom poly, extend, jitter). Custom poly scales laterally via a param.
- **Activation** (`patterns/activation.py`, game): activates vertices in radius or neighbor sets; coherence (INT) and strength (RES) gates can fizzle when over limits; mana costs per vertex.
- **Stats & params**: param tiers gated by INT/RES; auto-max for most except custom amplitude (user-chosen). XP/level ups boost HP/MP; coherence/strength limits scale with INT/RES.
- **World/state** (`state/*`, `mapgen.py`): grid levels with FOV; runes anchored per level; per-level actors/events.
- **Scenes** (`scenes/*`): character creation wrapper (uses full-featured creator), dungeon scene constructs `Game` with selected character.
- **Character creation** (`char_creation.py`): classes as presets (generator/illuminator/stats), point-buy, custom fractal drawing (grid-snapped), ability selection; returns `Character`.

## Renderer/input notes
- Pygame loop in `render/ascii.py`; zoom is cursor-centered; map glyphs scale, UI text fixed.
- Ability bar builds from character/unlocked generators; config overlay adjusts params; hover previews show activation damage/fail risk; pulsing placement range; post-activation glow only.
- Targeting/aim handled in renderer; game enqueues actions and advances time.

## Files of interest
- `edgecaster/game.py`: main game state, scheduling, actions, activations, XP/leveling, coherence/strength checks.
- `edgecaster/render/ascii.py`: renderer, UI, input, icons, overlays.
- `edgecaster/char_creation.py`: full character builder (classes, stats, custom fractal).
- `edgecaster/scenes/*`: scene manager + scene wrappers.
- `edgecaster/patterns/builder.py`: generators, custom poly with amplitude param.

## RNG
- Single RNG stream per run (`edgecaster/rng.py`); seeded from config in `SceneManager`.
