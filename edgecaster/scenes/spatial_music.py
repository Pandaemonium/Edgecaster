# edgecaster/systems/spatial_music.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence, Tuple

from edgecaster.scenes.audio_manager import MusicRequest
from edgecaster.state.pois import ABSRect as POIABSRect
from edgecaster.content.pois import get_poi_registry
from edgecaster.systems import spatial_index as spatial_index_system

ABSRect = Tuple[float, float, float, float]


def _norm_rect(r: ABSRect) -> ABSRect:
    x0, y0, x1, y1 = map(float, r)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _shrink_rect(r: ABSRect, margin: float) -> ABSRect:
    x0, y0, x1, y1 = _norm_rect(r)
    m = float(margin)
    return (x0 + m, y0 + m, x1 - m, y1 - m)


def _point_in_rect(p: Tuple[float, float], r: ABSRect) -> bool:
    x, y = map(float, p)
    x0, y0, x1, y1 = _norm_rect(r)
    return (x0 <= x <= x1) and (y0 <= y <= y1)


def _footprint_to_rect(fp: Any) -> Optional[ABSRect]:
    if fp is None:
        return None
    if isinstance(fp, dict):
        try:
            return (float(fp["x0"]), float(fp["y0"]), float(fp["x1"]), float(fp["y1"]))
        except Exception:
            return None
    for a0, a1, b0, b1 in (("x0", "y0", "x1", "y1"), ("ax0", "ay0", "ax1", "ay1")):
        try:
            return (float(getattr(fp, a0)), float(getattr(fp, a1)), float(getattr(fp, b0)), float(getattr(fp, b1)))
        except Exception:
            continue
    return None


def _iter_structure_kinds(poi_spec: Any) -> Iterable[str]:
    """Yield structure kind strings from a POI spec (robust to multiple formats)."""
    specs = getattr(poi_spec, "structure_specs", None)
    if not specs:
        return
    for s in specs or []:
        try:
            if isinstance(s, dict):
                k = s.get("kind")
            else:
                k = getattr(s, "kind", None)
            if k:
                yield str(k)
        except Exception:
            continue


@dataclass
class SpatialMusicConfig:
    # Leviathan loop (context override while it “dominates the frame”)
    leviathan_tag: str = "leviathan"
    leviathan_music_key: str = "baal_cycle"

    # Require the Leviathan's anchor point to be within the *inner* rect.
    # (Keeps it from triggering when it's barely grazing the edge.)
    leviathan_inner_margin: float = 0.0

    # “Dominates the frame” heuristic:
    # require estimated abs-size >= this fraction of the camera's smaller dimension.
    leviathan_min_screen_frac: float = 0.30

    leviathan_fade_out_ms: int = 250
    leviathan_fade_in_ms: int = 250

    # Colosseum fanfare on entry (interrupt then resume desired context)
    colosseum_music_key: str = "morituri"
    colosseum_fade_out_ms: int = 250
    colosseum_fade_in_ms: int = 0

    # What counts as “the colosseum”:
    # Prefer structural evidence over text matching (more yoga, less brittle).
    colosseum_struct_kinds: Tuple[str, ...] = ("colosseum_arena",)

    # Fallback keyword match against poi_id / poi.kind / tags.kind when structures are absent
    colosseum_kind_keywords: Tuple[str, ...] = ("colosseum", "arena")


class SpatialMusicDirector:
    """Context-driven music triggers based on observation (ABS + camera)."""

    def __init__(self, cfg: SpatialMusicConfig | None = None) -> None:
        self.cfg = cfg or SpatialMusicConfig()

        # Cache to avoid work when nothing moved
        self._last_player_abs: Tuple[float, float] | None = None
        self._last_cam_rect: ABSRect | None = None

        # Trigger state
        self._leviathan_active: bool = False
        self._in_colosseum: bool = False

    def update(self, manager: Any, renderer: Any) -> None:
        game = getattr(manager, "current_game", None)
        if game is None:
            return
        if not hasattr(renderer, "get_camera_abs_rect_and_lod"):
            return

        cam_rect, _cam_lod = renderer.get_camera_abs_rect_and_lod(game)
        cam_rect = _norm_rect(tuple(cam_rect))

        try:
            player_abs = tuple(map(float, game._get_player_abs()))
        except Exception:
            return

        # Early-out if nothing moved
        if self._last_player_abs == player_abs and self._last_cam_rect == cam_rect:
            return
        self._last_player_abs = player_abs
        self._last_cam_rect = cam_rect

        # Update continuous loop state
        self._update_leviathan(manager, game, cam_rect)

        # Avoid spamming interrupts while an interrupt is active
        try:
            if getattr(manager.audio, "is_interrupting") and manager.audio.is_interrupting():
                return
        except Exception:
            pass

        # Update rising-edge triggers
        self._update_colosseum_entry(manager, game, player_abs)

    # ------------------------------ Leviathan: in-frame loop ------------------------------

    def _update_leviathan(self, manager: Any, game: Any, cam_rect: ABSRect) -> None:
        inner = _shrink_rect(cam_rect, self.cfg.leviathan_inner_margin)

        found = self._find_tagged_world_entity(game, inner, tag=self.cfg.leviathan_tag)

        active = False
        if found is not None:
            _ax, _ay, est_abs_size = found
            try:
                cam_w = float(cam_rect[2]) - float(cam_rect[0])
                cam_h = float(cam_rect[3]) - float(cam_rect[1])
                denom = max(1.0, min(abs(cam_w), abs(cam_h)))
                frac = float(est_abs_size) / denom if est_abs_size and est_abs_size > 0 else 0.0
                active = frac >= float(self.cfg.leviathan_min_screen_frac)
            except Exception:
                # If size math fails, fall back to “in inner rect means active”
                active = True

        if active == self._leviathan_active:
            return
        self._leviathan_active = active

        if active:
            manager.set_context_music_override(
                MusicRequest(
                    key=self.cfg.leviathan_music_key,
                    playlist=None,
                    loop=True,
                    hard_cut=False,
                    fade_out_ms=int(self.cfg.leviathan_fade_out_ms),
                    fade_in_ms=int(self.cfg.leviathan_fade_in_ms),
                )
            )
        else:
            manager.set_context_music_override(None)

    def _estimate_entity_abs_size(self, ent: Any) -> float:
        """Best-effort estimate of how 'big' an entity is in ABS units."""
        # Direct attributes (proxies often have base_size)
        for attr in ("abs_size", "base_size", "size", "radius", "w", "h", "width", "height"):
            try:
                v = getattr(ent, attr, None)
                if v is None:
                    continue
                fv = float(v)
                if fv > 0:
                    # width/height are extents; radius is already a scalar-ish size; OK either way.
                    return fv
            except Exception:
                pass

        # Tags (we intentionally support a few synonyms)
        try:
            tags = getattr(ent, "tags", None) or {}
            for k in ("abs_size", "base_size", "size", "radius", "abs_radius", "diameter"):
                if k in tags:
                    fv = float(tags[k])
                    if fv > 0:
                        return fv
        except Exception:
            pass

        return 1.0

    def _find_tagged_world_entity(self, game: Any, rect: ABSRect, *, tag: str) -> Optional[Tuple[float, float, float]]:
        """Return (abs_x, abs_y, est_abs_size) for the first matching entity in rect."""
        ax0, ay0, ax1, ay1 = _norm_rect(rect)
        tag = str(tag)
        zz = int(getattr(getattr(game, "zone_coord", (0, 0, 0)), 2, 0) if not isinstance(getattr(game, "zone_coord", None), tuple) else game.zone_coord[2])

        try:
            for entry in spatial_index_system.query_game_spatial_rect(game, (ax0, ay0, ax1, ay1), zz=zz):
                ent = entry.obj
                tags = getattr(ent, "tags", None) or {}
                if not bool(tags.get(tag, False)):
                    continue
                ax, ay = spatial_index_system.entry_anchor(entry)
                if not _point_in_rect((ax, ay), (ax0, ay0, ax1, ay1)):
                    continue
                est = self._estimate_entity_abs_size(ent)
                return (float(ax), float(ay), float(est))
        except Exception:
            pass

        return None

    # ------------------------------ Colosseum: entry fanfare ------------------------------

    def _update_colosseum_entry(self, manager: Any, game: Any, player_abs: Tuple[float, float]) -> None:
        in_col = self._player_in_colosseum(game, player_abs)

        # Rising edge triggers
        if in_col and not self._in_colosseum:
            self._fire_colosseum_fanfare(manager)

        self._in_colosseum = in_col

    def _fire_colosseum_fanfare(self, manager: Any) -> None:
        req = MusicRequest(
            key=self.cfg.colosseum_music_key,
            playlist=None,
            loop=False,
            hard_cut=False,
            fade_out_ms=int(self.cfg.colosseum_fade_out_ms),
            fade_in_ms=int(self.cfg.colosseum_fade_in_ms),
        )

        resume_to = None
        try:
            # Includes context overrides like Leviathan if active.
            resume_to = manager.desired_music_request()
        except Exception:
            try:
                resume_to = manager.current_music_request()
            except Exception:
                resume_to = None

        manager.audio.interrupt_then_resume(req, resume_to=resume_to)

    def _player_in_colosseum(self, game: Any, player_abs: Tuple[float, float]) -> bool:
        px, py = map(float, player_abs)

        # Best guess of current depth (but we will also try depth=0)
        try:
            zc = getattr(game, "zone_coord", (0, 0, 0))
            zz = int(zc[2]) if isinstance(zc, tuple) else int(getattr(zc, 2, 0))
        except Exception:
            zz = 0

        struct_targets = set(s.lower() for s in (self.cfg.colosseum_struct_kinds or ()))
        kw = tuple(s.lower() for s in (self.cfg.colosseum_kind_keywords or ()))

        def _matches(poi_spec: Any) -> bool:
            # Primary: structure evidence (preferred)
            if struct_targets:
                for sk in _iter_structure_kinds(poi_spec):
                    if str(sk).lower() in struct_targets:
                        return True

            # Fallback: text match
            try:
                poi_id = str(getattr(poi_spec, "id", "") or "").lower()
            except Exception:
                poi_id = ""
            try:
                kind_txt = str(getattr(poi_spec, "kind", "") or "").lower()
            except Exception:
                kind_txt = ""
            tags = getattr(poi_spec, "tags", None) or {}
            tag_kind = str(tags.get("kind", "") or "").lower()
            hay = " ".join([poi_id, kind_txt, tag_kind])

            return bool(kw) and any(k in hay for k in kw)

        # Track D: POIRegistry mirrors POI specs into SpatialIndex. Prefer that
        # shared geometry surface; the registry-specific path below is a
        # compatibility fallback while POIRegistry still owns content state.
        for depth in (zz, 0):
            try:
                entries = spatial_index_system.query_game_spatial_rect(
                    game,
                    (px, py, px + 1.0, py + 1.0),
                    zz=depth,
                    source="poi_registry",
                )
            except Exception:
                entries = []
            for entry in entries:
                poi_spec = entry.obj
                try:
                    fp = _footprint_to_rect(getattr(poi_spec, "footprint", None))
                    if fp is None:
                        fp = entry.rect
                    if _point_in_rect((px, py), _norm_rect(fp)) and _matches(poi_spec):
                        return True
                except Exception:
                    continue

        poi_reg = getattr(game, "poi_registry", None)
        if poi_reg is None:
            return False

        # 1) Fast exact point query (preferred)
        for depth in (zz, 0):
            try:
                hits = list(poi_reg.get_at_abs_point(int(px), int(py), depth=depth) or [])
            except Exception:
                hits = []
            for poi_spec in hits:
                try:
                    fp = _footprint_to_rect(getattr(poi_spec, "footprint", None))
                    if fp is None:
                        continue
                    if _point_in_rect((px, py), _norm_rect(fp)) and _matches(poi_spec):
                        return True
                except Exception:
                    continue

        # 2) Small rect query fallback (correct ABSRect type)
        q = POIABSRect(x0=px - 1.0, y0=py - 1.0, x1=px + 1.0, y1=py + 1.0)
        for depth in (zz, 0):
            try:
                hits = list(poi_reg.get_in_abs_rect(q, depth=depth) or [])
            except Exception:
                hits = []
            for poi_spec in hits:
                try:
                    fp = _footprint_to_rect(getattr(poi_spec, "footprint", None))
                    if fp is None:
                        continue
                    if _point_in_rect((px, py), _norm_rect(fp)) and _matches(poi_spec):
                        return True
                except Exception:
                    continue

        # Optional debug probe
        if bool(getattr(game, "debug_spatial_music", False)):
            try:
                # Show nearby POIs by zone bucket, both depths
                zx = int(px) // int(getattr(poi_reg, "zone_w", 60) or 60)
                zy = int(py) // int(getattr(poi_reg, "zone_h", 40) or 40)
                keys = []
                for depth in (zz, 0):
                    try:
                        bucket = getattr(poi_reg, "_by_zone", {}).get((zx, zy, depth), [])
                    except Exception:
                        bucket = []
                    keys.append((depth, len(bucket)))
                print(f"[spatial_music] colosseum probe player=({int(px)},{int(py)}) zone=({zx},{zy}) buckets={keys}")
            except Exception:
                pass

        return False
