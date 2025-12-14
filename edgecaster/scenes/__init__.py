"""
edgecaster.scenes package.

IMPORTANT:
Do NOT import heavy submodules here (like manager, dungeon, etc.).
Those imports can easily create circular-import chains (e.g. ascii <-> manager).

Import what you need directly from submodules, e.g.:
    from edgecaster.scenes.manager import SceneManager
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import to keep IDEs happy without runtime import cycles.
    from .manager import SceneManager  # noqa: F401

__all__: list[str] = []
