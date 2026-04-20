"""Deterministic entity identity helpers.

This module provides stable runtime ids for deterministic world generation paths.
"""

from __future__ import annotations

from hashlib import blake2s
from typing import Any, Iterable, Tuple


def world_seed(game: object) -> int:
    """Best-effort world seed lookup."""
    try:
        seed = int(getattr(game, "fractal_seed", 0) or 0)
    except Exception:
        seed = 0
    if seed:
        return int(seed)
    try:
        cfg = getattr(game, "cfg", None)
        seed = int(getattr(cfg, "seed", 0) or 0)
    except Exception:
        seed = 0
    return int(seed)


def stable_int_hash(*parts: object) -> int:
    """Generate a stable FNV-1a hash from parts (deterministic within run)."""
    s = "|".join(str(p) for p in parts)
    h = 2166136261
    for ch in s:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return int(h)


def _stable_parts(parts: Iterable[object]) -> str:
    out: list[str] = []
    for p in parts:
        if isinstance(p, dict):
            items = sorted((str(k), str(v)) for k, v in p.items())
            out.append("{" + ",".join(f"{k}:{v}" for k, v in items) + "}")
            continue
        if isinstance(p, (list, tuple, set)):
            out.append("[" + ",".join(str(x) for x in p) + "]")
            continue
        out.append(str(p))
    return "|".join(out)


def _slug(s: Any) -> str:
    raw = str(s or "unknown").strip().lower()
    out = []
    for ch in raw:
        if ch.isalnum() or ch in ("_", "-", "."):
            out.append(ch)
        else:
            out.append("_")
    normalized = "".join(out).strip("_")
    return normalized or "unknown"


def deterministic_runtime_id(
    game: object,
    *,
    recipe_id: str,
    abs_pos: Tuple[int, int],
    namespace: str = "ent",
    salt: str | None = None,
) -> str:
    """Create a deterministic runtime id from seed + recipe + absolute location."""
    x, y = int(abs_pos[0]), int(abs_pos[1])
    seed = int(world_seed(game))
    rid = _slug(recipe_id)
    ns = _slug(namespace)
    payload = _stable_parts(("v1", seed, rid, x, y, salt or ""))
    digest = blake2s(payload.encode("utf-8"), digest_size=8).hexdigest()
    return f"{ns}:{rid}:{x},{y}:{digest}"
