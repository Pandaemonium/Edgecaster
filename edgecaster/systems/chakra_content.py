"""Load chakra layout/rule content."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


_LAYOUTS_CACHE: Optional[Dict[str, Any]] = None
_RULES_CACHE: Optional[Dict[str, Any]] = None


def _content_file(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "content" / name


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def chakra_layouts() -> Dict[str, Any]:
    global _LAYOUTS_CACHE
    if _LAYOUTS_CACHE is None:
        _LAYOUTS_CACHE = _load_yaml(_content_file("chakra_layouts.yaml"))
    return _LAYOUTS_CACHE


def chakra_rules() -> Dict[str, Any]:
    global _RULES_CACHE
    if _RULES_CACHE is None:
        _RULES_CACHE = _load_yaml(_content_file("chakra_rules.yaml"))
    return _RULES_CACHE


def clear_cache() -> None:
    global _LAYOUTS_CACHE, _RULES_CACHE
    _LAYOUTS_CACHE = None
    _RULES_CACHE = None


def resolve_layout_component(layout_id: Optional[str]) -> Optional[Dict[str, Any]]:
    lid = str(layout_id or "").strip()
    if not lid:
        return None
    layouts = chakra_layouts().get("layouts")
    if not isinstance(layouts, dict):
        return None
    row = layouts.get(lid)
    if not isinstance(row, dict):
        return None
    comp = row.get("component")
    if not isinstance(comp, dict):
        return None
    return deepcopy(comp)

