# Content Index

Purpose: map the content files in `edgecaster/content/` by loader and runtime consumer.
Status: active-plan
Last verified: 2026-04-10
Canonical for: content-folder navigation
Related docs: `edgecaster/content/AGENTS.md`, `vision_documents/architecture.txt`
Related code: `edgecaster/content/`, `edgecaster/prototypes.py`, `edgecaster/spawn_factory.py`
Supersedes: none

## Prototype Bucket Inputs

These feed the unified prototype bucket in `edgecaster/prototypes.py`:

- `entities.yaml`
- `enemies.yaml`
- `anatomy.yaml`
- `biology.yaml`
- `quests.yaml`
- `site_types.yaml`

## Dedicated Content Loaders

- `pois.py` + `pois.yaml` - POI registry loading and legacy-format conversion
- `factions.py` + `factions.yaml` + `factions_data.py` - faction data and helpers
- `merchants.py` + `merchants.yaml` - merchant stock/pricing definitions
- `sealing_runes.py` + `sealing_runes.yaml` - rune-trial/sealing content
- `rune_anchor_sieges.py` + `rune_anchor_sieges.yaml` - rune-anchor siege content
- `gods.yaml` - god definitions consumed by god systems
- `chakra_layouts.yaml` + `chakra_rules.yaml` - chakra layout/rule data

## Dialogue And NPC Fallback Layer

- `dialogues.py` - dialogue tree construction and quest/dialogue effects
- `npcs.py` - lightweight NPC fallback definitions and dialogue-facing metadata

## Maintenance Rules

- Update this file when a file changes from prototype-bucket input to dedicated-loader input or vice versa.
- Update this file when a content file gets a new primary consumer that changes how agents should find it.
