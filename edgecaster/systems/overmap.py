"""
Overmap system for world map rendering and Julia grid management.

Handles:
- Julia coordinate grid (tile_julia_grid) for fractal terrain
- Overmap parameter initialization
- Background world map rendering thread
- Corruption visuals (level, hotspots, anchors)
- Zone-to-Julia coordinate mapping

The overmap is "intimately linked" with local map generation:
- Local zones query the Julia grid to generate fractal terrain
- Corruption anchors placed in-world affect both local and overmap visuals

All functions accept (game, ...) as parameters to read/modify game state.
"""

from __future__ import annotations

import os
import random
import sys
import threading
import time
import traceback
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from edgecaster.game import Game


def _should_start_world_map_thread() -> bool:
    """Return True when it is safe/useful to start background overmap rendering.

    We skip background rendering under pytest/mocked pygame so unit tests don't
    spawn many expensive render threads (which can make the suite appear hung).
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    if "pytest" in sys.modules:
        return False
    pg = sys.modules.get("pygame")
    if pg is not None and pg.__class__.__name__ == "MagicMock":
        return False
    return True


def build_tile_julia_grid(game: "Game") -> None:
    """Precompute per-tile Julia coordinates across the whole world grid.

    The tile_julia_grid maps each world tile to its (jx, jy) position in
    the Julia plane, allowing local zone generation to sample the fractal
    at the correct coordinates.
    """
    if not getattr(game, "overmap_params", None):
        return
    p = game.overmap_params
    # Require julia extents from overmap_params
    if not all(k in p for k in ("view_min_jx", "view_max_jx", "view_min_jy", "view_max_jy")):
        return
    total_x = game.cfg.world_map_screens * game.cfg.world_width
    total_y = game.cfg.world_map_screens * game.cfg.world_height
    if total_x <= 0 or total_y <= 0:
        return
    step_x = (p["view_max_jx"] - p["view_min_jx"]) / max(1, total_x - 1)
    step_y = (p["view_max_jy"] - p["view_min_jy"]) / max(1, total_y - 1)
    jx = [p["view_min_jx"] + i * step_x for i in range(total_x)]
    jy = [p["view_min_jy"] + i * step_y for i in range(total_y)]
    game.tile_julia_grid = {
        "x": jx,
        "y": jy,
        "total_x": total_x,
        "total_y": total_y,
        "step_x": step_x,
        "step_y": step_y,
        "view_min_jx": p["view_min_jx"],
        "view_max_jx": p["view_max_jx"],
        "view_min_jy": p["view_min_jy"],
        "view_max_jy": p["view_max_jy"],
    }


def init_overmap_params_and_grid(game: "Game") -> None:
    """Set fixed overmap params from curated c-path bounds and start background render.

    This initializes:
    - overmap_params dict with Julia extents and corruption settings
    - tile_julia_grid for local zone generation
    - Rune anchor POIs
    - Background render thread for world map
    """
    # If already initialized, do nothing.
    if getattr(game, "overmap_params", None) and getattr(game, "tile_julia_grid", None):
        return
    try:
        from edgecaster.scenes.world_map_scene import WorldMapScene
        wm = WorldMapScene(game, span=16)
        entry = wm._pick_visual_entry()
    except Exception:
        return
    cfg = game.cfg
    total_w = cfg.world_map_screens * cfg.world_width
    total_h = cfg.world_map_screens * cfg.world_height
    min_wx = 0.0
    min_wy = 0.0
    max_wx = float(total_w)
    max_wy = float(total_h)
    # stash params without surface; render thread will fill it
    game.corruption_hotspots = [(0.8, 0.0, 0.3, 0.05)]  # your temporary single-blob default
    game.overmap_params = {
        "min_wx": min_wx,
        "min_wy": min_wy,
        "span_x": max_wx,
        "span_y": max_wy,
        "visual_c": entry["c"],
        "corruption_level": float(getattr(game, "corruption_level", 0.0) or 0.0),
        "corruption_seed": int(getattr(game, "corruption_seed", 1337) or 1337),
        "corruption_hotspots": list(getattr(game, "corruption_hotspots", []) or []),
        "corruption_anchors": list(getattr(game, "corruption_anchors", []) or []),
        "corruption_spline_weight": float(getattr(game, "corruption_spline_weight", 0.0) or 0.0),
        "climate_config": getattr(game, "climate_config", None),
        "surface_size": (0, 0),
        "surface": None,
        "orig_min_wx": min_wx,
        "orig_min_wy": min_wy,
        "orig_max_wx": max_wx,
        "orig_max_wy": max_wy,
        "view_max_wx": max_wx,
        "view_max_wy": max_wy,
        "orig_min_jx": entry["x_min"],
        "orig_max_jx": entry["x_max"],
        "orig_min_jy": entry["y_min"],
        "orig_max_jy": entry["y_max"],
        "view_min_jx": entry["x_min"],
        "view_max_jx": entry["x_max"],
        "view_min_jy": entry["y_min"],
        "view_max_jy": entry["y_max"],
    }
    # build grid immediately so locals can generate
    build_tile_julia_grid(game)
    # Seed rune anchors now that we have a Julia grid.
    init_rune_anchors(game)
    # Kick off world-map rendering in a background thread during startup so the
    # first time you open the map it (usually) appears immediately.
    if _should_start_world_map_thread():
        try:
            start_world_map_thread(game, reason="startup", view=None, view_token=0)
        except Exception:
            pass


def init_rune_anchors(game: "Game") -> None:
    """Seed rune-anchor POIs and corresponding corruption suppressors.

    Creates a set of rune anchor positions scattered across the world map,
    concentrated on the left side. Each anchor suppresses corruption in
    a local area.
    """
    from edgecaster.content import pois as poi_content

    if getattr(game, "corruption_anchors", None) and len(game.corruption_anchors) > 0:
        return
    grid = getattr(game, "tile_julia_grid", None)
    if not isinstance(grid, dict):
        return

    screens = int(getattr(game.cfg, "world_map_screens", 0) or 0)
    if screens <= 0:
        return
    zone_w = int(getattr(game.cfg, "world_width", 0) or 0)
    zone_h = int(getattr(game.cfg, "world_height", 0) or 0)
    if zone_w <= 0 or zone_h <= 0:
        return

    total_x = screens * zone_w
    total_y = screens * zone_h
    xgrid = grid.get("x") or []
    ygrid = grid.get("y") or []
    if len(xgrid) < total_x or len(ygrid) < total_y:
        return

    # Clear any previous dynamic anchors (e.g. if re-initialized in the same process).
    try:
        for pid in list(poi_content.POIS.keys()):
            if str(pid).startswith("rune_anchor_"):
                del poi_content.POIS[pid]
    except Exception:
        pass

    # Target count scales gently with map width to avoid thousands of markers.
    target_count = max(12, int(round(screens * 0.6)))

    # Use an isolated RNG so anchor placement doesn't perturb gameplay RNG.
    rng = random.Random(int(game.corruption_seed) + 424242)

    try:
        occupied: set[tuple[int, int, int]] = {tuple(poi.coord) for poi in poi_content.POIS.values()}
    except Exception:
        occupied = set()

    anchors: List[Tuple[float, float, float, float]] = []
    tries = 0
    max_tries = target_count * 50
    while len(anchors) < target_count and tries < max_tries:
        tries += 1
        # Bias X to the left side: u^2 concentrates near 0.
        zx = int((rng.random() ** 2.0) * screens)
        zy = int(rng.random() * screens)
        coord = (zx, zy, 0)
        if coord in occupied:
            continue
        occupied.add(coord)

        gx = zx * zone_w + (zone_w // 2)
        gy = zy * zone_h + (zone_h // 2)
        if gx < 0 or gy < 0 or gx >= len(xgrid) or gy >= len(ygrid):
            continue
        jx = float(xgrid[gx])
        jy = float(ygrid[gy])

        # Anchor "range": sigma is the Gaussian falloff radius in Julia-plane units.
        sigma = float(rng.uniform(0.05, 0.2))
        strength = 1.0
        anchors.append((jx, jy, sigma, strength))

        pid = f"rune_anchor_{len(anchors) - 1:03d}"
        try:
            poi_content.POIS[pid] = poi_content.POI(
                id=pid,
                coord=coord,
                npcs=[],
                structures=[{"kind": "rune_anchor"}],
            )
        except Exception:
            pass

    game.corruption_anchors = anchors
    if getattr(game, "overmap_params", None):
        try:
            game.overmap_params["corruption_anchors"] = list(game.corruption_anchors)
        except Exception:
            pass


def start_world_map_thread(
    game: "Game",
    *,
    reason: str = "loading",
    width: Optional[int] = None,
    height: Optional[int] = None,
    span: Optional[int] = None,
    view: Optional[Tuple[float, float, float, float]] = None,
    view_token: Optional[int] = None,
    corruption_version: Optional[int] = None,
) -> None:
    """Start a background thread to render the world map.

    If a render is already in progress, queues the request to run after
    the current render completes.
    """
    game._debug(f"[_start_world_map_thread] called with reason={reason} view_token={view_token}")
    reason = str(reason or "loading")

    if width is not None:
        game.world_map_render_width = int(width)
    if height is not None:
        game.world_map_render_height = int(height)
    if span is not None:
        game.world_map_render_span = int(span)
    if view is not None:
        game.world_map_view = (float(view[0]), float(view[1]), float(view[2]), float(view[3]))
    if view_token is not None:
        game.world_map_view_token = int(view_token)

    if corruption_version is not None:
        corr_ver = corruption_version
    else:
        corr_ver = int(getattr(game, "corruption_version", 0) or 0)

    req = {
        "reason": reason,
        "width": int(game.world_map_render_width),
        "height": int(game.world_map_render_height),
        "span": int(game.world_map_render_span),
        "view": game.world_map_view,
        "view_token": int(game.world_map_view_token),
        "corruption_version": corr_ver,
    }
    game._debug(f"[_start_world_map_thread] Request dictionary: {req}")

    # Ensure dict cache exists (the scene reads this)
    if not hasattr(game, "world_map_cache_dict"):
        game.world_map_cache_dict = {}

    key = (req["width"], req["height"], req["span"], req["view_token"], req["corruption_version"])

    # If we already have the exact requested render, do nothing.
    try:
        if not getattr(game, "world_map_rendering", False) and key in game.world_map_cache_dict:
            game._debug(f"[_start_world_map_thread] Render cache hit for key={key}. Skipping render.")
            return
    except Exception:
        pass

    if getattr(game, "world_map_rendering", False):
        game._debug(f"[_start_world_map_thread] World map is already rendering. Queueing request for view_token={req['view_token']}.")
        # Queue only the most recent request; older ones are irrelevant.
        game._world_map_pending_request = {
            "reason": req["reason"],
            "width": req["width"],
            "height": req["height"],
            "span": req["span"],
            "view": req["view"],
            "view_token": req["view_token"],
            "corruption_version": req["corruption_version"],
        }
        return

    game._debug(f"[_start_world_map_thread] No render in progress. Starting new render for view_token={req['view_token']}.")
    game._world_map_pending_request = None
    game.world_map_thread_started = True
    game.world_map_version += 1
    version = game.world_map_version
    game.world_map_render_reason = reason
    game.world_map_rendering = True
    try:
        v = req.get("view")
        game._debug(
            "[world_map] thread start "
            f"version={version} reason={game.world_map_render_reason} "
            f"size={req['width']}x{req['height']} span={req['span']} "
            f"view_token={req['view_token']} corr_ver={req['corruption_version']} "
            f"view={v!r}"
        )
    except Exception:
        pass
    t = threading.Thread(target=_background_render_map, args=(game, version, req), daemon=True)
    t.start()


def _background_render_map(game: "Game", version: int, req: dict) -> None:
    """Render overmap in a background thread using the requested view window.

    Drop-in replacement that stores results into game.world_map_cache_dict[key]
    (the dict cache that WorldMapScene.run() now queries), while keeping the
    legacy game.world_map_cache populated for backwards compatibility.
    """
    game._debug(f"[_background_render_map] Thread started for version: {version}. Request: {req}")
    t0 = time.perf_counter()
    try:
        from edgecaster.scenes.world_map_scene import WorldMapScene

        # Ensure dict cache exists (scene reads this)
        if not hasattr(game, "world_map_cache_dict"):
            game.world_map_cache_dict = {}

        span = int(req.get("span", 16) or 16)
        w = int(req.get("width", 0) or 0)
        h = int(req.get("height", 0) or 0)
        view = req.get("view")
        view_token = int(req.get("view_token", 0) or 0)
        corr_ver = int(req.get("corruption_version", 0) or 0)

        wm = WorldMapScene(game, span=span)

        class Stub:
            def __init__(self, ww: int, hh: int) -> None:
                self.width = ww
                self.height = hh

        stub = Stub(w, h)

        try:
            game._debug(f"[_background_render_map] Starting overmap render for version={version}, view_token={view_token}.")
            surf, out_view, surf_corr = wm._render_overmap(stub, view=view)
            game._debug(f"[_background_render_map] Finished overmap render for version={version}, view_token={view_token}.")
        except Exception as e:
            try:
                game._debug(f"[world_map] thread error version={version}: {e!r}")
                game._debug(traceback.format_exc())
            except Exception:
                pass
            return

        game._debug(f"[_background_render_map] Checking for staleness. Render version={version}, game version={getattr(game, 'world_map_version', version)}. Render token={view_token}, game token={getattr(game, 'world_map_view_token', view_token)}")
        # Only publish if this render is still current.
        if version != getattr(game, "world_map_version", version):
            game._debug(f"[_background_render_map] Stale render (version {version}), a newer one ({getattr(game, 'world_map_version', version)}) was started. Discarding.")
            return

        # Extra safety: if the game's current token has moved on, discard.
        try:
            cur_token = int(getattr(game, "world_map_view_token", view_token) or view_token)
            if cur_token != view_token:
                game._debug(f"[_background_render_map] Stale render (view_token {view_token}), a newer one ({cur_token}) was requested. Discarding.")
                return
        except Exception as e:
            game._debug(f"[_background_render_map] Error checking view_token: {e}")
            pass

        game._debug(f"[_background_render_map] Render is not stale. Publishing result for view_token={view_token}.")
        # Cache by (quantized) view window rather than a monotonically increasing token.
        try:
            if isinstance(out_view, dict):
                x_min = float(out_view.get('x_min', 0.0))
                x_max = float(out_view.get('x_max', x_min + 1.0))
                y_min = float(out_view.get('y_min', 0.0))
                y_max = float(out_view.get('y_max', y_min + 1.0))
                vw = max(1.0, x_max - x_min)
                vh = max(1.0, y_max - y_min)
            elif isinstance(out_view, (tuple, list)) and len(out_view) >= 4:
                x_min = float(out_view[0])
                y_min = float(out_view[1])
                vw = max(1.0, float(out_view[2]))
                vh = max(1.0, float(out_view[3]))
                x_max = x_min + vw
                y_max = y_min + vh
            else:
                x_min = y_min = 0.0
                vw = vh = 1.0
                x_max = x_min + vw
                y_max = y_min + vh
        except Exception:
            x_min = y_min = 0.0
            vw = vh = 1.0
            x_max = x_min + vw
            y_max = y_min + vh

        q = max(1.0, min(64.0, max(vw, vh) / 512.0))
        q_inv = 1.0 / q
        qx = int(round(x_min * q_inv))
        qy = int(round(y_min * q_inv))
        qw = int(round(vw * q_inv))
        qh = int(round(vh * q_inv))
        qk = int(round(q * 1000.0))
        key = (w, h, span, qx, qy, qw, qh, qk, corr_ver)

        payload = {
            "surface": surf,
            "surface_corr": surf_corr,
            "view": out_view,
            "key": key,
        }

        # New dict cache (what the scene uses)
        game.world_map_cache_dict[key] = payload
        # Also cache by the request's size_key (what WorldMapScene uses),
        try:
            size_key = (w, h, span, view_token, corr_ver)
            game.world_map_cache_dict[size_key] = payload
        except Exception:
            pass
        # Simple bound on cache size to avoid unbounded growth during rapid zoom/pan.
        max_cache = int(getattr(game.cfg, 'world_map_cache_max', 32) or 32)
        max_cache = max(8, min(256, max_cache))
        while len(game.world_map_cache_dict) > max_cache:
            try:
                game.world_map_cache_dict.pop(next(iter(game.world_map_cache_dict)))
            except Exception:
                break

        # Legacy single-slot cache (keep for any older code paths)
        game.world_map_cache = payload

        game.world_map_ready = True

        try:
            dt = time.perf_counter() - t0
            game._debug(
                f"[world_map] thread done version={version} dt={dt:.2f}s "
                f"key={key} view={out_view!r}"
            )
        except Exception:
            pass

    finally:
        # Only the latest thread should flip the rendering flag.
        if version == getattr(game, "world_map_version", version):
            game.world_map_rendering = False
            game.world_map_render_reason = "ready"
            game._debug(f"[_background_render_map] Render version {version} finished. world_map_rendering is now False.")

            pending = getattr(game, "_world_map_pending_request", None)
            if isinstance(pending, dict) and pending:
                game._debug(f"[_background_render_map] Found pending request. Starting new render thread for view_token={pending.get('view_token')}.")
                # Clear first so a failure doesn't loop forever.
                game._world_map_pending_request = None

                # Make sure queued requests carry corruption_version (older callers might not).
                if "corruption_version" not in pending:
                    try:
                        pending["corruption_version"] = int(getattr(game, "corruption_version", 0) or 0)
                    except Exception:
                        pending["corruption_version"] = 0

                try:
                    start_world_map_thread(game, **pending)
                except Exception as e:
                    game._debug(f"[_background_render_map] Error starting new render thread: {e!r}")
                    try:
                        game._debug(traceback.format_exc())
                    except Exception:
                        pass
            else:
                game._debug(f"[_background_render_map] No pending requests.")


def ensure_overmap_ready(game: "Game") -> None:
    """Ensure overmap params/grid exist.

    Overmap *rendering* is started lazily by WorldMapScene (or by corruption changes),
    so this should stay inexpensive and safe to call during startup.
    """
    if getattr(game, "overmap_params", None) and getattr(game, "tile_julia_grid", None):
        return
    # initialize params/grid
    init_overmap_params_and_grid(game)


def jx_jy_slices_for_zone(
    game: "Game",
    coord: Tuple[int, int, int],
) -> Tuple[Optional[List[float]], Optional[List[float]]]:
    """Return (jx_slice, jy_slice) for this zone coord using the global tile_julia_grid.

    This is how local zone generation gets its Julia coordinates - it queries
    the overmap's tile_julia_grid to find out what region of the Julia plane
    this zone corresponds to.
    """
    if getattr(game, "tile_julia_grid", None) is None:
        return None, None
    zx, zy, depth = coord
    if depth != 0:
        return None, None
    w = game.cfg.world_width
    h = game.cfg.world_height
    gx0 = zx * w
    gx1 = gx0 + w
    gy0 = zy * h
    gy1 = gy0 + h
    xgrid = game.tile_julia_grid.get("x", [])  # type: ignore[union-attr]
    ygrid = game.tile_julia_grid.get("y", [])  # type: ignore[union-attr]
    if gx0 < 0 or gy0 < 0 or gx1 > len(xgrid) or gy1 > len(ygrid):
        return None, None
    return xgrid[gx0:gx1], ygrid[gy0:gy1]


def set_corruption_level(game: "Game", level: float) -> None:
    """Set global corruption intensity (phase 1: visuals-only morphing)."""
    level = max(0.0, float(level))
    if abs(level - getattr(game, "corruption_level", 0.0)) < 1e-9:
        return
    game.corruption_level = level
    game.corruption_version += 1
    refresh_corruption_visuals(game)


def set_corruption_spline_weight(game: "Game", weight: float) -> None:
    """Set weight for the optional spline-based distortion field (0 disables)."""
    weight = max(0.0, float(weight))
    if abs(weight - getattr(game, "corruption_spline_weight", 0.0)) < 1e-9:
        return
    game.corruption_spline_weight = weight
    game.corruption_version += 1
    refresh_corruption_visuals(game)


def add_corruption_hotspot(game: "Game", jx: float, jy: float, strength: float, sigma: float) -> None:
    """Add a localized corruption 'cone' (Gaussian bump) in Julia-plane coordinates."""
    game.corruption_hotspots.append((float(jx), float(jy), float(strength), float(sigma)))
    game.corruption_version += 1
    refresh_corruption_visuals(game)


def alloc_rune_anchor_poi_id(game: "Game") -> str:
    """Return a unique POI id for a newly-created rune anchor."""
    from edgecaster.content import pois as poi_content

    i = 0
    while True:
        pid = f"rune_anchor_{i:03d}"
        if pid not in poi_content.POIS:
            return pid
        i += 1


def add_corruption_anchor(
    game: "Game",
    jx: float,
    jy: float,
    *,
    sigma: float,
    strength: float = 1.0,
    coord: Optional[Tuple[int, int, int]] = None,
    spawn_pos: Optional[Tuple[int, int]] = None,
) -> Optional[str]:
    """Add a rune anchor (corruption suppressor) and optionally create a POI marker.

    Args:
        jx, jy: Julia-plane coordinates of the anchor center (z-plane units).
        sigma: Gaussian falloff radius in Julia-plane units (controls range).
        strength: suppression strength in [0..1].
        coord: if provided, inject a POI at this zone coord so the anchor is discoverable on the world map.
        spawn_pos: optional tile position to spawn the visible anchor entity (if the zone already exists).
    """
    from edgecaster.content import pois as poi_content

    sigma = max(1e-6, float(sigma))
    strength = max(0.0, min(1.0, float(strength)))
    if strength <= 0.0:
        return None

    game.corruption_anchors.append((float(jx), float(jy), float(sigma), float(strength)))
    game.corruption_version += 1

    pid: Optional[str] = None
    if coord is not None:
        pid = alloc_rune_anchor_poi_id(game)
        try:
            poi_content.POIS[pid] = poi_content.POI(
                id=pid,
                coord=tuple(coord),
                npcs=[],
                structures=[{"kind": "rune_anchor"}],
            )
        except Exception:
            pid = None

        # Keep the world-map marker list in sync with dynamic POIs.
        if pid is not None:
            try:
                if getattr(game, "poi_locations", None) is None:
                    game.poi_locations = {}
                game.poi_locations[pid] = tuple(coord)
            except Exception:
                pass

        # If the zone already exists, ensure it knows about the POI and spawn the visible entity.
        try:
            lvl = game.levels.get(tuple(coord)) if hasattr(game, "levels") else None
        except Exception:
            lvl = None
        if lvl is not None:
            try:
                poi_ids = getattr(lvl.world, "poi_ids", None)
                if poi_ids is None:
                    setattr(lvl.world, "poi_ids", [pid] if pid else [])
                elif pid and isinstance(poi_ids, list) and pid not in poi_ids:
                    poi_ids.append(pid)
            except Exception:
                pass

            if spawn_pos is not None:
                try:
                    ent = game._spawn_entity_from_template("rune_anchor", spawn_pos)
                    lvl.entities[ent.id] = ent
                except Exception:
                    pass

    # Apply immediately to visited zones + overmap.
    refresh_corruption_visuals(game)
    return pid


def refresh_corruption_visuals(game: "Game") -> None:
    """Refresh already-instantiated overworld visuals and kick off overmap rerender.

    This updates the corruption params in overmap_params and refreshes all
    loaded surface-level zones to reflect new corruption settings.
    """
    from edgecaster import mapgen

    if getattr(game, "overmap_params", None):
        game.overmap_params["corruption_level"] = float(game.corruption_level)
        game.overmap_params["corruption_seed"] = int(game.corruption_seed)
        game.overmap_params["corruption_hotspots"] = list(getattr(game, "corruption_hotspots", []) or [])
        game.overmap_params["corruption_anchors"] = list(getattr(game, "corruption_anchors", []) or [])
        game.overmap_params["corruption_spline_weight"] = float(getattr(game, "corruption_spline_weight", 0.0) or 0.0)

    for coord, lvl in list(getattr(game, "levels", {}).items()):
        if coord[2] != 0:
            continue
        jx_slice, jy_slice = jx_jy_slices_for_zone(game, coord)
        if jx_slice is None or jy_slice is None:
            continue
        try:
            mapgen.refresh_fractal_overworld_visuals(
                lvl.world,
                coord,
                overmap_params=game.overmap_params,
                jx_slice=jx_slice,
                jy_slice=jy_slice,
            )
        except Exception:
            continue

    # Force overmap re-render to reflect new corruption.
    game.world_map_ready = False
    start_world_map_thread(game, reason="corruption")
