"""
Param System - Stat-gated action parameters.

This module manages the parameter definitions and state for actions that have
tunable values (like branch angle, activation radius, etc.) that are gated
by character stats.

Extracted from game.py as part of the SLICE 5 refactor.
See vision_documents/spring_cleaning.txt for details.
"""

from typing import Dict, List, Tuple, Callable, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from edgecaster.game import Game


# ---------------------------------------------------------------------------
# Default Parameter Definitions
# ---------------------------------------------------------------------------

def default_param_defs() -> Dict[str, Dict[str, dict]]:
    """Return the default parameter definitions for all actions.

    Each action maps to a dict of parameter names, each with:
    - values: list of possible values (indexed by tier)
    - thresholds: minimum stat value required to unlock each tier
    - stat: which stat gates this parameter (int, res, etc.)
    - label: UI display name
    """
    return {
        "branch": {
            "angle": {
                "values": [30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90],
                "thresholds": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
                "stat": "int",
                "label": "Angle",
            },
            "count": {
                "values": [2, 3, 4, 5],
                "thresholds": [0, 3, 5, 7],
                "stat": "int",
                "label": "Branches",
            },
        },
        "koch": {
            "height": {
                "values": [0.25, 0.4, 0.6],
                "thresholds": [0, 3, 6],
                "stat": "int",
                "label": "Amplitude",
            },
            "flip": {
                "values": [False, True],
                "thresholds": [0, 5],
                "stat": "int",
                "label": "Mirror",
            },
        },
        "subdivide": {
            "parts": {
                "values": [2, 3, 4, 5, 6],
                "thresholds": [0, 2, 4, 6, 8],
                "stat": "int",
                "label": "Segments",
            },
        },
        "zigzag": {
            "parts": {
                "values": [4, 6, 8, 10],
                "thresholds": [0, 2, 4, 6],
                "stat": "int",
                "label": "Segments",
            },
            "amp": {
                "values": [0.1, 0.2, 0.3],
                "thresholds": [0, 3, 6],
                "stat": "int",
                "label": "Amplitude",
            },
        },
        "activate_all": {
            "radius": {
                "values": [0.5, 1.0, 1.5, 2.0, 3.0, 4.0],
                "thresholds": [0, 1, 2, 3, 5, 8],
                "stat": "res",
                "label": "Radius",
            },
            "damage": {
                "values": [1, 2, 3],
                "thresholds": [0, 4, 8],
                "stat": "res",
                "label": "Damage",
            },
        },
        "activate_seed": {
            "neighbor_depth": {
                "values": [1, 2, 3],
                "thresholds": [0, 3, 6],
                "stat": "res",
                "label": "Depth",
            },
            "damage": {
                "values": [1, 2, 3],
                "thresholds": [0, 4, 8],
                "stat": "res",
                "label": "Damage",
            },
        },
        "custom": {
            "amplitude": {
                "values": [1.0, 0.9, 0.8, 0.7],
                "thresholds": [0, 1, 2, 3],
                "stat": "int",
                "label": "Scale",
            },
        },
        "winter_hue": {
            "radius": {
                "values": [1, 2, 3, 4],
                "thresholds": [0, 2, 4, 6],
                "stat": "res",
                "label": "Radius",
            },
            "scale": {
                "values": [1.0, 1.5, 2.0, 3.0],
                "thresholds": [0, 5, 10, 15],
                "stat": "res",
                "label": "Intensity Scale",
            },
        },
        "freeze": {
            "damage_scale": {
                "values": [0.05, 0.1, 0.2, 0.3],
                "thresholds": [0, 2, 4, 6],
                "stat": "res",
                "label": "Dmg / Blue",
            },
            "slow_scale": {
                "values": [0.02, 0.04, 0.06, 0.08],
                "thresholds": [0, 2, 4, 6],
                "stat": "res",
                "label": "Slow / Blue",
            },
        },
        "corrosive_melt": {
            "damage_scale": {
                "values": [20.0, 30.0, 40.0, 50.0],
                "thresholds": [0, 2, 4, 6],
                "stat": "res",
                "label": "Dmg / Green",
            },
            "mana_cost": {
                "values": [30, 25, 20, 15],
                "thresholds": [0, 3, 6, 9],
                "stat": "res",
                "label": "Mana Cost",
            },
        },
        "corruption_cone": {
            "height": {
                "values": [0.5, 1.0, 1.5, 2.0],
                "thresholds": [0, 2, 4, 6],
                "stat": "res",
                "label": "Height",
            },
            "slope": {
                "values": [0.25, 0.18, 0.12, 0.08],
                "thresholds": [0, 2, 4, 6],
                "stat": "res",
                "label": "Slope",
            },
        },
        "place_rune_anchor": {
            # Range is expressed in tiles for ergonomics, then converted to Julia-plane sigma
            # using tile_julia_grid step size at runtime.
            #
            # NOTE: Rune anchors are intentionally *not* stat-gated. We want designers (and
            # later players, via progression systems) to be able to freely tune anchor
            # range/strength regardless of the host body's attributes.
            "range": {
                "values": [25.0, 60.0, 140.0, 260.0],
                "thresholds": [0, 0, 0, 0],
                "stat": "res",
                "label": "Range (tiles)",
            },
            "strength": {
                "values": [0.5, 0.75, 1.0],
                "thresholds": [0, 0, 0],
                "stat": "res",
                "label": "Strength",
            },
        },
        "polygon": {
            "sides": {
                "values": [3, 4, 5, 6, 7, 8, 10, 12],
                "thresholds": [0, 1, 2, 3, 4, 5, 6, 7],
                "stat": "int",
                "label": "Sides",
            },
            "radius": {
                "values": [2, 3, 4, 5, 6, 8, 10],
                "thresholds": [0, 1, 2, 3, 4, 5, 6],
                "stat": "int",
                "label": "Radius",
            },
        },
        "star": {
            "points": {
                "values": [3, 4, 5, 6, 7, 8, 10, 12],
                "thresholds": [0, 1, 2, 3, 4, 5, 6, 7],
                "stat": "int",
                "label": "Points",
            },
            "outer_radius": {
                "values": [3, 4, 5, 6, 8, 10],
                "thresholds": [0, 1, 2, 3, 4, 5],
                "stat": "int",
                "label": "Outer Radius",
            },
            "inner_radius": {
                "values": [1, 2, 3, 4, 5],
                "thresholds": [0, 1, 2, 3, 4],
                "stat": "int",
                "label": "Inner Radius",
            },
        },
        "fern": {
            "growth_rate": {
                "values": [1, 2, 3, 4, 5],  # vertices per 10 ticks
                "thresholds": [0, 2, 4, 6, 8],
                "stat": "int",
                "label": "Growth Rate",
            },
            "max_vertices": {
                "values": [50, 75, 100, 150, 200],  # soft cap before pruning
                "thresholds": [0, 3, 6, 9, 12],
                "stat": "int",
                "label": "Max Vertices",
            },
            "coherence_cost": {
                "values": [2.0, 1.5, 1.0, 0.75, 0.5],  # coherence per vertex grown
                "thresholds": [0, 2, 4, 6, 8],
                "stat": "res",
                "label": "Coherence Cost",
            },
            "prune_batch": {
                "values": [1, 2, 3],  # vertices pruned per cycle
                "thresholds": [0, 4, 8],
                "stat": "int",
                "label": "Prune Speed",
            },
        },
    }


# ---------------------------------------------------------------------------
# ParamManager Class
# ---------------------------------------------------------------------------

class ParamManager:
    """Manages stat-gated action parameters.

    This class encapsulates the param_defs and param_state that were
    previously stored directly on the Game object.

    Usage:
        pm = ParamManager(stat_getter=game.effective_character_stats)
        pm.recalc_max()
        value = pm.get_value("branch", "angle")
    """

    def __init__(
        self,
        param_defs: Dict[str, Dict[str, dict]] | None = None,
        stat_getter: Callable[[], Dict[str, int]] | None = None,
    ):
        """Initialize the param manager.

        Args:
            param_defs: Parameter definitions. If None, uses default_param_defs().
            stat_getter: Callable that returns current character stats dict.
                         Used to determine which param tiers are unlocked.
        """
        self.param_defs = param_defs if param_defs is not None else default_param_defs()
        self.param_state: Dict[Tuple[str, str], int] = {}
        self._stat_getter = stat_getter

        # Initialize state to index 0 for all params
        self._init_state()

    def _init_state(self) -> None:
        """Initialize param_state with index 0 for all defined params."""
        for action, params in self.param_defs.items():
            for key in params:
                self.param_state[(action, key)] = 0

    def set_stat_getter(self, getter: Callable[[], Dict[str, int]]) -> None:
        """Set the stat getter function (for deferred initialization)."""
        self._stat_getter = getter

    def _get_stat_value(self, stat: str) -> int:
        """Get the current value of a stat."""
        if self._stat_getter is None:
            return 0
        try:
            stats = self._stat_getter()
            return int(stats.get(stat, 0))
        except Exception:
            return 0

    def allowed_index(self, action: str, key: str) -> int:
        """Return the highest tier index allowed by current stats.

        Returns -1 if the action/key doesn't exist.
        """
        if action not in self.param_defs:
            return -1
        if key not in self.param_defs[action]:
            return -1

        spec = self.param_defs[action][key]
        thresholds = spec["thresholds"]
        stat_val = self._get_stat_value(spec["stat"])

        allowed = -1
        for i, thr in enumerate(thresholds):
            if stat_val >= thr:
                allowed = i
        return allowed

    def get_value(self, action: str, key: str) -> Any:
        """Get the current value for a param, respecting stat caps.

        Special case: "activate_all"/"damage" always returns the max allowed
        (damage scales automatically with resonance).
        """
        if action == "activate_all" and key == "damage":
            spec = self.param_defs.get(action, {}).get(key)
            if not spec:
                return self._get_value_internal(action, key)
            allowed = self.allowed_index(action, key)
            values = spec["values"]
            allowed = max(0, min(allowed, len(values) - 1))
            return values[allowed]
        return self._get_value_internal(action, key)

    def _get_value_internal(self, action: str, key: str) -> Any:
        """Internal helper to get param value with stat capping."""
        if action not in self.param_defs or key not in self.param_defs[action]:
            return None

        idx = self.param_state.get((action, key), 0)
        values = self.param_defs[action][key]["values"]
        idx = max(0, min(idx, len(values) - 1))

        # Ensure we never use a value beyond the current stat cap
        allowed = self.allowed_index(action, key)
        if allowed < 0:
            allowed = 0
        idx = min(idx, allowed)

        return values[idx]

    def adjust(self, action: str, key: str, delta: int) -> Tuple[bool, str]:
        """Adjust a param by delta steps.

        Returns (success, message).
        - success=True if the param was changed
        - message contains error info if failed (e.g., "Requires INT 5")
        """
        spec = self.param_defs.get(action, {}).get(key)
        if not spec:
            return False, "Unknown parameter"

        values = spec["values"]
        allowed = self.allowed_index(action, key)
        cur_idx = self.param_state.get((action, key), 0)

        # If stats dropped, clamp stored index back into range
        if allowed < 0:
            allowed = 0
        if cur_idx > allowed:
            cur_idx = allowed
            self.param_state[(action, key)] = cur_idx

        new_idx = cur_idx + delta
        new_idx = max(0, min(new_idx, len(values) - 1))

        if new_idx > allowed:
            need = spec["thresholds"][new_idx]
            return False, f"Requires {spec['stat'].upper()} {need}"

        if new_idx == cur_idx:
            return False, ""

        self.param_state[(action, key)] = new_idx
        return True, ""

    def view(self, action: str) -> List[dict]:
        """Return UI-friendly view of params for an action.

        Returns list of dicts with keys:
        - key: param name
        - label: display label
        - value: current value
        - allowed_idx: max tier allowed by stats
        - current_idx: current tier index
        - next_req: requirement string for next tier (or "")
        """
        result = []
        params = self.param_defs.get(action, {})

        for key, spec in params.items():
            # Hide auto-scaling damage from UI
            if action == "activate_all" and key == "damage":
                continue

            cur_idx = self.param_state.get((action, key), 0)
            allowed = self.allowed_index(action, key)
            value = spec["values"][cur_idx]
            label = spec.get("label", key)

            # Next requirement string
            next_req = ""
            next_idx = cur_idx + 1
            if next_idx < len(spec["values"]):
                need = spec["thresholds"][next_idx]
                if self._get_stat_value(spec["stat"]) < need:
                    next_req = f"{spec['stat'].upper()} {need}"

            result.append({
                "key": key,
                "label": label,
                "value": value,
                "allowed_idx": allowed,
                "current_idx": cur_idx,
                "next_req": next_req,
            })

        return result

    def recalc_max(self) -> None:
        """Set all params to the highest tier allowed by current stats.

        Exceptions:
        - "custom"/"amplitude": kept at user choice
        - "place_rune_anchor": designer/player-tuned, not auto-maxed
        """
        for action, params in self.param_defs.items():
            for key in params:
                # Keep custom amplitude at user choice (default 1.0)
                if action == "custom" and key == "amplitude":
                    continue
                # Rune-anchor range/strength are designer/player-tuned
                if action == "place_rune_anchor":
                    continue

                allowed = self.allowed_index(action, key)
                if allowed < 0:
                    allowed = 0
                self.param_state[(action, key)] = allowed

    def get_state(self) -> Dict[Tuple[str, str], int]:
        """Return the current param state dict (for serialization)."""
        return dict(self.param_state)

    def set_state(self, state: Dict[Tuple[str, str], int]) -> None:
        """Restore param state from a dict (for deserialization)."""
        self.param_state = dict(state)
