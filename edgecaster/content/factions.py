from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Dict

import yaml


@dataclass(frozen=True)
class FactionDef:
    id: str
    name: str
    description: str = ""
    # How this faction feels about other factions (and itself).
    # Positive => like/love, negative => dislike/hate.
    opinions: Dict[str, int] = field(default_factory=dict)

    # Default hostility threshold used by AI/interaction:
    # an entity is hostile if its perceived standing is < hostile_below.
    hostile_below: int = 125

    # Reputation change magnitudes applied to the *player* for kill/help events.
    # If this faction likes the target => help increases, kill decreases.
    # If this faction dislikes the target => help decreases, kill increases.
    kill_rep_delta: int = 5
    help_rep_delta: int = 5


def _load_factions() -> Dict[str, FactionDef]:
    path = pathlib.Path(__file__).resolve().parent / "factions.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}

    out: Dict[str, FactionDef] = {}
    for fid, spec in data.items():
        if not isinstance(spec, dict):
            continue
        opinions_raw = spec.get("opinions", {}) or {}
        opinions: Dict[str, int] = {}
        if isinstance(opinions_raw, dict):
            for k, v in opinions_raw.items():
                try:
                    opinions[str(k)] = int(v)
                except Exception:
                    continue

        out[str(fid)] = FactionDef(
            id=str(fid),
            name=str(spec.get("name") or fid),
            description=str(spec.get("description") or ""),
            opinions=opinions,
            hostile_below=int(spec.get("hostile_below", 125) or 125),
            kill_rep_delta=int(spec.get("kill_rep_delta", 5) or 5),
            help_rep_delta=int(spec.get("help_rep_delta", 5) or 5),
        )
    return out


FACTIONS: Dict[str, FactionDef] = _load_factions()

