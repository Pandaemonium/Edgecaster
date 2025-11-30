from __future__ import annotations

class Scene:
    """Abstract base for all scenes."""

    def run(self, manager: "SceneManager") -> None:  # type: ignore[name-defined]
        """Run this scene. When finished, call manager.set_scene(...) to choose what comes next."""
        raise NotImplementedError
