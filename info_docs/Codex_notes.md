# Codex Notes (Handoff)
Updated: 2026-02-11
Repo: `C:\Games\Edgecaster`

## 1) Current Snapshot
- Branch: `main`
- `debug.log` and `telemetry.ndjson` are active runtime logs.

## 2) Core Architecture (Where Things Live)
- `edgecaster/game.py`
  - Orchestrator/state owner with many delegate wrappers.
  - Still large, but substantial logic has been moved to systems modules.
- `edgecaster/systems/attention.py`
  - Attention/render candidate assembly and related world-proxy details.
- `edgecaster/systems/combat_actions.py`
  - Runtime implementations for major combat abilities:
    - Wind Rush, Energy Kick, Palm Burst, Mirror Strike
    - Aggressive Vines, Choking Vines
    - Throw Flask, Destabilize, Ignite, Regrow, Freeze
- `edgecaster/systems/pattern_runtime.py`
  - Pattern/chakra runtime graph mutation + activation behavior.
- `edgecaster/systems/blade_runtime.py`
  - Blade runtime and attack evaluators.
- `edgecaster/scenes/blade_editor_scene.py`
  - Fractal blade editor UI.
- `edgecaster/systems/chakras.py`
  - Chakra graph/build logic, chakra state integration.
- `vision_documents/architecture.txt`
  - Main architecture reference; keep updated as concerns move.

## 3) Major Mechanics Recently Touched
### Camera / Zone / Attention
- Player-centered camera behavior implemented.
- Neighbor zone/active radius concepts introduced to reduce seam feeling.
- Attention code was heavily consolidated into `systems/attention.py`.

### Ability Pipeline
- Combat-heavy actions migrated out of `game.py` into `systems/combat_actions.py`.
- Damage/target policy work started toward centralized control.
- Cooldown labeling and recharge feedback improvements were discussed/added in prior passes.

### Chakra System
- Chakra UI received multiple polish passes.
- Chakra root selection and preview alignment were heavily debugged.
- Pattern application from chakra state has been unstable across passes (see Known Issues).
- Chakra item interactions were introduced (unlock/bonuses while equipped), but behavior needs verification.

### Blade System
- Blade class and blade editor introduced.
- Initial scene blocking/hanging bug was fixed (editor now appears).
- Blade preview math and generator semantics are still an active quality area.

### Enemy/Content
- Additional enemy slow/deferred attacks were added through deferred action infrastructure.


## 5) Recent User Intent (High Priority Direction)
- Prefer **deletion over fallback bloat**:
  - User explicitly prefers removing legacy/fallback paths and patching crashes as needed.
- Keep systems **elegant, unified, yoga-centric**.
- Continue trimming `game.py` by extracting concerns into system modules.

## 6) Gameplay Direction Requests Already Expressed
- Continue enemy challenge tuning in higher tiers.
- Continue chakra/rune polish.
- More explicit and useful UI explanations/tooltips.
- Monk and Gardener class expansion with class-identity mechanics.
- Build out blade/melee depth, but preserve modular architecture first.

## 7) Practical Next-Session Checklist
1. Run quick smoke test on:
- Chakra screen -> apply chakra pattern -> in-world render parity
- Chakra root set to non-body and verify root/terminus mapping
- Chakra item equip/unequip and state deltas
- Ringmaster spawn guarantees
- Blade editor preview parity vs runtime pattern

2. If chakra mismatch remains:
- Add temporary targeted logs only in chakra->pattern conversion boundary.
- Confirm one canonical source of truth for:
  - active nodes
  - root node id
  - normalized coordinate set
  - final projected vertices/edges

3. Keep architecture docs current:
- Update `vision_documents/architecture.txt` whenever responsibility moves.
- Update `vision_documents/spring_cleaning.txt` with completed slices and next extractions.

## 8) Notes on Logging / Instrumentation
- `debug.log` has been used heavily for diagnosis; keep logs targeted and remove high-volume spam after each fix.
- A separate telemetry log exists (`telemetry.ndjson`); telemetry is intended to remain on.

## 9) Conventions to Preserve
- Prefer system modules over adding more logic to `game.py`.
- Keep action registration in `systems/actions.py`, heavy runtime in dedicated modules.
- Avoid introducing hidden fallback behavior unless absolutely necessary.
- Document tuning knobs near the code and in `architecture.txt`.
- Follow vision in `the_yoga.txt`

## 10) Rune Anchor Siege (New System, V1)
- Core runtime: `edgecaster/systems/rune_anchor_sieges.py`
  - State machine phases: `coherence -> stabilize -> stabilized`
  - Auto-starts on zone entry (through zone-runtime sync hooks).
  - Fractures can backlash and become harder (`required_channels` grows), so failed pressure creates persistent consequences.
  - Completion applies corruption dampening via overmap anchor API and emits `seal_rune` quest progress for compatibility.
- Content: `edgecaster/content/rune_anchor_sieges.yaml`
  - Current encounter id: `starter_anchor`
  - Data-driven knobs for stability model, wave pacing, enemy pool, and dampening reward.
- POI hookup:
  - `failing_rune` now points to `kind: rune_anchor_siege` in `edgecaster/content/pois.yaml`.
  - Attach path in `edgecaster/systems/poi_spawning.py`.
- New actions:
  - `anchor_channel` (`Seal Fracture`)
  - `anchor_stabilize` (`Stabilize Anchor`)
  - `anchor_purge` (`Anchor Purge`)
- Escalation mechanics added in V2 pass:
  - Catastrophe pulse loop (telegraph tiles -> detonation damage -> extra pressure wave).
  - Sapper enemy role with dedicated AI objective (prioritizes fracture sabotage).
  - Siege HUD now includes pulse countdown and active sapper count.

### Cool ideas queued for next passes
1. Add Resonance Crystal phase after coherence repair (socket network, polarity/order rules).
2. Expand sapper ecosystem into named roles (drainers, displacers, edge-shredders) with unique sabotage verbs.
3. Add shrine archetype-specific catastrophe signatures (wind shear, blind pulse, gravity sink).
4. Add bespoke anchor arena generation and hazard lanes per shrine archetype.
5. Add cinematic convergence pulse with regional map-state shifts and follow-up world events.
