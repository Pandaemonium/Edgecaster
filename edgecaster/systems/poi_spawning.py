"""POI content realization — re-export facade.

All orchestration logic has moved to ``poi_worldgen.spawn_poi_contents``.
This module is kept as a thin facade so existing call-sites and tests
continue to work without changes during the transition.

[LEGACY_DELETE][ENTITY_CHAKRA][PHASE_8]
Remove this file once all call-sites import from poi_worldgen directly.
"""

from __future__ import annotations

from edgecaster.systems.poi_worldgen import spawn_poi_contents

__all__ = ["spawn_poi_contents"]
