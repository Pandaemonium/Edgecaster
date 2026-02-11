from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import yaml


@dataclass(frozen=True)
class RuneAnchorSiegeDef:
    id: str
    name: str
    anchor_offset: Tuple[int, int]
    fracture_offsets: List[Tuple[int, int]]
    channel_range: int
    coherence_per_channel: int
    stabilize_ticks: int
    stabilize_action_bonus: int
    stability_max: float
    stability_start: float
    stability_decay_per_tick: float
    repair_stability_gain: float
    stabilize_stability_gain: float
    spawn_radius_min: int
    spawn_radius_max: int
    wave_min_interval: int
    wave_max_interval: int
    wave_base_count: int
    wave_pressure_scale: float
    enemy_pool: List[str]
    dampening_range_tiles: float
    dampening_strength: float
    reward_bismuth_min: int
    reward_bismuth_max: int
    legacy_trial_id: str
    intro_lines: List[str] = field(default_factory=list)


_RUNE_ANCHOR_SIEGES_CACHE: Dict[str, RuneAnchorSiegeDef] | None = None


def load_rune_anchor_sieges() -> Dict[str, RuneAnchorSiegeDef]:
    """Load rune-anchor siege definitions from YAML (cached)."""
    global _RUNE_ANCHOR_SIEGES_CACHE
    if _RUNE_ANCHOR_SIEGES_CACHE is not None:
        return _RUNE_ANCHOR_SIEGES_CACHE

    yaml_path = Path(__file__).resolve().parent / "rune_anchor_sieges.yaml"
    if not yaml_path.exists():
        _RUNE_ANCHOR_SIEGES_CACHE = {}
        return _RUNE_ANCHOR_SIEGES_CACHE

    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    out: Dict[str, RuneAnchorSiegeDef] = {}
    for siege_id, spec in data.items():
        if not isinstance(spec, dict):
            continue

        anchor_offset = tuple(spec.get("anchor_offset", (0, 0)))
        fracture_offsets_raw = list(spec.get("fracture_offsets", []) or [])
        fracture_offsets: List[Tuple[int, int]] = []
        for item in fracture_offsets_raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                fracture_offsets.append((int(item[0]), int(item[1])))

        enemy_pool = [str(x) for x in list(spec.get("enemy_pool", []) or []) if x]
        intro_lines = [str(x) for x in list(spec.get("intro_lines", []) or []) if x]

        if not fracture_offsets:
            fracture_offsets = [(-4, 0), (0, 3), (4, 0)]

        out[str(siege_id)] = RuneAnchorSiegeDef(
            id=str(siege_id),
            name=str(spec.get("name", str(siege_id))),
            anchor_offset=(int(anchor_offset[0]), int(anchor_offset[1])),
            fracture_offsets=fracture_offsets,
            channel_range=max(1, int(spec.get("channel_range", 1))),
            coherence_per_channel=max(1, int(spec.get("coherence_per_channel", 1))),
            stabilize_ticks=max(1, int(spec.get("stabilize_ticks", 90))),
            stabilize_action_bonus=max(1, int(spec.get("stabilize_action_bonus", 4))),
            stability_max=max(1.0, float(spec.get("stability_max", 100.0))),
            stability_start=max(1.0, float(spec.get("stability_start", 72.0))),
            stability_decay_per_tick=max(0.0, float(spec.get("stability_decay_per_tick", 0.18))),
            repair_stability_gain=max(0.0, float(spec.get("repair_stability_gain", 16.0))),
            stabilize_stability_gain=max(0.0, float(spec.get("stabilize_stability_gain", 9.0))),
            spawn_radius_min=max(1, int(spec.get("spawn_radius_min", 6))),
            spawn_radius_max=max(1, int(spec.get("spawn_radius_max", 10))),
            wave_min_interval=max(1, int(spec.get("wave_min_interval", 8))),
            wave_max_interval=max(1, int(spec.get("wave_max_interval", 14))),
            wave_base_count=max(1, int(spec.get("wave_base_count", 2))),
            wave_pressure_scale=max(0.0, float(spec.get("wave_pressure_scale", 3.0))),
            enemy_pool=enemy_pool or ["imp"],
            dampening_range_tiles=max(1.0, float(spec.get("dampening_range_tiles", 14.0))),
            dampening_strength=max(0.0, min(1.0, float(spec.get("dampening_strength", 0.55)))),
            reward_bismuth_min=max(0, int(spec.get("reward_bismuth_min", 20))),
            reward_bismuth_max=max(0, int(spec.get("reward_bismuth_max", 36))),
            legacy_trial_id=str(spec.get("legacy_trial_id", "")),
            intro_lines=intro_lines,
        )

    _RUNE_ANCHOR_SIEGES_CACHE = out
    return out


def get_rune_anchor_siege(siege_id: str) -> RuneAnchorSiegeDef | None:
    """Fetch a rune-anchor siege definition by id (cached)."""
    return load_rune_anchor_sieges().get(str(siege_id))


def clear_rune_anchor_sieges_cache() -> None:
    """Clear cached rune-anchor siege definitions (testing convenience)."""
    global _RUNE_ANCHOR_SIEGES_CACHE
    _RUNE_ANCHOR_SIEGES_CACHE = None

