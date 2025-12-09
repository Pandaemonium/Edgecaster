from __future__ import annotations

import pathlib
import yaml
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any, Optional


@dataclass(frozen=True)
class NPCSpawnSpec:
    npc_id: str
    name: Optional[str] = None
    glyph: Optional[str] = None
    color: Optional[Tuple[int, int, int]] = None
    offsets: List[Tuple[int, int]] = field(default_factory=list)


@dataclass(frozen=True)
class POI:
    id: str
    coord: Tuple[int, int, int]  # exact match for now
    npcs: List[NPCSpawnSpec] = field(default_factory=list)
    # Future: entities, items, layout overrides


def _load_pois() -> Dict[str, POI]:
    path = pathlib.Path(__file__).resolve().parent / "pois.yaml"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    out: Dict[str, POI] = {}
    for pid, spec in data.items():
        coord = tuple(spec.get("coord", (0, 0, 0)))
        npc_specs = []
        for npc in spec.get("npcs", []) or []:
            offsets = [tuple(o) for o in npc.get("offsets", [])]
            color_raw = npc.get("color")
            color = tuple(color_raw) if color_raw else None
            npc_specs.append(
                NPCSpawnSpec(
                    npc_id=npc.get("npc_id", ""),
                    name=npc.get("name"),
                    glyph=npc.get("glyph"),
                    color=color,  # type: ignore[arg-type]
                    offsets=offsets,
                )
            )
        out[pid] = POI(id=pid, coord=coord, npcs=npc_specs)
    return out


POIS: Dict[str, POI] = _load_pois()
