Title: Entity + Chakra Unification Overview
Purpose: explain the entity/chakra unification strategy to human collaborators in plain language, including what is changing in the game and what the refactor is intended to unlock.
Status: active-plan
Last verified: 2026-04-12
Canonical for: human-facing overview of the entity/chakra migration
Related docs: `vision_documents/entity_chakra_unification_vision.txt`, `vision_documents/entity_chakra_unification_master_plan.txt`, `vision_documents/architecture.txt`, `vision_documents/the_yoga.txt`
Related code: `edgecaster/state/`, `edgecaster/systems/`, `edgecaster/content/`
Supersedes: none

# Entity + Chakra Unification Overview

Updated: 2026-04-12
Audience: human collaborators

## Why this document exists

The main vision and master-plan documents for entity/chakra unification are
useful, but they are dense and implementation-heavy. This document is the
shorter collaborator-facing explanation:

- what the strategy is
- what is changing in the game
- why we are doing it
- what it will let us build later

If you want the high-level design lock, read
`entity_chakra_unification_vision.txt`.
If you want the implementation roadmap, read
`entity_chakra_unification_master_plan.txt`.

## Short version

We are moving Edgecaster toward one shared world model where:

1. everything with meaningful gameplay identity is an entity
2. chakras are not a second ontology, but a role an entity can play inside a larger hierarchy
3. bodies, items, rooms, buildings, cities, and other structures can all be represented through the same containment and geometry framework
4. simulation, persistence, attention, and fractal/rune interactions can all read from that same shared framework

In plain English: we want the game to stop having one system for actors, one for body parts, one for buildings, one for map objects, one for inventory, and another for chakras. We want one underlying model that all of those systems can use.

## The current problem

Right now the codebase still has several partially separate ways of thinking
about the world:

- actors and items are runtime entities
- body anatomy is often represented through `body_schema`
- chakra gameplay often still reads `ChakraState`
- world structures and POIs have their own realization paths
- attention, persistence, containment, and geometry are only partly unified

This causes a few recurring problems:

- duplicated logic
- brittle bridges between systems
- legacy state that has to be mirrored in multiple places
- difficulty making geometry matter in a deep, consistent way
- difficulty scaling the same ideas from body parts up to buildings and cities

The unification effort is meant to fix that at the root.

## Core idea

The key idea is that "chakra" is not a separate type of thing. It is a way of
talking about an entity that is acting as part of a larger structure.

Examples:

- a hand is an entity inside an arm/body hierarchy
- a room is an entity inside a building hierarchy
- a building is an entity inside a neighborhood or city hierarchy
- a market could be an entity composed of buildings and stalls
- a rune anchor could be an entity with meaningful substructure

So when we say "every chakra is an entity," what we really mean is:

- every meaningful subcomponent should be representable in the same world model
- those subcomponents should have geometry, identity, and relationships
- other systems should be able to query them without needing bespoke adapters

## Important terms

### Entity

A thing with meaningful gameplay identity.

It can persist, be referred to, move, be attached to other things, or take part
in simulation.

### Chakra

A role word for an entity inside a larger composed system.

For example, a body part can be treated as a chakra of a body. A building could
eventually be treated as a chakra of a district. The point is not mystic flavor
alone; the point is shared composition semantics.

### Containment tree

The main parent/child structure of the world.

This is the "what is inside what" hierarchy:

- actor contains body nodes
- player inventory contains items
- building contains rooms
- city contains neighborhoods

The current plan prefers a strict tree for containment so identity and
parentage stay legible and deterministic.

### Geometry

The absolute positions and spatial relationships of entities and sub-entities.

This matters because in Edgecaster, geometry is not just rendering. It is
intended to be part of gameplay truth, especially for fractals, runes, body
patterns, and other magical interactions.

### Attention / expansion / collapse

The game cannot simulate the entire world in full detail every tick.

So the world needs to support:

- collapsed entities when far away or not relevant
- expanded entities when detail is needed
- deterministic re-realization when detail comes back into focus

The important rule is that attention changes fidelity, not truth.

## What the strategy is

The migration strategy has several layers.

### 1. Give everything stable identity

The game is standardizing on stable `entity_id` values as the main runtime and
persistence identity.

This is the base requirement for everything else. If identity is unstable, then
containment, persistence, geometry, and simulation all become fragile.

### 2. Move toward one shared containment graph

Instead of many systems secretly owning parent/child relationships, we are
moving toward one authoritative entity graph.

That graph records things like:

- parent/child relationships
- socket/attachment points
- lifecycle state
- layout and rule metadata

This is the backbone that lets bodies, inventories, buildings, and world
structures all behave in a comparable way.

### 3. Make geometry first-class

We want one shared geometry/query layer that can answer questions like:

- what are this actor's realized body nodes?
- what is the pattern formed by these linked entities?
- what is the exact geometry here, and what is only approximate?
- can this system query shape data without inventing fake runtime objects?

This matters for future rune and fractal gameplay.

### 4. Use attention to control detail, not ontology

When something is offscreen or far away, it may be collapsed.
When it becomes relevant, it may be expanded.

But the same underlying entity model should apply in both cases.

We do not want "city mode" to use one ontology and "local map mode" to use
another.

### 5. Add a reducer/rule layer

Once entities and sub-entities share a common structure, we can compute derived
state across the hierarchy.

For chakras, this means channel values can propagate and combine through the
graph in deterministic ways.

That reducer layer is what eventually lets the game say:

- this body configuration changes spell behavior
- this structure is unstable because of its subcomponents
- this larger pattern inherits and transforms properties from its children

### 6. Cut over gameplay gradually, then delete legacy code

This is not a rewrite-from-scratch plan.

The repo is being migrated in slices:

- add new substrate
- bridge old systems onto it
- move real gameplay readers and writers onto the new path
- mark old compatibility code for deletion
- delete old code once the new path is stable

That aggressive cleanup step is important. Otherwise the codebase would just
accumulate permanent migration layers.

## What is changing in the game right now

Here is the practical version of what the refactor is currently changing.

### Stable runtime identity is becoming more important

More systems are now expected to operate on `entity_id` rather than ad hoc
runtime references or lineage-shaped assumptions.

### Bodies are starting to become real runtime sub-entities

Actor body structure is being turned into deterministic body-node entities.

That means the game can increasingly talk about:

- actor
- body
- head
- arm
- hand

as actual runtime nodes in one hierarchy, rather than only as schema data.

### Chakra state is moving away from actor-only caches

`ChakraState` still exists as a compatibility layer, but the long-term goal is
for shared entity/chakra structures to be the real authority.

In other words, the game is moving away from:

- "the actor has a special chakra blob"

and toward:

- "the actor is a hierarchy of meaningful sub-entities with channels and geometry"

### The reducer is entering real runtime flow

The reducer is no longer just a speculative side system.

It is beginning to participate in live gameplay by:

- consuming dirty/clean graph state
- caching reduced channel snapshots
- feeding at least one real gameplay consumer

This is an early step, but it is an important one, because it means the new
substrate is starting to matter for actual play behavior.

### Attention and lifecycle are becoming more unified

Expansion/collapse behavior for deterministic hierarchies is becoming more
shared and explicit. That will matter more and more as bodies, buildings, POIs,
and world structures all move onto the same model.

## Concrete example of the direction

### Before

The player body might be:

- an actor runtime object
- a `body_schema`
- a `ChakraState`
- some pattern-generation helper logic

Meanwhile a city might be:

- a site prototype
- a POI realization path
- some custom attention logic
- some special-case building spawn code

These are conceptually similar hierarchies, but they do not behave like one
shared system.

### After

The player body and the city are both expressed through the same general ideas:

- stable entities
- containment relationships
- queryable geometry
- lifecycle state
- rule-driven derived behavior

They are still different kinds of content, but they stop being different
ontologies.

## What this will allow us to do going forward

This refactor is not just cleanup. It is meant to unlock real design space.

### 1. Geometry-driven magic and fractal interactions

Because body parts and other substructures can exist in a shared geometric
framework, systems like fractal casting and rune logic can query meaningful
patterns rather than relying on one-off adapters.

That is a major part of the long-term Edgecaster promise.

### 2. One model across scales

The same conceptual machinery can apply to:

- a hand
- a body
- a room
- a building
- a city
- eventually larger regional structures

That makes it much easier to build genuinely scale-aware mechanics.

### 3. Better persistence and offscreen truth

If entities, sub-entities, and their state live in one shared model, then the
game can preserve truth more cleanly as areas collapse and re-expand.

This supports the broader Yoga principle that observation changes fidelity, not
reality.

### 4. Cleaner inventory/body/equipment logic

Long-term, items moving between map, inventory, and body/equipment contexts can
be modeled as identity-preserving containment changes instead of bespoke
special cases.

### 5. Stronger quest and world-system integration

Quest objects, rune anchors, buildings, NPCs, and faction structures can be
referenced through the same identity and hierarchy framework.

That should make quest logic, state tracking, and world consequences more
coherent.

### 6. A smaller, cleaner codebase after migration

One of the explicit goals is to delete the old bridges once the new path is
stable.

Success here is not "two systems forever."
Success is:

- one better system
- a cleaner architecture
- fewer special cases

## What is still transitional

A few important things are still mid-migration:

- `ChakraState` still exists
- some gameplay still reads `body_schema` directly
- some UI paths still use compatibility logic
- inventory/map/world authority is not fully cut over
- not every gameplay system reads from shared geometry or reduced channels yet

So this work is already changing the substrate, but it is not finished.

## What collaborators should keep in mind

When designing or reviewing features during this migration, the most useful
questions are:

1. Should this thing really be an entity?
2. Is this relationship containment, or just a typed relation?
3. Is the geometry part of gameplay truth, or only presentation?
4. Are we adding a new parallel authority path by accident?
5. Is this a temporary bridge that should be tagged for deletion later?

That framing helps keep new work aligned with the migration rather than
quietly reintroducing old fragmentation.

## Bottom line

The entity/chakra unification is a strategy for making Edgecaster's simulation
more coherent, more geometric, more scalable, and more legible.

It is turning several partially separate world models into one shared model.

If it succeeds, it will let Edgecaster support:

- better multiscale simulation
- deeper geometry-driven magic
- cleaner persistence and realization
- stronger integration between bodies, items, structures, cities, and quests
- a leaner codebase with less legacy baggage

That is why the refactor is large, and also why it is worth doing.
