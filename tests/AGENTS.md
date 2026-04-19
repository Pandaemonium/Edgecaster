# Tests Guide

Purpose: guide agents working in the `tests/` directory.

## Scope

This file applies to `tests/`.

## Test Command

- Run the suite with:
  ```bash
  python -m pytest
  ```
- Verified on 2026-04-19:
  - `python -m pytest --collect-only -q` collected 650 tests

## What Belongs Here

- focused behavior tests for shared systems
- regression tests for refactors and architectural migrations
- coverage for current invariants such as ABS-space handling, entity identity, and shared runtime policies

## Expectations

- Add or update tests when changing shared system behavior.
- Prefer small, targeted tests that name the invariant being protected.
- When a new area of the codebase gets a cluster of tests, update `INDEX.md`.
- Keep test names descriptive enough that failures are useful without opening the file.

## Read First

1. `INDEX.md`
2. `../vision_documents/architecture.txt`
3. local production files touched by the change

## Maintenance Rules

- Update `INDEX.md` when test ownership changes, new clusters appear, or a file becomes the main regression home for a subsystem.
- If tests encode a major architectural invariant, make sure the relevant docs still describe that invariant accurately.
