# Content Guide

Purpose: guide work inside `edgecaster/content/`, where content definitions and content loaders live.

## Scope

This file applies to `edgecaster/content/`.

## What Belongs Here

- YAML and Python content definitions
- content loaders and registry bridges
- content-facing fallback definitions used by systems or scenes

## What Does Not Belong Here

- runtime simulation code that belongs in `edgecaster/systems/`
- renderer logic
- state dataclasses

## Local Invariants

- Prefer entities/enemies/site prototypes over ad hoc additions to `npcs.py` when possible.
- Keep prototype-bucket files and dedicated-loader files conceptually separate.
- Content should flow through prototype resolution and spawn factories rather than direct runtime construction.
- Be careful not to confuse prototype IDs with runtime entity IDs.
- POI definitions are registry-backed ABS-space content, not just zone-local spawn notes.

## Read First

1. `INDEX.md`
2. `../../vision_documents/architecture.txt`
3. `../AGENTS.md`
4. the loader or system that consumes the content file you are editing

## Maintenance Rules

- Update `INDEX.md` when a content file changes loader/consumer ownership or when a new content registry is added.
- If schema expectations shift, update both this file and the relevant loader documentation in the same pass.
- If a content change affects structure, commands, or tests, update the relevant local indexes and architecture docs.
