# UI Index

Purpose: map the reusable UI modules in `edgecaster/ui/`.
Status: active-plan
Last verified: 2026-04-10
Canonical for: ui-folder navigation
Related docs: `edgecaster/ui/AGENTS.md`, `vision_documents/architecture.txt`
Related code: `edgecaster/ui/`
Supersedes: none

## Main Files

- `widgets.py` - generic widget framework and common controls
- `ability_bar.py` - ability bar model, renderer, and hit-test wrapper
- `status_header.py` - top HUD status widget
- `__init__.py` - package export glue

## Maintenance Rules

- Update this file when a new reusable widget module becomes important enough to join the normal reading path.
