"""POI loading and registry management.

Supports both legacy format (zone-local coords) and v2 format (ABS footprints).

Legacy format (still supported):
    academy:
      coord: [1, 0, 0]
      npcs:
        - npc_id: mentor
          offsets: [[0, 0], [1, 0]]

V2 format (preferred):
    trader_city:
      kind: city
      name: "Port Tradeswind"
      footprint: {x0: 3000, y0: 2000, x1: 3180, y1: 2120}
      anchor_abs: [3090, 2060]
      npcs:
        - npc_id: mayor
          abs_positions: [[3090, 2060]]
"""
from __future__ import annotations

import pathlib
import yaml
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Any, Optional

from edgecaster.state.pois import (
    ABSRect,
    POISpec,
    NPCSpawnSpecV2,
    StructureSpec,
    EntitySpawnSpec,
)
from edgecaster.systems.poi_registry import POIRegistry

# Default zone dimensions (tiles)
DEFAULT_ZONE_W = 60
DEFAULT_ZONE_H = 40


@dataclass(frozen=True)
class NPCSpawnSpec:
    npc_id: str
    name: Optional[str] = None
    glyph: Optional[str] = None
    color: Optional[Tuple[int, int, int]] = None
    offsets: List[Tuple[int, int]] = field(default_factory=list)
    description: Optional[str] = None  


@dataclass(frozen=True)
class POI:
    id: str
    coord: Tuple[int, int, int]  # exact match for now
    npcs: List[NPCSpawnSpec] = field(default_factory=list)
    structures: List[dict] = field(default_factory=list)
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
                    description=npc.get("description"),  # NEW
                )
            )
        structures = spec.get("structures", []) or []
        out[pid] = POI(id=pid, coord=coord, npcs=npc_specs, structures=structures)
    return out


POIS: Dict[str, POI] = _load_pois()


def _coord_to_abs_rect(
    coord: Tuple[int, int, int],
    zone_w: int = DEFAULT_ZONE_W,
    zone_h: int = DEFAULT_ZONE_H,
) -> ABSRect:
    """Convert legacy zone coord to ABS footprint covering entire zone."""
    zx, zy, _ = coord
    return ABSRect(
        x0=zx * zone_w,
        y0=zy * zone_h,
        x1=(zx + 1) * zone_w,
        y1=(zy + 1) * zone_h,
    )


def _coord_to_anchor(
    coord: Tuple[int, int, int],
    zone_w: int = DEFAULT_ZONE_W,
    zone_h: int = DEFAULT_ZONE_H,
) -> Tuple[int, int]:
    """Convert legacy zone coord to ABS anchor (zone center)."""
    zx, zy, _ = coord
    return (zx * zone_w + zone_w // 2, zy * zone_h + zone_h // 2)


def _convert_npc_spec_to_v2(
    npc: NPCSpawnSpec,
    anchor_abs: Tuple[int, int],
) -> NPCSpawnSpecV2:
    """Convert legacy NPCSpawnSpec to v2 format."""
    # Convert zone-local offsets to ABS positions
    abs_positions: List[Tuple[int, int]] = []
    for dx, dy in npc.offsets:
        abs_positions.append((anchor_abs[0] + dx, anchor_abs[1] + dy))

    return NPCSpawnSpecV2(
        npc_id=npc.npc_id,
        name=npc.name,
        glyph=npc.glyph,
        color=npc.color,
        description=npc.description,
        abs_positions=abs_positions,
        offsets=list(npc.offsets),  # Keep legacy offsets for backwards compat
    )


def _convert_structure_to_v2(
    struct: dict,
    anchor_abs: Tuple[int, int],
) -> StructureSpec:
    """Convert legacy structure dict to v2 format."""
    kind = struct.get("kind", "unknown")

    # Copy all other fields as tags
    tags = {k: v for k, v in struct.items() if k != "kind"}

    return StructureSpec(
        kind=kind,
        relative_offset=(0, 0),  # Legacy structures are at anchor
        tags=tags,
    )


def _load_poi_spec_from_yaml(
    pid: str,
    spec: Dict[str, Any],
    zone_w: int = DEFAULT_ZONE_W,
    zone_h: int = DEFAULT_ZONE_H,
) -> POISpec:
    """Load a single POI from YAML, supporting both legacy and v2 formats."""
    # Determine format: v2 has 'footprint', legacy has 'coord'
    if "footprint" in spec:
        # V2 format - explicit ABS footprint
        fp = spec["footprint"]
        footprint = ABSRect(
            x0=fp["x0"],
            y0=fp["y0"],
            x1=fp["x1"],
            y1=fp["y1"],
        )
        anchor_raw = spec.get("anchor_abs", footprint.center)
        anchor_abs = (int(anchor_raw[0]), int(anchor_raw[1]))
        depth = spec.get("depth", 0)
        kind = spec.get("kind", "poi")
        name = spec.get("name", pid.replace("_", " ").title())
    else:
        # Legacy format - convert coord to footprint
        coord = tuple(spec.get("coord", (0, 0, 0)))
        footprint = _coord_to_abs_rect(coord, zone_w, zone_h)
        anchor_abs = _coord_to_anchor(coord, zone_w, zone_h)
        depth = coord[2] if len(coord) > 2 else 0
        kind = spec.get("kind", "legacy_poi")
        name = spec.get("name", pid.replace("_", " ").title())

    # Load NPC specs
    npc_specs: List[NPCSpawnSpecV2] = []
    for npc_data in spec.get("npcs", []) or []:
        if "abs_positions" in npc_data:
            # V2 format NPC
            abs_pos = [tuple(p) for p in npc_data.get("abs_positions", [])]
            offsets = [tuple(o) for o in npc_data.get("offsets", [])]
            color_raw = npc_data.get("color")
            color = tuple(color_raw) if color_raw else None
            npc_specs.append(
                NPCSpawnSpecV2(
                    npc_id=npc_data.get("npc_id", ""),
                    name=npc_data.get("name"),
                    glyph=npc_data.get("glyph"),
                    color=color,  # type: ignore[arg-type]
                    description=npc_data.get("description"),
                    abs_positions=abs_pos,
                    offsets=offsets,
                )
            )
        else:
            # Legacy format - convert
            offsets = [tuple(o) for o in npc_data.get("offsets", [])]
            color_raw = npc_data.get("color")
            color = tuple(color_raw) if color_raw else None
            legacy_spec = NPCSpawnSpec(
                npc_id=npc_data.get("npc_id", ""),
                name=npc_data.get("name"),
                glyph=npc_data.get("glyph"),
                color=color,  # type: ignore[arg-type]
                offsets=offsets,
                description=npc_data.get("description"),
            )
            npc_specs.append(_convert_npc_spec_to_v2(legacy_spec, anchor_abs))

    # Load structure specs
    structure_specs: List[StructureSpec] = []
    for struct_data in spec.get("structures", []) or []:
        if "abs_rect" in struct_data:
            # V2 format structure
            ar = struct_data["abs_rect"]
            abs_rect = ABSRect(x0=ar["x0"], y0=ar["y0"], x1=ar["x1"], y1=ar["y1"])
            tags = {k: v for k, v in struct_data.items() if k not in ("kind", "abs_rect")}
            structure_specs.append(
                StructureSpec(
                    kind=struct_data.get("kind", "unknown"),
                    abs_rect=abs_rect,
                    tags=tags,
                )
            )
        else:
            # Legacy format
            structure_specs.append(_convert_structure_to_v2(struct_data, anchor_abs))

    # Load entity specs (v2 only)
    entity_specs: List[EntitySpawnSpec] = []
    for ent_data in spec.get("entities", []) or []:
        abs_pos = [tuple(p) for p in ent_data.get("abs_positions", [])]
        offsets = [tuple(o) for o in ent_data.get("offsets", [])]
        entity_specs.append(
            EntitySpawnSpec(
                proto_id=ent_data.get("proto_id", ""),
                abs_positions=abs_pos,
                offsets=offsets,
                count=ent_data.get("count", 1),
                tags=ent_data.get("tags", {}),
            )
        )

    # Hierarchy (v2 only)
    parent_id = spec.get("parent_id")
    child_ids = spec.get("child_ids", [])

    # Other metadata
    seed = spec.get("seed", hash(pid) & 0xFFFFFFFF)
    tags = spec.get("tags", {})

    return POISpec(
        id=pid,
        kind=kind,
        name=name,
        footprint=footprint,
        depth=depth,
        anchor_abs=anchor_abs,
        parent_id=parent_id,
        child_ids=child_ids,
        npc_specs=npc_specs,
        structure_specs=structure_specs,
        entity_specs=entity_specs,
        seed=seed,
        tags=tags,
    )


def load_poi_registry(
    zone_w: int = DEFAULT_ZONE_W,
    zone_h: int = DEFAULT_ZONE_H,
) -> POIRegistry:
    """Load all POIs into a registry, supporting both legacy and v2 formats.

    This is the primary entry point for the new yoga-compliant POI system.
    """
    registry = POIRegistry(zone_w=zone_w, zone_h=zone_h)

    path = pathlib.Path(__file__).resolve().parent / "pois.yaml"
    if not path.exists():
        return registry

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    # First pass: create all POI specs
    for pid, spec in data.items():
        try:
            poi_spec = _load_poi_spec_from_yaml(pid, spec, zone_w, zone_h)
            registry.add(poi_spec)
        except (KeyError, ValueError, TypeError) as e:
            # Skip malformed POIs, log in debug mode
            continue

    return registry


# Module-level registry (lazy loaded)
_poi_registry: Optional[POIRegistry] = None


def get_poi_registry(
    zone_w: int = DEFAULT_ZONE_W,
    zone_h: int = DEFAULT_ZONE_H,
) -> POIRegistry:
    """Get or create the global POI registry."""
    global _poi_registry
    if _poi_registry is None:
        _poi_registry = load_poi_registry(zone_w, zone_h)
    return _poi_registry


def reset_poi_registry() -> None:
    """Reset the global POI registry (for testing or reloading)."""
    global _poi_registry
    _poi_registry = None
