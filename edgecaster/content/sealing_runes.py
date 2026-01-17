from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import yaml


@dataclass(frozen=True)
class SealPatternStep:
    gen: str
    times: int = 1
    params: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SealTrialDef:
    id: str
    name: str
    root_offset: Tuple[int, int]
    terminus_offset: Tuple[int, int]
    full_span_ratio: float | None
    full_angle_deg: float
    full_angle_jitter_deg: float
    full_margin_tiles: int
    center_jitter_tiles: int
    missing_center: Tuple[float, float]
    missing_radius: float
    repair_start_step: int
    repair_target_scale: float
    repair_len_tolerance: float
    snap_radius: int
    score_threshold: float
    blur_radius: int
    required_generators: List[str]
    pattern_chain: List[SealPatternStep]


_SEAL_TRIALS_CACHE: Dict[str, SealTrialDef] | None = None


def load_seal_trials() -> Dict[str, SealTrialDef]:
    """Load sealing rune trial definitions from YAML (cached)."""
    global _SEAL_TRIALS_CACHE
    if _SEAL_TRIALS_CACHE is not None:
        return _SEAL_TRIALS_CACHE

    yaml_path = Path(__file__).resolve().parent / "sealing_runes.yaml"
    if not yaml_path.exists():
        _SEAL_TRIALS_CACHE = {}
        return _SEAL_TRIALS_CACHE

    with yaml_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    out: Dict[str, SealTrialDef] = {}
    for trial_id, spec in data.items():
        if not isinstance(spec, dict):
            continue

        root_offset = tuple(spec.get("root_offset", (0, 0)))
        terminus_offset = tuple(spec.get("terminus_offset", (8, 0)))
        full_span_ratio = spec.get("full_span_ratio", None)
        if full_span_ratio is not None:
            full_span_ratio = float(full_span_ratio)

        full_angle_deg = float(spec.get("full_angle_deg", 0.0))
        full_angle_jitter_deg = float(spec.get("full_angle_jitter_deg", 0.0))
        full_margin_tiles = int(spec.get("full_margin_tiles", 1))
        center_jitter_tiles = int(spec.get("center_jitter_tiles", 0))

        missing_center = tuple(spec.get("missing_center", (0.0, 0.0)))
        missing_radius = float(spec.get("missing_radius", 2.0))

        repair_start_step = int(spec.get("repair_start_step", -1))
        repair_target_scale = float(spec.get("repair_target_scale", 1.0))
        repair_len_tolerance = float(spec.get("repair_len_tolerance", 0.35))

        snap_radius = int(spec.get("snap_radius", 3))
        score_threshold = float(spec.get("score_threshold", 0.65))
        blur_radius = int(spec.get("blur_radius", 2))

        required = list(spec.get("required_generators", []) or [])

        chain_steps: List[SealPatternStep] = []
        for step in spec.get("pattern_chain", []) or []:
            if not isinstance(step, dict):
                continue
            gen = str(step.get("gen", ""))
            times = int(step.get("times", 1))
            params = dict(step.get("params", {}) or {})
            if gen:
                chain_steps.append(SealPatternStep(gen=gen, times=times, params=params))

        out[str(trial_id)] = SealTrialDef(
            id=str(trial_id),
            name=str(spec.get("name", str(trial_id))),
            root_offset=(int(root_offset[0]), int(root_offset[1])),
            terminus_offset=(int(terminus_offset[0]), int(terminus_offset[1])),
            full_span_ratio=full_span_ratio,
            full_angle_deg=full_angle_deg,
            full_angle_jitter_deg=full_angle_jitter_deg,
            full_margin_tiles=full_margin_tiles,
            center_jitter_tiles=center_jitter_tiles,
            missing_center=(float(missing_center[0]), float(missing_center[1])),
            missing_radius=missing_radius,
            repair_start_step=repair_start_step,
            repair_target_scale=repair_target_scale,
            repair_len_tolerance=repair_len_tolerance,
            snap_radius=snap_radius,
            score_threshold=score_threshold,
            blur_radius=blur_radius,
            required_generators=[str(x) for x in required],
            pattern_chain=chain_steps,
        )

    _SEAL_TRIALS_CACHE = out
    return out


def get_seal_trial(trial_id: str) -> SealTrialDef | None:
    """Fetch a seal trial definition by id (cached)."""
    trials = load_seal_trials()
    return trials.get(trial_id)


def clear_seal_trials_cache() -> None:
    """Clear the trial definition cache (for testing)."""
    global _SEAL_TRIALS_CACHE
    _SEAL_TRIALS_CACHE = None
