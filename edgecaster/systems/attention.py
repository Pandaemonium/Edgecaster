from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Dict, List, Tuple

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.content.pois import get_poi_registry
from edgecaster.content import npcs
from edgecaster.enemies import factory as enemy_factory
from edgecaster.systems import aggregate_resolution as aggregate_system
from edgecaster.systems.sites import load_site_types
from edgecaster.systems.world_entity_index import WorldEntityIndex
from edgecaster.state.actors import Human, Stats

@dataclass
class _YogaStagedEntity:
    """Minimal entity-like object for attention-staged renderables (e.g., structure walls).

    We use this when we don't yet have a full prototype/spec for an entity, but we still
    want the renderer + LoD system to treat it as a real object. It intentionally mirrors
    the small surface area that renderables_in_abs_rect() relies on: id, pos, abs_pos,
    kind, tags, glyph, color/base_size.
    """
    id: str
    pos: Tuple[int, int]
    abs_pos: Tuple[int, int]
    kind: str = "structure"
    glyph: str = "#"
    color: Tuple[int, int, int] = (140, 120, 100)
    base_size: float = 1.0
    tags: Dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.tags.get("name", self.id)


class AttentionCellStore:
    """Unified cache of instantiated entities keyed by ABS-space bins (not zones).

    This is a Phase-1.8 stepping stone toward a full quadtree-backed attention field.
    For now it's a simple ABS bin grid so we can:
      - stage/unstage derived entities anywhere (god-vision), without creating zones
      - keep derived entities "real" objects that can be rendered and later interacted with
    """

    def __init__(self, *, bin_size: int = 32) -> None:
        self.bin_size = max(1, int(bin_size))
        self.entities: Dict[str, object] = {}
        self.eid_to_bin: Dict[str, Tuple[int, int, int]] = {}
        self.bins: Dict[Tuple[int, int, int], List[str]] = {}
        self.lineage_to_eid: Dict[str, str] = {}

    def _bin_for_abs(self, ax: float, ay: float, zz: int) -> Tuple[int, int, int]:
        bs = self.bin_size
        return (int(math.floor(float(ax) / bs)), int(math.floor(float(ay) / bs)), int(zz))

    def stage(self, obj: object, *, abs_x: float, abs_y: float, zz: int, lineage_id: str | None = None) -> str:
        eid = str(getattr(obj, "id", "") or "")
        if not eid:
            raise ValueError("staged object missing id")

        if lineage_id:
            prev = self.lineage_to_eid.get(lineage_id)
            if prev and prev != eid:
                # Prefer stable lineage mapping; remove old eid if present.
                self.despawn(prev)
            self.lineage_to_eid[lineage_id] = eid

        b = self._bin_for_abs(abs_x, abs_y, zz)

        prevb = self.eid_to_bin.get(eid)
        if prevb and prevb != b:
            # Move bins
            try:
                lst = self.bins.get(prevb)
                if lst and eid in lst:
                    lst.remove(eid)
            except Exception:
                pass

        self.entities[eid] = obj
        self.eid_to_bin[eid] = b
        self.bins.setdefault(b, []).append(eid)
        return eid

    def despawn(self, eid: str) -> None:
        eid = str(eid)
        b = self.eid_to_bin.pop(eid, None)
        if b is not None:
            try:
                lst = self.bins.get(b)
                if lst and eid in lst:
                    lst.remove(eid)
            except Exception:
                pass
        self.entities.pop(eid, None)

        # Remove from lineage mapping if present
        try:
            for k, v in list(self.lineage_to_eid.items()):
                if v == eid:
                    del self.lineage_to_eid[k]
        except Exception:
            pass

    def query_abs_rect(self, abs_rect: Tuple[float, float, float, float], *, zz: int) -> List[Tuple[object, float, float]]:
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
        if ax1 < ax0:
            ax0, ax1 = ax1, ax0
        if ay1 < ay0:
            ay0, ay1 = ay1, ay0
        if ax1 == ax0 or ay1 == ay0:
            return []

        bs = self.bin_size
        bx0 = int(math.floor(ax0 / bs))
        by0 = int(math.floor(ay0 / bs))
        bx1 = int(math.floor((ax1 - 1e-6) / bs))
        by1 = int(math.floor((ay1 - 1e-6) / bs))

        out: List[Tuple[object, float, float]] = []
        for by in range(by0, by1 + 1):
            for bx in range(bx0, bx1 + 1):
                ids = self.bins.get((bx, by, int(zz)))
                if not ids:
                    continue
                for eid in ids:
                    obj = self.entities.get(eid)
                    if obj is None:
                        continue
                    ap = getattr(obj, "abs_pos", None)
                    if not ap:
                        continue
                    ax, ay = ap
                    ax = float(ax)
                    ay = float(ay)
                    if ax0 <= ax < ax1 and ay0 <= ay < ay1:
                        out.append((obj, ax, ay))
        return out

def sync_attention_instantiation(game, abs_rect: tuple[float, float, float, float], *, cam_lod: float) -> None:
    """
    Phase-1 LifecycleManager (Route 2): stage/unstage real entities based on attention.

    - May instantiate/de-instantiate entities as camera pans/zooms (even while paused).
    - Must NOT advance time or apply simulation deltas.
    - Deterministic layout for berry_patch -> berries (cluster mode).
    - Derived children are staged into game.attn_store (ABS-binned), not into zones.
    - Basic persistence (Phase 1): if a spawned berry disappears from the *loaded* zone (picked up),
      we record its slot as harvested on the aggregate so it won't respawn.
    """
    # Remember latest view (used as a safety net before time advances)
    try:
        game._last_view_abs_rect = tuple(map(float, abs_rect))
        game._last_view_cam_lod = float(cam_lod)
    except Exception:
        pass

    try:
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
    except Exception:
        return
    if ax1 < ax0:
        ax0, ax1 = ax1, ax0
    if ay1 < ay0:
        ay0, ay1 = ay1, ay0
    if ax1 == ax0 or ay1 == ay0:
        return

    cfg = getattr(game, "cfg", None)

    # Zone dimensions are still used as a spatial hash (NOT ontology).
    zone_w = int(getattr(cfg, "world_width", 0) or 0)
    zone_h = int(getattr(cfg, "world_height", 0) or 0)
    if zone_w <= 0 or zone_h <= 0:
        try:
            lvl0 = game._level()
            zone_w = int(getattr(lvl0.world, "width", 60) or 60)
            zone_h = int(getattr(lvl0.world, "height", 40) or 40)
        except Exception:
            zone_w = 60
            zone_h = 40

    # Current depth only in Phase 1
    _czx, _czy, zz = getattr(game, "zone_coord", (0, 0, 0))
    zz = int(zz)

    # Warm rect (reuse same idea as renderables_in_abs_rect)
    pad_tiles = float(getattr(cfg, "entity_render_pad_tiles", 0.0) or 0.0)
    if pad_tiles <= 0.0:
        pad_tiles = 0.5 * max((ax1 - ax0), (ay1 - ay0))

    wx0 = ax0 - pad_tiles
    wy0 = ay0 - pad_tiles
    wx1 = ax1 + pad_tiles
    wy1 = ay1 + pad_tiles

    # Camera center (ABS)
    ccx = (ax0 + ax1) * 0.5
    ccy = (ay0 + ay1) * 0.5

    # Union with player abs_pos ONLY if camera is reasonably close to the player.
    # (God-vision / remote panning must not create a giant "bridge" warm rect.)
    try:
        pax, pay = game._get_player_abs()
        dx = float(pax) - float(ccx)
        dy = float(pay) - float(ccy)
        near_thresh = 2.0 * float(pad_tiles)
        if (dx * dx + dy * dy) <= (near_thresh * near_thresh):
            wx0 = min(wx0, float(pax) - pad_tiles)
            wy0 = min(wy0, float(pay) - pad_tiles)
            wx1 = max(wx1, float(pax) + pad_tiles)
            wy1 = max(wy1, float(pay) + pad_tiles)
    except Exception:
        pass

    world_index = getattr(game, "world_entity_index", None)
    if world_index is None:
        return

    # Query macro entities in warm rect (clamped around camera so god-vision doesn't stage the universe)
    max_zone_span = int(getattr(cfg, "render_max_zone_span", 9) or 9)
    max_zone_span = max(1, int(max_zone_span))

    # Compute warm-rect zone window
    zx0 = int(math.floor(wx0 / float(zone_w)))
    zy0 = int(math.floor(wy0 / float(zone_h)))
    zx1 = int(math.floor((wx1 - 1e-9) / float(zone_w)))
    zy1 = int(math.floor((wy1 - 1e-9) / float(zone_h)))

    zx0c, zx1c, zy0c, zy1c, was_clamped = game._clamp_zone_window(
        zx0, zx1, zy0, zy1,
        zone_span_cap=max_zone_span,
        ccx=ccx, ccy=ccy,
        zone_w=zone_w, zone_h=zone_h,
    )

    # Ensure aggregate proxies exist at least for the clamped camera window.
    # (This avoids "only a few patches near player" when panning far away.)
    try:
        game._ensure_world_aggregate_entities(
            zone_w=zone_w,
            zone_h=zone_h,
            zx0=zx0c,
            zx1=zx1c,
            zy0=zy0c,
            zy1=zy1c,
            zz=zz,
            kinds=("berry_patch",),
        )
    except Exception:
        pass

    try:
        refs = world_index.query_abs_rect((wx0, wy0, wx1, wy1), z=zz, zone_span_cap=max_zone_span)
    except Exception:
        return

    partial_knowledge = bool(was_clamped)

    attn_store: AttentionCellStore = getattr(game, "attn_store", None)
    if attn_store is None:
        return

    # -----------------------------
    # A) BERRY PATCH -> BERRIES
    # -----------------------------
    in_scope_aggs: set[str] = set()

    for r in refs:
        ent = getattr(r, "ent", None)
        if ent is None:
            continue

        tags = getattr(ent, "tags", {}) or {}
        kind = str(tags.get("aggregate_kind", "") or getattr(ent, "kind", "") or "")
        if kind != "berry_patch":
            continue

        # Only refine when zoomed in enough
        thresh = float(tags.get("detail_lod_threshold", -1.25))
        if float(cam_lod) > thresh:
            continue

        zc = tuple(getattr(r, "zone_coord", (0, 0, zz)))
        if len(zc) != 3:
            continue

        agg_id = str(getattr(ent, "id", "") or "")
        if not agg_id:
            continue

        in_scope_aggs.add(agg_id)

        slot_to_eid = game._attn_active_agg_children.get(agg_id)
        if not isinstance(slot_to_eid, dict):
            slot_to_eid = {}
            game._attn_active_agg_children[agg_id] = slot_to_eid

        # Harvest persistence stored on the aggregate itself (truthy macro object)
        harvested = getattr(ent, "_agg_harvested_slots", None)
        if not isinstance(harvested, set):
            harvested = set()
            try:
                setattr(ent, "_agg_harvested_slots", harvested)
            except Exception:
                pass

        # If something removed a berry from a *loaded zone* (pickup), record its slot harvested.
        # (This keeps existing gameplay interactions working while zones still exist locally.)
        try:
            level = game.get_zone_for_render(zc)
        except Exception:
            level = None
        if level is not None:
            try:
                for s, eid in list(slot_to_eid.items()):
                    if eid not in level.entities and eid in attn_store.entities:
                        harvested.add(int(s))
                        attn_store.despawn(eid)
                        del slot_to_eid[s]
            except Exception:
                pass

        # Deterministic child layout (local coords)
        try:
            child_id, pts = aggregate_system.compute_cluster_children_layout(
                game,
                aggregate_ent=ent,
                zone_coord=zc,
                local_pos=tuple(getattr(r, "local_pos", (0, 0))),
                zone_w=zone_w,
                zone_h=zone_h,
            )
        except Exception:
            continue
        if not child_id or not pts:
            continue

        # Resolve child prototype once
        try:
            child_proto = prototypes.resolve_proto(str(child_id))
        except Exception:
            continue

        zx, zy, _z = map(int, zc)

        for slot, (lx, ly) in enumerate(pts):
            if slot in harvested:
                continue

            ax = int(zx * int(zone_w) + int(lx))
            ay = int(zy * int(zone_h) + int(ly))

            # Keep within warm rect (prevents weird edge-instantiation)
            if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                continue

            eid = f"{agg_id}:{child_id}:{slot}"
            if eid in attn_store.entities:
                slot_to_eid[slot] = eid
                # Mirror into loaded zone if present (enables pickup/look)
                if level is not None and eid not in level.entities:
                    try:
                        level.entities[eid] = attn_store.entities[eid]
                        level.spatial_dirty = True
                    except Exception:
                        pass
                continue

            # Spawn real entity (native local coords for its zone coord; ABS for rendering)
            try:
                bent = spawn_factory.build_entity_from_spec(
                    spec=child_proto,
                    eid=eid,
                    pos=(int(lx), int(ly)),
                    abs_pos=(int(ax), int(ay)),
                    overrides={
                        "tags": {
                            "from_aggregate": agg_id,
                            "aggregate_slot": int(slot),
                            "aggregate_kind": "berry_patch",
                        }
                    },
                )
            except Exception:
                continue

            # Stage into attention store (primary)
            try:
                attn_store.stage(bent, abs_x=ax, abs_y=ay, zz=zz, lineage_id=f"{agg_id}:{child_id}:{slot}")
            except Exception:
                continue

            # Mirror into loaded zone if present
            if level is not None:
                try:
                    level.entities[eid] = bent
                    level.spatial_dirty = True
                except Exception:
                    pass

            slot_to_eid[slot] = eid

    # Evict berries for aggregates no longer in scope.
    # IMPORTANT: only evict when our macro query is complete. If the query was clamped/capped,
    # eviction causes flicker (entities appear for a tick then vanish).
    if not partial_knowledge:
        try:
            for agg_id, slot_map in list(game._attn_active_agg_children.items()):
                if agg_id in in_scope_aggs:
                    continue
                if isinstance(slot_map, dict):
                    for _slot, eid in list(slot_map.items()):
                        try:
                            attn_store.despawn(eid)
                        except Exception:
                            pass
                del game._attn_active_agg_children[agg_id]
        except Exception:
            pass

    
    # -----------------------------
    # B) POI DETAILS (structures + NPC markers)
    #
    # Yoga invariant: POIs exist in ABS space independent of zone loading.
    # Therefore: do NOT rely on a POI's world-index marker being on-screen to
    # resolve its walls/NPCs. Query the POI registry directly by ABS overlap.
    # -----------------------------
    poi_reg = getattr(game, "poi_registry", None)
    if poi_reg is None:
        try:
            poi_reg = get_poi_registry(zone_w=zone_w, zone_h=zone_h)
        except Exception:
            poi_reg = None

    if poi_reg is not None:
        # Query POIs intersecting our warm rect. Prefer registry helper if present.
        try:
            poi_hits = poi_reg.get_in_abs_rect((wx0, wy0, wx1, wy1), z=zz)
        except Exception:
            try:
                poi_hits = poi_reg.get_in_abs_rect((wx0, wy0, wx1, wy1), depth=zz)
            except Exception:
                try:
                    poi_hits = poi_reg.get_in_abs_rect((wx0, wy0, wx1, wy1))
                except Exception:
                    poi_hits = []

        in_scope_pois: set[str] = set()

        # Track active staged children per POI (walls + NPC markers)
        if not hasattr(game, "_attn_active_poi_children"):
            game._attn_active_poi_children = {}  # poi_id -> set[eid]

        for poi_spec in (poi_hits or []):
            try:
                poi_id = str(getattr(poi_spec, "id", "") or "")
                if not poi_id:
                    continue
                in_scope_pois.add(poi_id)

                poi_tags = getattr(poi_spec, "tags", None) or {}
                # Only refine when zoomed in enough
                thresh = float(poi_tags.get("detail_lod_threshold", -0.75))
                if float(cam_lod) > thresh:
                    continue

                fp = getattr(poi_spec, "footprint", None)
                # Some POIs may not have a footprint (legacy); skip cleanly
                if fp is None:
                    continue

                active: set[str] = game._attn_active_poi_children.get(poi_id)
                if not isinstance(active, set):
                    active = set()
                    game._attn_active_poi_children[poi_id] = active
                desired: set[str] = set()

                # -----------------------------
                # B1) Colosseum / arena walls (structure markers)
                # -----------------------------
                poi_kind = str(getattr(poi_spec, "kind", "") or "")
                if "colosseum" in poi_kind.lower() or "arena" in poi_kind.lower():
                    arena_cx = (float(fp.x0) + float(fp.x1)) / 2.0
                    arena_cy = (float(fp.y0) + float(fp.y1)) / 2.0
                    arena_rx = max(1.0, (float(fp.x1) - float(fp.x0)) / 2.0)
                    arena_ry = max(1.0, (float(fp.y1) - float(fp.y0)) / 2.0)

                    wall_thickness = int(poi_tags.get("wall_thickness", 3))
                    wall_thickness = max(1, int(wall_thickness))

                    # Ring thickness in normalized ellipse-space
                    tnorm = float(wall_thickness) / max(arena_rx, arena_ry)
                    inner = max(0.0, 1.0 - 2.0 * tnorm)
                    outer = 1.0 + 0.25 * tnorm

                    # Clip to warm rect âˆ© footprint
                    ix0 = max(int(math.floor(wx0)), int(fp.x0))
                    iy0 = max(int(math.floor(wy0)), int(fp.y0))
                    ix1 = min(int(math.ceil(wx1)), int(fp.x1))
                    iy1 = min(int(math.ceil(wy1)), int(fp.y1))

                    for ax in range(ix0, ix1):
                        nx = ((float(ax) + 0.5) - arena_cx) / arena_rx
                        nx2 = nx * nx
                        if nx2 > outer:
                            continue

                        ny2_outer = outer - nx2
                        ny2_inner = inner - nx2
                        if ny2_outer <= 0.0:
                            continue

                        ny_outer = math.sqrt(max(0.0, ny2_outer))
                        ny_inner = math.sqrt(max(0.0, ny2_inner)) if ny2_inner > 0.0 else 0.0

                        y_outer = ny_outer * arena_ry
                        y_inner = ny_inner * arena_ry

                        top0 = int(math.floor(arena_cy + y_inner))
                        top1 = int(math.ceil(arena_cy + y_outer))
                        bot0 = int(math.floor(arena_cy - y_outer))
                        bot1 = int(math.ceil(arena_cy - y_inner))

                        top0 = max(top0, iy0)
                        top1 = min(top1, iy1)
                        bot0 = max(bot0, iy0)
                        bot1 = min(bot1, iy1)

                        for ay in range(top0, top1):
                            eid = f"poi:{poi_id}:wall:{ax}:{ay}"
                            desired.add(eid)
                            if eid not in attn_store.entities:
                                w = _YogaStagedEntity(
                                    id=eid,
                                    pos=(int(ax), int(ay)),
                                    abs_pos=(int(ax), int(ay)),
                                    kind="structure",
                                    glyph="#",
                                    color=(160, 160, 160),
                                    name="Wall",
                                    tags={"poi": True, "poi_id": poi_id, "structure": "wall"},
                                )
                                attn_store.stage(w, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)

                        for ay in range(bot0, bot1):
                            eid = f"poi:{poi_id}:wall:{ax}:{ay}"
                            desired.add(eid)
                            if eid not in attn_store.entities:
                                w = _YogaStagedEntity(
                                    id=eid,
                                    pos=(int(ax), int(ay)),
                                    abs_pos=(int(ax), int(ay)),
                                    kind="structure",
                                    glyph="#",
                                    color=(160, 160, 160),
                                    name="Wall",
                                    tags={"poi": True, "poi_id": poi_id, "structure": "wall"},
                                )
                                attn_store.stage(w, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)

                # -----------------------------
                # B2) POI NPCs (REAL actors, staged like berry children)
                # -----------------------------
                def _build_poi_actor(
                    *,
                    eid: str,
                    npc_id: str,
                    name: str,
                    glyph: str,
                    color,
                    abs_pos: tuple[int, int],
                    local_pos: tuple[int, int],
                    poi_id: str,
                    ns,
                ):
                    """Build a real Actor instance for a POI child.

                    Yoga rule: this is a *resolution* product (like berries), not a zone-stamp.
                    """
                    npc_def = npcs.NPC_DEFS.get(npc_id, {}) if "npcs" in globals() else {}

                    # Special cases (matching legacy POI spawning behavior)
                    if npc_id == "caged_demon":
                        try:
                            a = enemy_factory.spawn_enemy("caged_demon", local_pos, abs_pos=abs_pos)
                        except TypeError:
                            a = enemy_factory.spawn_enemy("caged_demon", local_pos)
                            try:
                                a.abs_pos = abs_pos
                            except Exception:
                                pass
                        a.id = eid
                        a.pos = local_pos
                        a.faction = "neutral"
                        a.actions = ()
                        a.ai = "idle"
                        a.tags = getattr(a, "tags", {}) or {}
                        a.tags.update({"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id})
                        a.tags["show_exact_hp"] = True
                        try:
                            a.show_exact_hp = True
                        except Exception:
                            pass
                        desc = getattr(ns, "description", None) or npc_def.get("description") or getattr(a, "description", None)
                        if desc:
                            a.description = desc
                        try:
                            a.regen_per_tick = (1, 10)
                            game._start_regen(game.get_zone_for_render((abs_pos[0] // zone_w, abs_pos[1] // zone_h, zz)) or game._level(), a.id, amount=1, interval=10)
                        except Exception:
                            pass
                        # Appearance overrides
                        a.name = name
                        try:
                            a.glyph = glyph
                            a.color = color  # type: ignore[assignment]
                        except Exception:
                            pass
                        return a

                    if npc_id == "merchant":
                        try:
                            a = enemy_factory.spawn_enemy("merchant", local_pos, abs_pos=abs_pos)
                        except TypeError:
                            a = enemy_factory.spawn_enemy("merchant", local_pos)
                            try:
                                a.abs_pos = abs_pos
                            except Exception:
                                pass
                        a.id = eid
                        a.pos = local_pos
                        a.faction = "npc"
                        a.actions = ()
                        a.ai = "idle"
                        a.tags = getattr(a, "tags", {}) or {}
                        a.tags.update({"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id})
                        a.tags["merchant_id"] = npc_def.get("merchant_id", "general_store")

                        # Appearance overrides
                        a.name = name
                        try:
                            a.glyph = glyph
                            a.color = color  # type: ignore[assignment]
                        except Exception:
                            pass
                        desc = getattr(ns, "description", None) or npc_def.get("description") or getattr(a, "description", None)
                        if desc:
                            a.description = desc

                        # Ensure merchant system initialized when possible
                        try:
                            from edgecaster.systems import trade as trade_system
                            lvl = game.get_zone_for_render((abs_pos[0] // zone_w, abs_pos[1] // zone_h, zz))
                            if lvl is not None:
                                trade_system.ensure_merchant_initialized(game, lvl, a)
                        except Exception:
                            pass
                        return a

                    # Default: Human NPC
                    try:
                        a = Human(
                            id=eid,
                            name=name,
                            pos=local_pos,
                            abs_pos=abs_pos,
                            faction="npc",
                            stats=Stats(hp=50, max_hp=50),
                            tags={"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id},
                            disposition=int(npc_def.get("base_disposition", 0) or 0),
                            affiliations=tuple(npc_def.get("factions", [])),
                            glyph=glyph,
                            color=color,  # type: ignore[arg-type]
                        )
                    except TypeError:
                        # In case Human signature differs in some branches
                        a = Human(
                            id=eid,
                            name=name,
                            pos=local_pos,
                            faction="npc",
                            stats=Stats(hp=50, max_hp=50),
                            tags={"poi": True, "poi_id": poi_id, "npc": True, "npc_id": npc_id},
                            disposition=int(npc_def.get("base_disposition", 0) or 0),
                            affiliations=tuple(npc_def.get("factions", [])),
                            glyph=glyph,
                            color=color,  # type: ignore[arg-type]
                        )
                        try:
                            a.abs_pos = abs_pos
                        except Exception:
                            pass

                    desc = getattr(ns, "description", None) or npc_def.get("description")
                    if desc:
                        a.description = desc
                    return a

                def _mirror_actor_into_loaded_zone(eid: str, actor_obj, abs_pos: tuple[int, int]) -> None:
                    """If the actor's zone is loaded, mirror it into level.actors/entities."""
                    try:
                        zc = (int(abs_pos[0]) // int(zone_w), int(abs_pos[1]) // int(zone_h), int(zz))
                        lvl = game.get_zone_for_render(zc)
                    except Exception:
                        lvl = None
                    if lvl is None:
                        return
                    try:
                        if eid not in lvl.entities:
                            lvl.entities[eid] = actor_obj
                            lvl.spatial_dirty = True
                        if eid not in lvl.actors:
                            lvl.actors[eid] = actor_obj
                            lvl.spatial_dirty = True
                    except Exception:
                        pass

                try:
                    npc_specs = getattr(poi_spec, "npc_specs", None) or []
                except Exception:
                    npc_specs = []

                for ns_i, ns in enumerate(npc_specs):
                    try:
                        npc_id = str(getattr(ns, "npc_id", "") or "")
                        if not npc_id:
                            continue

                        # Prefer explicit abs_positions (v2). If absent, fall back to legacy offsets.
                        abs_positions = list(getattr(ns, "abs_positions", []) or [])
                        if not abs_positions:
                            offsets = list(getattr(ns, "offsets", []) or [])
                            coord = getattr(poi_spec, "coord", None)
                            if offsets and coord is not None:
                                try:
                                    zx = int(coord[0]); zy = int(coord[1])
                                    base_x = zx * int(zone_w)
                                    base_y = zy * int(zone_h)
                                    abs_positions = [(base_x + int(ox), base_y + int(oy)) for (ox, oy) in offsets]
                                except Exception:
                                    abs_positions = []

                        if not abs_positions:
                            continue

                        npc_def = npcs.NPC_DEFS.get(npc_id, {}) if "npcs" in globals() else {}
                        glyph = getattr(ns, "glyph", None) or npc_def.get("glyph", "@")
                        color = getattr(ns, "color", None) or npc_def.get("color", (255, 255, 255))
                        name = getattr(ns, "name", None) or npc_def.get("name", npc_id.title())

                        for j, (ax, ay) in enumerate(abs_positions):
                            ax = int(ax); ay = int(ay)

                            # Only instantiate within warm rect
                            if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                                continue

                            eid = f"poi:{poi_id}:npc:{npc_id}:{j}"
                            desired.add(eid)

                            # If already staged, just ensure it's mirrored into a loaded zone (enables talk/look/etc)
                            if eid in attn_store.entities:
                                try:
                                    obj = attn_store.entities[eid]
                                    _mirror_actor_into_loaded_zone(eid, obj, (ax, ay))
                                except Exception:
                                    pass
                                continue

                            # Compute local pos for the actor's own zone
                            lzx = ax // int(zone_w)
                            lzy = ay // int(zone_h)
                            lx = ax - (lzx * int(zone_w))
                            ly = ay - (lzy * int(zone_h))

                            # Build a real Actor and stage it (like berries)
                            try:
                                a = _build_poi_actor(
                                    eid=eid,
                                    npc_id=npc_id,
                                    name=str(name),
                                    glyph=str(glyph)[0] if glyph else "@",
                                    color=tuple(color) if isinstance(color, (list, tuple)) else (255, 255, 255),
                                    abs_pos=(ax, ay),
                                    local_pos=(int(lx), int(ly)),
                                    poi_id=poi_id,
                                    ns=ns,
                                )
                            except Exception:
                                continue

                            try:
                                attn_store.stage(a, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)
                            except Exception:
                                continue

                            _mirror_actor_into_loaded_zone(eid, a, (ax, ay))

                    except Exception:
                        continue

                # Evict POI children that are no longer desired (for this poi_id)
                try:
                    # (When query is clamped elsewhere, POI query itself is still complete for this rect.)
                    for eid in list(active):
                        if eid not in desired:
                            try:
                                obj = attn_store.entities.get(eid)
                                ap = getattr(obj, "abs_pos", None) if obj is not None else None
                                if ap:
                                    try:
                                        zc = (int(ap[0]) // int(zone_w), int(ap[1]) // int(zone_h), int(zz))
                                        lvl = game.get_zone_for_render(zc)
                                    except Exception:
                                        lvl = None
                                    if lvl is not None:
                                        try:
                                            if eid in lvl.entities:
                                                del lvl.entities[eid]
                                                lvl.spatial_dirty = True
                                        except Exception:
                                            pass
                                        try:
                                            if eid in lvl.actors:
                                                del lvl.actors[eid]
                                                lvl.spatial_dirty = True
                                        except Exception:
                                            pass
                                attn_store.despawn(eid)
                            except Exception:
                                pass
                            active.discard(eid)

                except Exception:
                    pass
            except Exception:
                continue


        # Evict POI children for POIs no longer in scope (when camera moves away).
        try:
            for poi_id, active in list(game._attn_active_poi_children.items()):
                if poi_id in in_scope_pois:
                    continue
                if isinstance(active, set):
                    for eid in list(active):
                        try:
                            obj = attn_store.entities.get(eid)
                            ap = getattr(obj, "abs_pos", None) if obj is not None else None
                            if ap:
                                try:
                                    zc = (int(ap[0]) // int(zone_w), int(ap[1]) // int(zone_h), int(zz))
                                    lvl = game.get_zone_for_render(zc)
                                except Exception:
                                    lvl = None
                                if lvl is not None:
                                    try:
                                        if eid in lvl.entities:
                                            del lvl.entities[eid]
                                            lvl.spatial_dirty = True
                                    except Exception:
                                        pass
                                    try:
                                        if eid in lvl.actors:
                                            del lvl.actors[eid]
                                            lvl.spatial_dirty = True
                                    except Exception:
                                        pass
                            attn_store.despawn(eid)
                        except Exception:
                            pass

                del game._attn_active_poi_children[poi_id]
        except Exception:
            pass

    # -----------------------------
    # C) SITE DETAILS (NPCs from SiteRegistry; yoga-style resolution)
    #
    # Procedural "sites" (fishing_village, spriggan_grove, etc.) currently stamp
    # content on zone visit via mapgen_sites. That breaks yoga (camera can't resolve).
    #
    # This block resolves *site NPC children* like berry children:
    # - deterministic from site.seed + npc_pool
    # - staged into attn_store (primary truth)
    # - mirrored into loaded zone if present (enables talk/look)
    # -----------------------------
    try:
        site_reg = getattr(game, "site_registry", None)
    except Exception:
        site_reg = None

    if site_reg is not None:
        # Track active staged children per site_id
        if not hasattr(game, "_attn_active_site_children"):
            game._attn_active_site_children = {}  # site_id -> set[eid]

        # Determine which site zones intersect our warm rect
        szx0 = int(wx0) // int(zone_w)
        szx1 = (int(wx1) - 1) // int(zone_w)
        szy0 = int(wy0) // int(zone_h)
        szy1 = (int(wy1) - 1) // int(zone_h)

        # Player zone (for "in-person even if hidden")
        try:
            pz = getattr(game, "zone_coord", (None, None, zz))
            player_zx, player_zy, _ = (int(pz[0]), int(pz[1]), int(pz[2]))
        except Exception:
            player_zx, player_zy = (None, None)

        in_scope_sites: set[str] = set()

        import random as _random  # local import to avoid file-level churn

        for zxi in range(szx0, szx1 + 1):
            for zyi in range(szy0, szy1 + 1):
                try:
                    site_specs = list(site_reg.get_at_zone(int(zxi), int(zyi)) or [])
                except Exception:
                    site_specs = []

                for site in site_specs:
                    try:
                        site_id = str(getattr(site, "id", "") or "")
                        if not site_id:
                            continue

                        # Gate refinement by zoom, like POIs do
                        # (You can tune this; this is a sane default band.)
                        thresh = -0.75
                        if float(cam_lod) > float(thresh):
                            continue

                        # Yoga: camera observation is sufficient to resolve site details.
                        # Do NOT gate resolution on discovery/zone-visit visibility.
                        # (If we want "hidden sites" later, that should be a *content* rule, not a render gate.)
                        in_scope_sites.add(site_id)


                        active: set[str] = game._attn_active_site_children.get(site_id)
                        if not isinstance(active, set):
                            active = set()
                            game._attn_active_site_children[site_id] = active
                        desired: set[str] = set()

                        # Deterministic RNG from site seed
                        try:
                            seed = int(getattr(site, "seed", 0) or 0)
                        except Exception:
                            seed = 0
                        rng = _random.Random(seed)

                        tags = getattr(site, "tags", {}) or {}
                        npc_pool = list(tags.get("npc_pool", []) or [])
                        if not npc_pool:
                            # Nothing to resolve for this site type (yet)
                            continue

                        # Pick 1-3 NPCs deterministically (bounded by pool size)
                        k = min(len(npc_pool), max(1, rng.randint(1, 3)))
                        chosen = [npc_pool[rng.randrange(0, len(npc_pool))] for _ in range(k)]

                        # Deterministic offsets near zone center (keeps them stable)
                        offsets = [(0, 0), (2, 0), (-2, 0), (0, 2), (0, -2), (3, 1), (-3, 1), (1, 3), (-1, -3)]
                        rng.shuffle(offsets)

                        # Zone anchor in ABS
                        zx, zy, zdepth = map(int, getattr(site, "coord", (zxi, zyi, zz)))
                        base_ax = zx * int(zone_w) + (int(zone_w) // 2)
                        base_ay = zy * int(zone_h) + (int(zone_h) // 2)

                        for i, npc_id in enumerate(chosen):
                            try:
                                npc_id = str(npc_id)
                                if not npc_id:
                                    continue

                                ox, oy = offsets[i % len(offsets)]
                                ax = int(base_ax + ox)
                                ay = int(base_ay + oy)

                                # Only instantiate within warm rect
                                if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                                    continue

                                eid = f"site:{site_id}:npc:{npc_id}:{i}"
                                desired.add(eid)

                                # Already staged? just mirror into loaded zone if available
                                if eid in attn_store.entities:
                                    try:
                                        obj = attn_store.entities[eid]
                                        _mirror_actor_into_loaded_zone(eid, obj, (ax, ay))
                                    except Exception:
                                        pass
                                    continue

                                # Compute local coords for the actor's own zone
                                lzx = ax // int(zone_w)
                                lzy = ay // int(zone_h)
                                lx = ax - (lzx * int(zone_w))
                                ly = ay - (lzy * int(zone_h))

                                # Reuse the POI actor builder for now (keeps NPC defs consistent)
                                npc_def = npcs.NPC_DEFS.get(npc_id, {}) if "npcs" in globals() else {}
                                glyph = npc_def.get("glyph", "@")
                                color = npc_def.get("color", (255, 255, 255))
                                name = npc_def.get("name", npc_id.title())

                                a = _build_poi_actor(
                                    eid=eid,
                                    npc_id=npc_id,
                                    name=str(name),
                                    glyph=str(glyph)[0] if glyph else "@",
                                    color=tuple(color) if isinstance(color, (list, tuple)) else (255, 255, 255),
                                    abs_pos=(ax, ay),
                                    local_pos=(int(lx), int(ly)),
                                    poi_id=f"site:{site_id}",   # piggyback field; tags below disambiguate
                                    ns=None,
                                )
                                # Patch tags so downstream can tell it's a site-child, not a POI-child
                                try:
                                    a.tags = getattr(a, "tags", {}) or {}
                                    a.tags.update({"site": True, "site_id": site_id, "site_npc": True})
                                except Exception:
                                    pass

                                attn_store.stage(a, abs_x=ax, abs_y=ay, zz=zz, lineage_id=eid)
                                _mirror_actor_into_loaded_zone(eid, a, (ax, ay))

                            except Exception:
                                continue

                        # Evict site children no longer desired
                        try:
                            for eid in list(active):
                                if eid not in desired:
                                    try:
                                        obj = attn_store.entities.get(eid)
                                        ap = getattr(obj, "abs_pos", None) if obj is not None else None
                                        if ap:
                                            try:
                                                zc = (int(ap[0]) // int(zone_w), int(ap[1]) // int(zone_h), int(zz))
                                                lvl = game.get_zone_for_render(zc)
                                            except Exception:
                                                lvl = None
                                            if lvl is not None:
                                                try:
                                                    if eid in lvl.entities:
                                                        del lvl.entities[eid]
                                                        lvl.spatial_dirty = True
                                                except Exception:
                                                    pass
                                                try:
                                                    if eid in lvl.actors:
                                                        del lvl.actors[eid]
                                                        lvl.spatial_dirty = True
                                                except Exception:
                                                    pass
                                        attn_store.despawn(eid)
                                    except Exception:
                                        pass
                                    active.discard(eid)

                            for eid in desired:
                                active.add(eid)
                        except Exception:
                            pass

                    except Exception:
                        continue

        # Evict entire site sets that left scope
        try:
            for site_id, active in list(game._attn_active_site_children.items()):
                if site_id in in_scope_sites:
                    continue
                if isinstance(active, set):
                    for eid in list(active):
                        try:
                            attn_store.despawn(eid)
                        except Exception:
                            pass
                del game._attn_active_site_children[site_id]
        except Exception:
            pass


def renderables_in_abs_rect(
    game,
    abs_rect: Tuple[float, float, float, float],
    *,
    include_actors: bool = True,
    include_entities: bool = True,
    cam_lod: float,
    dmin: float = -5.0,
    dmax: float = 0.75,
    fade_w: float = 0.6,
    max_count: int = 2000,
    proxy_cls,
) -> List[object]:
    """Return renderable objects intersecting an absolute-world tile rect.

    abs_rect = (x0, y0, x1, y1) in absolute world-tile coordinates.
    Rect is half-open: [x0,x1) Ã— [y0,y1).

    Camera-centric query for god-vision / scry / macro-view.

    Critical invariants:
    - Rendering must not instantiate gameplay state.
    - get_zone_for_render() may return None for unloaded zones (and that's OK).
    - World-index entities (sites, later forests/cities/etc.) must still be queryable
      even when the camera spans many zones.
    """
    try:
        ax0, ay0, ax1, ay1 = map(float, abs_rect)
    except Exception:
        return []

    if ax1 < ax0:
        ax0, ax1 = ax1, ax0
    if ay1 < ay0:
        ay0, ay1 = ay1, ay0
    if ax1 == ax0 or ay1 == ay0:
        return []

    cfg = getattr(game, "cfg", None)

    # Zone dimensions MUST match actual zone tile dims.
    zone_w = int(getattr(cfg, "world_width", 0) or 0)
    zone_h = int(getattr(cfg, "world_height", 0) or 0)
    if zone_w <= 0 or zone_h <= 0:
        try:
            lvl0 = game._level()
            zone_w = int(getattr(lvl0.world, "width", 60) or 60)
            zone_h = int(getattr(lvl0.world, "height", 40) or 40)
        except Exception:
            zone_w = 60
            zone_h = 40

    # Which depth are we on?
    _czx, _czy, zz = getattr(game, "zone_coord", (0, 0, 0))
    zz = int(zz)

    # ------------------------------------------------------------
    # YOGA: attention-driven resolution must run when the camera changes,
    # regardless of *how* the change happened (pan vs zoom vs look).
    #
    # The renderer calls renderables_in_abs_rect every frame; if the camera
    # rect / lod band changed since last frame, sync our attention store now.
    # ------------------------------------------------------------
    try:
        sig = (
            round(float(ax0), 3), round(float(ay0), 3), round(float(ax1), 3), round(float(ay1), 3),
            round(float(cam_lod), 4), int(zz),
        )
        last = getattr(game, "_attn_last_sig", None)
        if sig != last:
            game._attn_last_sig = sig
            try:
                game.sync_attention_instantiation((ax0, ay0, ax1, ay1), cam_lod=float(cam_lod))
            except Exception:
                # Never fail rendering because attention sync hiccuped.
                pass
    except Exception:
        pass


    # ------------------------------------------------------------
    # WARM RECT (IMPORTANT): drive *candidate gathering* from warmth,
    # not from the razor-thin camera rect. This prevents "player disappears
    # when panning a few pixels" due to float/rounding/tight rect edges.
    # ------------------------------------------------------------
    # Default padding in ABS tiles around camera rect (offscreen pursuit / warmth)
    pad_tiles = float(getattr(cfg, "entity_render_pad_tiles", 0.0) or 0.0)
    if pad_tiles <= 0.0:
        try:
            pad_tiles = 0.5 * max((ax1 - ax0), (ay1 - ay0))
        except Exception:
            pad_tiles = 0.0

    wx0 = ax0 - pad_tiles
    wy0 = ay0 - pad_tiles
    wx1 = ax1 + pad_tiles
    wy1 = ay1 + pad_tiles

    # Union with inhabited actor position (keeps the fovea "warm" even if camera pans slightly)
    pax = pay = None
    try:
        pax, pay = game._get_player_abs()
        wx0 = min(wx0, float(pax) - pad_tiles)
        wy0 = min(wy0, float(pay) - pad_tiles)
        wx1 = max(wx1, float(pax) + pad_tiles)
        wy1 = max(wy1, float(pay) + pad_tiles)
    except Exception:
        pax = pay = None

    # ------------------------------------------------------------
    # Zone span covered by *warm rect* (NOT raw camera rect)
    # ------------------------------------------------------------
    zx0 = int(math.floor(wx0 / float(zone_w)))
    zy0 = int(math.floor(wy0 / float(zone_h)))
    zx1 = int(math.floor((wx1 - 1e-9) / float(zone_w)))
    zy1 = int(math.floor((wy1 - 1e-9) / float(zone_h)))

    max_zone_span = int(getattr(cfg, "render_max_zone_span", 9) or 9)
    max_zone_span = max(1, max_zone_span)
    span_x = (zx1 - zx0 + 1)
    span_y = (zy1 - zy0 + 1)
    too_many_zones = (span_x > max_zone_span) or (span_y > max_zone_span)

    out: List[object] = []
    candidates: List[Tuple[object, float, float, Tuple[int, int, int], Tuple[int, int], float]] = []

    # Camera center for scoring (raw camera center, not warm center)
    ccx = 0.5 * (ax0 + ax1)
    ccy = 0.5 * (ay0 + ay1)

    def _score(abs_size: float, abs_x: float, abs_y: float) -> float:
        dx = abs_x - ccx
        dy = abs_y - ccy
        return abs_size * 1000.0 - (dx * dx + dy * dy)

    # ------------------------------------------------------------
    # 1) WORLD INDEX ENTITIES (sites, POIs, later forests/cities/etc.)
    # ------------------------------------------------------------
    game._ensure_world_site_entities(zone_w=zone_w, zone_h=zone_h)
    game._ensure_world_poi_entities(zone_w=zone_w, zone_h=zone_h)

    # Always ensure aggregate proxies for a **clamped camera window**.
    # This keeps god-vision panning responsive while still allowing distant areas
    # to resolve aggregates when the camera is there.
    try:
        zx0c, zx1c, zy0c, zy1c, _clamped = game._clamp_zone_window(
            zx0, zx1, zy0, zy1,
            zone_span_cap=max_zone_span,
            ccx=ccx, ccy=ccy,
            zone_w=zone_w, zone_h=zone_h,
        )
        game._ensure_world_aggregate_entities(
            zone_w=zone_w,
            zone_h=zone_h,
            zx0=zx0c,
            zx1=zx1c,
            zy0=zy0c,
            zy1=zy1c,
            zz=zz,
            kinds=("berry_patch",),
        )
    except Exception:
        pass


    # Query world index using WARM rect so panning doesn't "drop" things on the edge.
    try:
        if getattr(game, "world_entity_index", None) is not None:
            for ref in game.world_entity_index.query_abs_rect((wx0, wy0, wx1, wy1), z=zz, zone_span_cap=None):
                obj = ref.ent
                zx, zy, _z = ref.zone_coord
                ox, oy = ref.local_pos

                abs_x = float(zx * zone_w + ox)
                abs_y = float(zy * zone_h + oy)

                abs_size = game._size_for_render(obj)
                ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                delta = float(cam_lod) - float(ent_lod)

                if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                    continue

                # Intersection test against WARM rect (gather candidates), not camera rect.
                half = 0.5 * float(abs_size)
                ex0 = abs_x - half
                ey0 = abs_y - half
                ex1 = abs_x + half
                ey1 = abs_y + half
                if ex1 <= wx0 or ex0 >= wx1 or ey1 <= wy0 or ey0 >= wy1:
                    continue

                sc = _score(abs_size, abs_x, abs_y)

                # Render-only detail proxies for aggregates when zoomed in.
                # NOTE (Yoga): detail proxies are disabled.
                # If berries/soldiers/etc. are visible at a given band, they must be real entities
                # staged into LevelState.entities by the attention lifecycle (not invented here).
                pass

                candidates.append((obj, abs_x, abs_y, (int(zx), int(zy), int(zz)), (int(ox), int(oy)), sc))
    except Exception:
        pass

    
    # ------------------------------------------------------------
    # 1.5) ATTENTION-STAGED ENTITIES (Route 2)
    # ------------------------------------------------------------
    try:
        attn_store = getattr(game, "attn_store", None)
        if attn_store is not None and include_entities:
            for obj, abs_x, abs_y in attn_store.query_abs_rect((wx0, wy0, wx1, wy1), zz=zz):
                abs_size = game._size_for_render(obj)
                ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                delta = float(cam_lod) - float(ent_lod)

                if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                    continue

                sc = _score(abs_size, abs_x, abs_y)

                # Derive zone/local purely for renderer convenience.
                zx = int(math.floor(abs_x / float(zone_w)))
                zy = int(math.floor(abs_y / float(zone_h)))
                ox = int(abs_x - zx * zone_w)
                oy = int(abs_y - zy * zone_h)

                candidates.append((obj, float(abs_x), float(abs_y), (int(zx), int(zy), int(zz)), (int(ox), int(oy)), sc))
    except Exception:
        pass


    # ------------------------------------------------------------
    # 2) LOADED ZONES ONLY (never instantiate zones here)
    # ------------------------------------------------------------
    coords_to_check: List[Tuple[int, int, int]] = []

    if too_many_zones:
        # Camera spans many zones; do NOT iterate them all.
        # But do render entities from zones already in memory that intersect WARM rect.
        try:
            for coord in getattr(game, "levels", {}).keys():
                try:
                    zx, zy, zdepth = coord
                except Exception:
                    continue
                if int(zdepth) != int(zz):
                    continue

                zax0 = float(zx * zone_w)
                zay0 = float(zy * zone_h)
                zax1 = zax0 + float(zone_w)
                zay1 = zay0 + float(zone_h)
                if zax1 <= wx0 or zax0 >= wx1 or zay1 <= wy0 or zay0 >= wy1:
                    continue

                coords_to_check.append((int(zx), int(zy), int(zz)))
        except Exception:
            coords_to_check = []
    else:
        for zx in range(zx0, zx1 + 1):
            for zy in range(zy0, zy1 + 1):
                coords_to_check.append((int(zx), int(zy), int(zz)))

    for coord in coords_to_check:
        zx, zy, _ = coord
        try:
            level = game.get_zone_for_render(coord)
        except Exception:
            continue
        if level is None:
            continue

        if getattr(level, "spatial_dirty", True) or not getattr(level, "spatial_bins", None):
            game._rebuild_spatial_bins(level)

        # Local rect within this zone for WARM rect.
        lx0 = wx0 - float(zx * zone_w)
        ly0 = wy0 - float(zy * zone_h)
        lx1 = wx1 - float(zx * zone_w)
        ly1 = wy1 - float(zy * zone_h)

        if lx1 <= 0 or ly1 <= 0 or lx0 >= zone_w or ly0 >= zone_h:
            continue
        lx0 = max(0.0, min(float(zone_w), lx0))
        ly0 = max(0.0, min(float(zone_h), ly0))
        lx1 = max(0.0, min(float(zone_w), lx1))
        ly1 = max(0.0, min(float(zone_h), ly1))
        if lx1 <= lx0 or ly1 <= ly0:
            continue

        bs = int(getattr(level, "spatial_bin_size", 16) or 16)
        bs = max(1, bs)
        bx0 = int(math.floor(lx0 / bs))
        by0 = int(math.floor(ly0 / bs))
        bx1 = int(math.floor((lx1 - 1e-6) / bs))
        by1 = int(math.floor((ly1 - 1e-6) / bs))

        bins = level.spatial_bins
        seen_ids: set[str] = set()

        for by in range(by0, by1 + 1):
            for bx in range(bx0, bx1 + 1):
                ids = bins.get((bx, by))
                if not ids:
                    continue
                for obj_id in ids:
                    if obj_id in seen_ids:
                        continue
                    seen_ids.add(obj_id)

                    obj = None
                    if include_actors:
                        obj = level.actors.get(obj_id)
                    if obj is None and include_entities:
                        obj = level.entities.get(obj_id)
                    if obj is None:
                        continue

                    try:
                        ox, oy = obj.pos
                    except Exception:
                        continue
                    if not (lx0 <= ox < lx1 and ly0 <= oy < ly1):
                        continue

                    abs_x = float(zx * zone_w + ox)
                    abs_y = float(zy * zone_h + oy)

                    abs_size = game._size_for_render(obj)
                    ent_lod = math.log2(abs_size) if abs_size > 0 else -30.0
                    delta = float(cam_lod) - float(ent_lod)
                    if delta < (float(dmin) - float(fade_w)) or delta > (float(dmax) + float(fade_w)):
                        continue

                    sc = _score(abs_size, abs_x, abs_y)
                    candidates.append((obj, abs_x, abs_y, coord, (int(ox), int(oy)), sc))

    if not candidates:
        return []

    # ------------------------------------------------------------
    # Banded multi-scale attention selection
    # ------------------------------------------------------------
    band_width = int(getattr(cfg, "entity_band_width", 4) or 4)
    band_width = max(1, band_width)

    bucket_slack = int(getattr(cfg, "entity_bucket_slack", 3) or 3)

    k_cell = int(getattr(cfg, "entity_render_k_cell", 12) or 12)
    k_cell = max(1, k_cell)

    k_layer = int(getattr(cfg, "entity_render_k_layer", max_count) or max_count)
    if k_layer <= 0:
        k_layer = max_count

    # Band overlap allows adjacent bands to still render (prevents "sites vanish entirely")
    band_overlap = int(getattr(cfg, "entity_band_overlap", 1) or 1)
    band_overlap = max(0, band_overlap)

    cam_lod_f = float(cam_lod)

    # Bias lets you zoom in closer before band transitions.
    zoom_in_bias = float(getattr(cfg, "entity_zoom_in_bias_lod", 3.0) or 3.0)
    cam_lod_f += zoom_in_bias

    # Optional additional band bias (simple constant nudge).
    band_bias = float(getattr(cfg, "entity_band_bias", 0.0) or 0.0)
    cam_lod_f += band_bias

    if not hasattr(game, "_entity_active_band"):
        game._entity_active_band = int(math.floor(cam_lod_f / float(band_width)))

    b = int(getattr(game, "_entity_active_band", 0) or 0)

    # Less sticky hysteresis by default.
    h = float(getattr(cfg, "entity_band_hysteresis", 0.05) or 0.05)

    if cam_lod_f > ((b + 1) * band_width + h):
        b = int(math.floor(cam_lod_f / float(band_width)))
    elif cam_lod_f < (b * band_width - h):
        b = int(math.floor(cam_lod_f / float(band_width)))

    game._entity_active_band = b

    lod_min = b * band_width
    lod_max = lod_min + (band_width - 1)

    cell_lod_min = lod_min + bucket_slack
    cell_lod_max = lod_max + bucket_slack

    def _ent_lod(sz: float) -> float:
        return math.log2(sz) if sz and sz > 0.0 else -30.0

    # Bucket candidates by native cell.
    cell_map = {}
    for obj, ex, ey, coord, local_pos, _sc in candidates:
        try:
            abs_size = float(game._size_for_render(obj))
        except Exception:
            abs_size = 1.0

        e_lod = _ent_lod(abs_size)
        e_band = int(math.floor(e_lod / float(band_width)))

        # Allow overlap bands around active band.
        if abs(e_band - b) > band_overlap:
            continue

        try:
            native_cell_lod = int(math.floor(e_lod)) + int(bucket_slack)
        except Exception:
            native_cell_lod = cell_lod_min

        if native_cell_lod < cell_lod_min:
            native_cell_lod = cell_lod_min
        elif native_cell_lod > cell_lod_max:
            native_cell_lod = cell_lod_max

        cell_size = float(2 ** int(native_cell_lod))
        cx = int(math.floor(float(ex) / cell_size))
        cy = int(math.floor(float(ey) / cell_size))

        key = (int(native_cell_lod), cx, cy)
        cell_map.setdefault(key, []).append((obj, float(ex), float(ey), coord, local_pos, abs_size))

    if not cell_map:
        return []

    selected = []

    # Iterate cell LODs for this band
    for cell_lod in range(int(cell_lod_min), int(cell_lod_max) + 1):
        cell_size = float(2 ** int(cell_lod))

        # Hot cells from WARM rect
        cx0 = int(math.floor(wx0 / cell_size))
        cy0 = int(math.floor(wy0 / cell_size))
        cx1 = int(math.floor((wx1 - 1e-9) / cell_size))
        cy1 = int(math.floor((wy1 - 1e-9) / cell_size))

        hot_keys = []
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                key = (int(cell_lod), cx, cy)
                if key not in cell_map:
                    continue
                ccx_cell = (float(cx) + 0.5) * cell_size
                ccy_cell = (float(cy) + 0.5) * cell_size
                dx = ccx_cell - ccx
                dy = ccy_cell - ccy
                hot_keys.append((dx * dx + dy * dy, key))

        hot_keys.sort(key=lambda t: t[0])

        for _d2, key in hot_keys:
            ents = cell_map.get(key, [])
            if not ents:
                continue

            def _rank(rec):
                _obj, _x, _y, _coord, _lp, _sz = rec
                dx = _x - ccx
                dy = _y - ccy
                return (dx * dx + dy * dy, id(_obj))

            ents.sort(key=_rank)
            for rec in ents[:k_cell]:
                selected.append(rec)

            if k_layer > 0 and len(selected) >= k_layer:
                break

        if k_layer > 0 and len(selected) >= k_layer:
            break

    # ------------------------------------------------------------
    # YOGA INVARIANT: EVERYTHING ONSCREEN MUST RENDER.
    #
    # k_cell / k_layer / max_count are *attention budgets* and may
    # cull OFFSCREEN (warm padding) candidates, but they must never
    # hide entities that are actually inside the raw camera rect.
    #
    # Otherwise dense piles (e.g. item_depot) get arbitrarily clipped
    # by k_cell because many items share the same native bucket cell.
    # ------------------------------------------------------------
    onscreen: list[tuple[object, float, float, tuple[int, int, int], tuple[int, int], float]] = []
    onscreen_ids: set[object] = set()

    def _obj_key(o: object) -> object:
        # Prefer stable entity ids if present; fallback to object identity.
        oid = getattr(o, "id", None)
        return oid if oid is not None else id(o)

    # NOTE: candidates are already LoD-filtered (delta vs dmin/dmax/fade_w),
    # so we only enforce geometric inclusion here.
    for obj, ex, ey, coord, local_pos, _sc in candidates:
        if ax0 <= float(ex) < ax1 and ay0 <= float(ey) < ay1:
            try:
                abs_size = float(game._size_for_render(obj))
            except Exception:
                abs_size = 1.0
            rec = (obj, float(ex), float(ey), coord, local_pos, abs_size)
            key = _obj_key(obj)
            if key in onscreen_ids:
                continue
            onscreen_ids.add(key)
            onscreen.append(rec)

    # Merge: onscreen first, then the budget-selected offscreen/warm set.
    merged: list[tuple[object, float, float, tuple[int, int, int], tuple[int, int], float]] = []
    merged.extend(onscreen)

    for rec in selected:
        obj = rec[0]
        if _obj_key(obj) in onscreen_ids:
            continue
        merged.append(rec)

    # Apply max_count ONLY to the warm/offscreen remainder.
    # Never truncate onscreen.
    if max_count > 0 and len(merged) > max_count:
        keep_n = max_count - len(onscreen)
        if keep_n <= 0:
            merged = onscreen
        else:
            merged = onscreen + merged[len(onscreen) : len(onscreen) + keep_n]

    selected = merged


    # Hard guarantee: include player if we can find it.
    # (Not a "render special-case"; it's an observer invariant.)
    try:
        p = getattr(game, "player", None)
        if p is not None:
            pid = getattr(p, "id", None)
            if pid is not None:
                has_player = any(getattr(rec[0], "id", None) == pid for rec in selected)
                if not has_player and pax is not None and pay is not None:
                    # If player was in candidates, it would've been selected; but if not,
                    # include it anyway to satisfy "fovea never disappears".
                    # Compute correct local_pos (not 0,0!) for tile lookup in rendering.
                    try:
                        p_zone_coord, p_local = game.zone_local_from_abs((int(pax), int(pay)), depth=zz)
                        p_local_pos = (int(p_local[0]), int(p_local[1]))
                    except Exception:
                        # Fallback: use player.pos if abs conversion fails
                        p_zone_coord = getattr(game, "zone_coord", (0, 0, zz))
                        p_local_pos = getattr(p, "pos", (0, 0))
                    # Debug: log when player fallback is triggered
                    with open("C:/Games/Edgecaster/debug.log", "a") as f:
                        f.write(f"[RenderSelect] PLAYER FALLBACK: abs=({pax},{pay}), zone={p_zone_coord}, local={p_local_pos}, not in {len(selected)} candidates\n")
                    selected.append((p, float(pax), float(pay), p_zone_coord, p_local_pos, 1.0))
    except Exception:
        pass

    for obj, abs_x, abs_y, coord, local_pos, _abs_size in selected:
        out.append(
            proxy_cls(
                obj=obj,
                abs_x=float(abs_x),
                abs_y=float(abs_y),
                zone_coord=coord,
                local_pos=local_pos,
            )
        )

    return out

def _ensure_world_site_entities(game, *, zone_w: int, zone_h: int) -> None:
    """
    Incrementally build world-level site entities (macro renderables) from the SiteRegistry.

    Important properties:
    - No gameplay side effects. Does NOT stamp walls/NPCs/etc.
    - Does NOT wait for async placement completion.
    - Not "build once": it incrementally adds newly-placed or newly-revealed sites.
    """

    # ---------------------------------------------------------------------
    # Leviathan (single world-scale test entity)
    # Declarative, idempotent, no flags
    # ---------------------------------------------------------------------

    # Hard-wired "spec" (exactly like a site spec, just inline for now)
    leviathan_spec = {
        "id": "world:leviathan",
        "hotspot_index": 0,  # east corruption patch
    }

    try:
        # Authoritative data
        hotspots = list(getattr(game, "corruption_hotspots", []) or [])
        grid = getattr(game, "tile_julia_grid", None)

        if hotspots and isinstance(grid, dict):
            jx, jy, *_ = hotspots[leviathan_spec["hotspot_index"]]

            # Julia â†’ ABS tile conversion (linear grid inversion)
            view_min_jx = float(grid["view_min_jx"])
            view_min_jy = float(grid["view_min_jy"])
            step_x = float(grid["step_x"])
            step_y = float(grid["step_y"])
            total_x = int(grid["total_x"])
            total_y = int(grid["total_y"])

            ax = int(round((float(jx) - view_min_jx) / step_x))
            ay = int(round((float(jy) - view_min_jy) / step_y))
            ax = max(0, min(total_x - 1, ax))
            ay = max(0, min(total_y - 1, ay))

            # ABS â†’ zone + local
            zx = ax // zone_w
            zy = ay // zone_h
            zz = 0
            ox = ax % zone_w
            oy = ay % zone_h

            # Build entity from existing prototype
            levi_proto = prototypes.resolve_proto("leviathan")

            ent = spawn_factory.build_entity_from_spec(
                spec=levi_proto,
                eid=leviathan_spec["id"],
                pos=(ox, oy),
                overrides={
                    "kind": "feature",  # world-scale renderable, like sites
                    "tags": {
                        "world_entity": True,
                        "leviathan": True,
                        # Used by spatial_music "dominates frame" heuristic
                        "abs_size": float(max(zone_w, zone_h) * 0.80),
                        "corruption_source": True,
                    },
                },
            )

            # Idempotent add (WorldEntityIndex de-dupes by ID)
            game.world_entity_index.add(
                ent,
                zone_coord=(zx, zy, zz),
                local_pos=(ox, oy),
            )

    except Exception:
        pass


    # Initialize tracking
    if not hasattr(game, "_world_site_ids_built"):
        game._world_site_ids_built = set()

    # If zone dims changed, rebuild the index (safe: we can re-add incrementally)
    prev_wh = getattr(game, "_world_entity_index_wh", None)
    wh = (int(zone_w), int(zone_h))
    if prev_wh != wh or getattr(game, "world_entity_index", None) is None:
        try:
            game.world_entity_index = WorldEntityIndex(zone_w=wh[0], zone_h=wh[1])
            game._world_entity_index_wh = wh
            game._world_site_ids_built.clear()
            # If the index is rebuilt, POI/world proxies must be re-added too.
            try:
                if hasattr(game, "_world_poi_ids_built"):
                    game._world_poi_ids_built.clear()
            except Exception:
                pass
        except Exception:
            return

    # Grab site specs. In god vision: all sites that exist so far.
    # In normal: only visible/discovered sites.
    try:
        if bool(getattr(game, "god_vision", False)):
            specs = list(getattr(game.site_registry, "_sites", {}).values())
        else:
            specs = list(game.site_registry.get_visible())
    except Exception:
        return

    if not specs:
        return

    site_types = load_site_types()

    # Resolve settlement prototype once
    try:
        settlement_proto = prototypes.resolve_proto("settlement")
    except Exception:
        settlement_proto = {
            "id": "settlement",
            "name": "Settlement",
            "glyph": "Â§",
            "color": [240, 220, 160],
            "kind": "feature",
            "base_size": 64,
            "tags": {},
        }

    new_count = 0

    for s in specs:
        try:
            sid = getattr(s, "id", None) or f"{getattr(s,'coord',(0,0,0))}"
            eid = f"site:{sid}"
            if eid in game._world_site_ids_built:
                continue

            zx, zy, zz = map(int, getattr(s, "coord", (0, 0, 0)))
            cfg = site_types.get(getattr(s, "kind", ""), None)

            name = getattr(cfg, "name", None) or getattr(s, "kind", "site")
            glyph = getattr(cfg, "map_glyph", "?") if cfg else "?"
            color = list(getattr(cfg, "map_color", (255, 255, 255))) if cfg else [255, 255, 255]

            # Zone-center for now (later: intra-zone location from spec if you add it)
            ox = int(zone_w // 2)
            oy = int(zone_h // 2)

            overrides = {
                "name": name,
                "glyph": glyph,
                "color": color,
                "kind": "feature",
                "base_size": 64,
                "tags": {
                    "world_entity": True,
                    "site": True,
                    "site_id": getattr(s, "id", ""),
                    "site_kind": getattr(s, "kind", ""),
                    "site_seed": int(getattr(s, "seed", 0) or 0),
                    "site_biome": str(getattr(s, "biome", "")),
                    **(getattr(s, "tags", {}) or {}),
                },
            }

            ent = spawn_factory.build_entity_from_spec(
                spec=settlement_proto,
                eid=eid,
                pos=(ox, oy),
                overrides=overrides,
            )

            game.world_entity_index.add(ent, zone_coord=(zx, zy, zz), local_pos=(ox, oy))
            game._world_site_ids_built.add(eid)
            new_count += 1

        except Exception:
            continue

    if new_count and hasattr(game, "_debug"):
        game._debug(f"[world_entities] added {new_count} site proxies (total={len(game._world_site_ids_built)})")

def _ensure_world_poi_entities(game, *, zone_w: int, zone_h: int) -> None:
    """Incrementally build world-level POI entities (macro renderables) from the POIRegistry.

    This creates visible markers for v2-style POIs (like ancient_colosseum) that have
    show_on_map=True in their tags, allowing them to appear on the normal dungeon map
    at appropriate zoom levels.

    Important properties:
    - No gameplay side effects. Does NOT stamp walls/NPCs/etc.
    - Incrementally adds newly-placed or newly-discovered POIs.
    """
    # Initialize tracking
    if not hasattr(game, "_world_poi_ids_built"):
        game._world_poi_ids_built = set()

    # Get POI registry
    poi_reg = getattr(game, "poi_registry", None)
    if poi_reg is None:
        return

    # If zone dims changed, clear tracking
    prev_wh = getattr(game, "_world_poi_entity_wh", None)
    wh = (int(zone_w), int(zone_h))
    if prev_wh != wh:
        game._world_poi_ids_built.clear()
        game._world_poi_entity_wh = wh

    # Ensure world_entity_index exists
    if getattr(game, "world_entity_index", None) is None:
        return

    # Resolve settlement prototype for POI markers
    try:
        settlement_proto = prototypes.resolve_proto("settlement")
    except Exception:
        settlement_proto = {
            "id": "settlement",
            "name": "Settlement",
            "glyph": "Â§",
            "color": [240, 220, 160],
            "kind": "feature",
            "base_size": 64,
            "tags": {},
        }

    new_count = 0

    for poi in poi_reg:
        try:
            poi_id = poi.id
            eid = f"poi:{poi_id}"
            if eid in game._world_poi_ids_built:
                continue

            # Only show POIs with show_on_map=True
            tags = poi.tags or {}
            if not tags.get("show_on_map", False):
                continue

            # Get POI display properties
            name = poi.name or poi.kind or "Unknown"
            glyph = tags.get("map_glyph", "?")
            color_raw = tags.get("map_color", [200, 200, 200])
            if isinstance(color_raw, (list, tuple)) and len(color_raw) >= 3:
                color = [int(color_raw[0]), int(color_raw[1]), int(color_raw[2])]
            else:
                color = [200, 200, 200]

            # Get anchor position in ABS coordinates
            anchor_x, anchor_y = poi.anchor_abs
            depth = poi.depth

            # Convert to zone + local position
            zx = anchor_x // zone_w
            zy = anchor_y // zone_h
            ox = anchor_x % zone_w
            oy = anchor_y % zone_h

            # Calculate a reasonable base_size based on footprint
            footprint = poi.footprint
            poi_width = footprint.x1 - footprint.x0
            poi_height = footprint.y1 - footprint.y0
            base_size = max(poi_width, poi_height, 64)

            overrides = {
                "name": name,
                "glyph": glyph,
                "color": color,
                "kind": "feature",
                "base_size": base_size,
                "tags": {
                    "world_entity": True,
                    "poi": True,
                    "poi_id": poi_id,
                    "poi_kind": poi.kind,
                    **tags,
                },
            }

            ent = spawn_factory.build_entity_from_spec(
                spec=settlement_proto,
                eid=eid,
                pos=(ox, oy),
                overrides=overrides,
            )

            game.world_entity_index.add(ent, zone_coord=(zx, zy, depth), local_pos=(ox, oy))
            game._world_poi_ids_built.add(eid)
            new_count += 1

        except Exception:
            continue

    if new_count and hasattr(game, "_debug"):
        game._debug(f"[world_entities] added {new_count} POI proxies (total={len(game._world_poi_ids_built)})")

def _ensure_world_aggregate_entities(
    game,
    *,
    zone_w: int,
    zone_h: int,
    zx0: int,
    zx1: int,
    zy0: int,
    zy1: int,
    zz: int,
    kinds=None,
) -> None:
    """Ensure aggregate world entities exist in WorldEntityIndex (no gameplay side effects).

    General-purpose mechanism used for berry patches today; goblin bands / forests / armies tomorrow.
    """
    # If the world_entity_index was rebuilt (e.g. zone dims changed), aggregates must be re-added.
    wh = (int(zone_w), int(zone_h))
    prev = getattr(game, "_agg_world_entity_index_wh", None)
    if prev != wh:
        try:
            # Clear incremental generation tracking so we can repopulate the fresh index.
            if hasattr(game, "_agg_worldgen_done"):
                game._agg_worldgen_done.clear()
        except Exception:
            pass
        game._agg_world_entity_index_wh = wh

    aggregate_system.ensure_world_aggregates(
        game,
        zone_w=int(zone_w),
        zone_h=int(zone_h),
        zx0=int(zx0),
        zx1=int(zx1),
        zy0=int(zy0),
        zy1=int(zy1),
        zz=int(zz),
        kinds=kinds,
    )

def _realize_aggregate_details_in_zone(game, level: "LevelState", coord: Tuple[int, int, int], kinds=None) -> None:
    """When a zone is created/entered (simulation allowed), realize aggregate details into real entities."""
    cfg = getattr(game, "cfg", None)
    zone_w = int(getattr(cfg, "world_width", 60) or 60)
    zone_h = int(getattr(cfg, "world_height", 40) or 40)

    # Ensure aggregates for this bucket exist in the world index first.
    game._ensure_world_aggregate_entities(
        zone_w=zone_w,
        zone_h=zone_h,
        zx0=int(coord[0]),
        zx1=int(coord[0]),
        zy0=int(coord[1]),
        zy1=int(coord[1]),
        zz=int(coord[2]),
        kinds=kinds,
    )

    aggregate_system.realize_details_for_loaded_zone(
        game,
        level,
        zone_coord=(int(coord[0]), int(coord[1]), int(coord[2])),
        zone_w=zone_w,
        zone_h=zone_h,
        kinds=kinds,
    )
