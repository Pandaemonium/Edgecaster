# Patterns Guide

Purpose: guide work inside `edgecaster/patterns/`, where fractal geometry generation, motion, and coloring live.

## Scope

This file applies to `edgecaster/patterns/`.

## What Belongs Here

- fractal generator implementations that produce `Segment` lists
- pattern construction helpers (`builder.py`, `library.py`)
- gameplay projection helpers (`activation.py`)
- runtime motion state (`motion.py`)
- edge/vertex color assignment helpers (`colors.py`)

## What Does Not Belong Here

- gameplay rules (damage math, ability costs, cooldowns)
- scene-stack or UI concerns
- entity spawning or zone management
- the `Pattern` / `Vertex` / `Edge` / `Segment` dataclasses themselves (those live in `edgecaster/state/patterns.py`)

## Data Model (from `edgecaster/state/patterns.py`)

Two representations exist and serve different purposes:

- **`Segment` (float geometry)**: a directed line segment `(a: Vec2, b: Vec2)` used during fractal generation. Generators consume and produce `Segment` lists. Carries optional provenance: `src_kind`, `src_node_a`, `src_node_b`.
- **`Pattern` (vertex-index graph)**: the authoritative runtime representation. Stores `vertices: List[Vertex]` and `edges: List[Edge]` where `Edge.a` and `Edge.b` are integer indices into `vertices`. This is what lives on `level.pattern`.

Conversion between the two:
- `Pattern.to_segments()` — expand edges into `Segment` list for processing by generators.
- `Pattern.from_segments()` — collapse a `Segment` list back into a `Pattern`.
- Always round-trip through these helpers; do not reconstruct the conversion math inline.

`Vertex` can carry a `tags` dict (e.g. `{"chakra_node": "leg.foot.toe_1"}`). Most generators ignore it; chakra-aware code and the coloring helpers can consume it.

## Generators (`builder.py`)

All generators extend `GeneratorBase` and implement `apply_segments(segments, max_segments)`.

Available generators:
- `SubdivideGenerator` — split each segment into N equal parts.
- `KochGenerator` — Koch bump (three-part replacement with a perpendicular peak).
- `BranchGenerator` — split each segment at midpoint and add two angled branches.
- `ZigzagGenerator` — alternating lateral offsets.
- `JitterGenerator` — deterministic per-point positional noise.
- `ExtendGenerator` — copy and translate by the head-to-tail vector.
- `CustomPolyGenerator` — normalize a user polyline onto each segment's baseline.
- `CustomGraphGenerator` — normalize a vertex/edge graph onto each segment's baseline.

Use `apply_chain(initial, [(gen, repeats), ...])` to sequence generators.

### CustomGraphGenerator — vertex ordering contract (critical)

`CustomGraphGenerator` maps a graph onto a line-segment baseline using:
- `vertices[0]` as the **root** (maps onto `segment.a`).
- `vertices[-1]` as the **terminus** (maps onto `segment.b`).
- All other vertices are normalized relative to the `vertices[0]`→`vertices[-1]` vector.

**Getting the ordering wrong causes size explosion.** When the root and terminus are far apart relative to the pattern's current scale, the mapping amplifies dimensions. The chakra pipeline avoids this by explicitly selecting the vertex closest to the pattern origin as index 0 before calling the generator (see `game.py act_chakra` and `systems/chakras.py build_chakra_generator_seed_for_actor`).

When `vertex_labels` is provided, generated `Segment` objects have `src_kind="chakra"` and `src_node_a/src_node_b` set from the labels. This provenance is used by coloring helpers and the acidic pattern tick.

### Segment count guard

All generators respect `max_segments`. This is the primary guard against runaway pattern growth. The coherence drain in `systems/scheduling.py coherence_tick` fires proportionally to vertex count beyond `INT * 4`, so unbounded generators drain coherence rapidly and eventually unravel the pattern.

## Edge Coloring (`colors.py`)

Color helpers write to `pattern.edge_colors`, a `dict` keyed by `(min(a, b), max(a, b))` normalized tuples. Renderers read this dict for drawing; the acidic pattern tick in `scheduling.py` reads it to determine green intensity for corrosive damage.

Available helpers:
- `apply_rainbow_edges` — ROYGBIV depth coloring outward from the anchor/root vertex.
- `apply_depth_green_edges` — white→green gradient by BFS depth (used by verdant/acidic effects).
- `apply_winter_hue` — white→deep-blue based on local vertex density.

Rule: always use the normalized `(min(a, b), max(a, b))` key when reading `edge_colors`, because some writers and readers use both orderings. The helpers in `colors.py` normalize consistently; ad hoc reads should do the same.

## Gameplay Connections

- `systems/chakras.py` — `build_chakra_generator_seed_for_actor()` builds a `ChakraGeneratorSeed` from active chakra positions using the realized entity tree (entity path) or body schema (fallback).
- `systems/pattern_ops.py` — fractal op placement and activation.
- `systems/pattern_runtime.py` — runtime fractal op mutation (Activate R/N, polygon/star/chakra actions).
- `game.py act_chakra` — applies `CustomGraphGenerator` with explicit root/terminus alignment.
- `systems/scheduling.py coherence_tick` — reads `len(level.pattern.vertices)` for drain calculation.
- `systems/scheduling.py acidic_pattern_tick` — reads `pattern.edge_colors` for green-edge damage.

## Activation and Motion

- `activation.py` — `project_vertices(pattern, origin)` translates pattern vertices into world space for gameplay use (ABS or local, depending on `origin`). Pure helper; no side effects.
- `motion.py` — `start_motion()` + `step_motion()` apply per-tick rotation/translation. **Motion mutates `Pattern.vertices` in place.** Code that caches vertex positions (activation points, coherence checks) will see the change on the next tick.

## Read First

1. `../../vision_documents/architecture.txt` (section 11: Chakra System)
2. `../../vision_documents/the_yoga.txt`
3. `../../edgecaster/state/patterns.py`
4. `builder.py` — then the specific module you are editing

## Maintenance Rules

- Update this file when a new generator is added, a module changes role, or a key invariant (e.g. the baseline contract) is revised.
- Update `../../vision_documents/architecture.txt` if the patterns–chakra–gameplay pipeline changes structurally.
- If a new coloring helper is added, document the `edge_colors` key convention here so it stays consistent.
