# Passdown: Pattern-Active Lag Investigation

**Date:** 2026-04-18  
**Status:** Root cause identified, fix NOT yet implemented  
**Author:** Claude (Sonnet 4.6)

---

## Symptom

~500ms visual lag after each move, but **only when a pattern is active**. If the pattern is cleared, lag disappears. Pressing move multiple times shows the lag is roughly constant per turn (not multiplicative), pointing to a per-tick overhead rather than an O(input) problem.

---

## What Has Already Been Fixed (this session)

### Fix 1: `effective_active_nodes` / `effective_unlocked_nodes` (implemented)

These were calling `_rebuild_chakra_state_from_component` on the hot per-tick path — allocating a full `ChakraState` just to extract a node set. Fixed to read directly from `_component_node_sets` + `_baseline_chakra_root`. File: `edgecaster/systems/chakra_items.py`.

### Fix 2: `effective_chakra_state` triple-rebuild (implemented)

`effective_chakra_state` was calling `_rebuild_chakra_state_from_component` three times — once directly, then once each via `effective_unlocked_nodes` and `effective_active_nodes`. Fixed to one call, applying item effects inline. Same file.

User confirmed: "It seems better, but still a bit laggy." — these fixes helped but did not resolve the root cause.

---

## Root Cause (NOT YET FIXED)

### The dirty/clean/reduce cycle

When a pattern is active, `charging=True` in `chakra_charge_tick` (`scheduling.py:243`).

Each tick the following chain fires:

1. **`tick_actor_chakra_charge`** (`chakra_items.py:506`) — updates charge values for every active node + decays inactive nodes with charge > 0. Collects all written node IDs in `dirtied` (every tick, every active node is in `dirtied`).

2. **`_mark_actor_chakra_dirty`** (`chakra_items.py:183`) — called for each nid in `dirtied`. Per node: looks up `graph.get_node(f"{actor_id}:body:{nid}")`, then calls `graph.mark_dirty_up(body_entity_id)`, which walks up the entity graph parent chain marking ancestors dirty.

3. **`chakra_reducer_tick`** (`scheduling.py:183`) — sees actor is dirty (from step 2), so it cannot skip. Calls `reduce_component(comp, rules)` (topological sort + channel propagation), then calls `graph.mark_subtree_clean(actor_id)`.

4. **`mark_subtree_clean`** (`state/entity_graph.py`) — BFS over the actor's entire entity subtree. The player now has a full body subtree (~20+ body-node entities added by spawn-time body expansion in `register_actor`). This is O(body_subtree_size) per tick.

5. Next tick: step 1 immediately dirty-marks the graph again → repeat.

**Every tick while charging: O(active_nodes) dirty-up traversals + O(body_subtree_size) clean BFS + reducer work.**

The spawn-time body expansion added ~20+ child entities to the player's entity graph subtree. Before that expansion, `mark_subtree_clean` was cheap. Now it's expensive and runs every single tick while a pattern is active.

---

## Recommended Fix

The key insight: **charge values change every tick while charging, so the dirty/clean mechanism adds pure overhead** — the reducer will always need to re-run anyway, so there is no value in marking clean only to re-dirty immediately.

### Option A (recommended): Skip dirty-marking for routine charge updates

In `tick_actor_chakra_charge` (`chakra_items.py`, lines 577–579), **do not call `_mark_actor_chakra_dirty`** for routine charge gain/decay. Remove this block:

```python
if game is not None:
    for nid in sorted(dirtied):
        _mark_actor_chakra_dirty(game, actor, nid)
```

Then in `chakra_reducer_tick` (`scheduling.py:220`), bypass the clean-check when the actor is in a charging state:

```python
charging = bool(getattr(level, "pattern", None) and level.pattern.vertices)
# ...
for actor in list(level.actors.values()):
    # ...
    is_charging = charging and getattr(actor, "id", None) == getattr(game, "player_id", None)
    if not is_charging and _actor_chakra_snapshot_is_clean(game, actor):
        continue
    effective = chakra_reducer_system.reduce_component(comp, rules)
    actor._chakra_effective_channels = effective
    if graph is not None and actor_id:
        graph.mark_subtree_clean(actor_id)
```

This way:
- The reducer still runs every tick while charging (same correctness as now).
- The O(active_nodes) dirty-up traversals per tick are eliminated.
- The O(body_subtree_size) `mark_subtree_clean` BFS is still called once per tick, but...

### Option B (better): Also skip `mark_subtree_clean` when charging

If the graph is never marked dirty by charge ticks, there is no need to mark it clean either. When `is_charging`, skip `mark_subtree_clean` entirely:

```python
if not is_charging:
    graph.mark_subtree_clean(actor_id)
```

The graph will remain dirty (or clean from the last non-charging tick) until the pattern clears. At that point the regular dirty/clean path handles structural changes. This eliminates both traversals from the per-tick hot path while charging.

**Verify**: confirm that nothing outside `chakra_reducer_tick` relies on the graph being marked clean as a side effect of charge ticks. A grep for `mark_subtree_clean` and `dirty` consumers in `entity_graph.py` should confirm this is safe.

---

## Profiling Probes (added this session — can be removed after fix verified)

Added `perf_profiler.measure` wraps to:
- All subsystems in `advance_time` in `scheduling.py` (e.g. `tick.chakra_charge`, `tick.chakra_reducer`)
- `render.sync_attention` in `attention.py` (render-path sync call)
- `action.run_action.*` in `game.py` (per-action timing)

These write to `debug.log` every 2 seconds via the existing `perf_profiler` infrastructure. Remove or guard behind a debug flag once performance is confirmed acceptable.

---

## Files to Touch

| File | Change |
|------|--------|
| `edgecaster/systems/chakra_items.py` | Remove `_mark_actor_chakra_dirty` calls from `tick_actor_chakra_charge` (lines 577–579) |
| `edgecaster/systems/scheduling.py` | In `chakra_reducer_tick`: bypass clean-check and optionally skip `mark_subtree_clean` when `is_charging` |

---

## Secondary Check (lower priority)

`sync_attention_instantiation` is called from the render path (`attention.py:renderables_in_abs_rect`) every time the camera signature changes, which is every player move. The early-exit path appears fast but worth checking in profiler output if lag persists after the charge fix.

---

## Tests

Run `python -m pytest` after the fix. No new tests are strictly required, but a focused test that verifies `chakra_reducer_tick` re-runs correctly while charging (without requiring the entity graph to be dirty first) would be good insurance.
