from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Dict, List, Tuple

from edgecaster import prototypes
from edgecaster import spawn_factory
from edgecaster.content import npcs
from edgecaster.enemies import factory as enemy_factory
from edgecaster.systems import aggregate_resolution as aggregate_system
from edgecaster.systems import spawning as spawning_system
from edgecaster.systems import site_placement as site_placement_system
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

    # Ensure world-map site entities exist (yogic: everything is an entity).
    # Idempotent; bridges Game.__init__ init order.
    try:
        site_placement_system.ensure_world_sites(game)
    except Exception:
        pass


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
            kinds=None,
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

    if not hasattr(game, "_attn_active_resolved_children"):
        game._attn_active_resolved_children = {}  # parent_eid -> set[child_eid]


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
        if not kind:
            continue

        # Generic aggregate refinement (cluster mode)
        if tags.get("aggregate") is not True:
            continue
        if str(tags.get("detail_mode", "") or "") != "cluster":
            continue

        # ------------------------------------------------------------
        # YOGA: Detail instantiation threshold derives from the child’s size,
        # using the same LoD regime as renderables_in_abs_rect().
        #
        # Child is eligible to render when:
        #   delta = cam_lod - log2(child_abs_size) <= dmax
        # so instantiate when:
        #   cam_lod <= log2(child_abs_size) + dmax
        #
        # We keep an escape hatch: if tags set detail_lod_threshold_mode="fixed",
        # we honor the explicit detail_lod_threshold for bespoke tuning.
        # ------------------------------------------------------------
        mode = str(tags.get("detail_lod_threshold_mode", "") or "")
        if mode == "fixed":
            thresh = float(tags.get("detail_lod_threshold", -1.25))
        else:
            # Use the same dmax the renderer used most recently (defaults to 0.75).
            # Use the same dmax the renderer used most recently (defaults to 0.75),
            # and include the renderer fade margin so instantiation doesn't lag behind visibility.
            dmax = float(getattr(game, "_attn_render_dmax", 0.75) or 0.75)
            fade_w = float(getattr(cfg, "entity_lod_fade_w", 0.6) or 0.6)  # fallback; matches renderables default

            # Derive child render size the same way the renderer would:
            # prefer tags.abs_size if present, otherwise base_size.
            child_size = 1.0
            try:
                child_id = str(tags.get("detail_child", "") or "")
                if child_id:
                    child_proto = prototypes.resolve_proto(child_id)

                    # proto may be dict-like or object-like
                    ptags = {}
                    try:
                        ptags = (getattr(child_proto, "tags", None) or {})  # object style
                    except Exception:
                        ptags = {}
                    if not ptags:
                        try:
                            ptags = (child_proto.get("tags", None) or {})  # dict style
                        except Exception:
                            ptags = {}

                    abs_size_tag = None
                    try:
                        abs_size_tag = ptags.get("abs_size", None)
                    except Exception:
                        abs_size_tag = None

                    if abs_size_tag is not None:
                        child_size = float(abs_size_tag)
                    else:
                        # base_size fallback
                        try:
                            child_size = float(getattr(child_proto, "base_size", None) or 1.0)
                        except Exception:
                            try:
                                child_size = float(child_proto.get("base_size", 1.0) or 1.0)
                            except Exception:
                                child_size = 1.0
            except Exception:
                child_size = 1.0

            child_size = max(1e-9, float(child_size))
            child_lod = math.log2(child_size)

            # Instantiate whenever it could plausibly render in the same band as normal entities.
            thresh = child_lod + (dmax + fade_w)


        if float(cam_lod) > float(thresh):
            continue

        zc = tuple(getattr(r, "zone_coord", (0, 0, zz)))
        if len(zc) != 3:
            continue

        agg_id = str(getattr(ent, "id", "") or "")
        if not agg_id:
            continue

        # Allow combat.kill_actor to find the macro aggregate entity by id
        try:
            amap = getattr(game, "_attn_agg_id_to_ent", None)
            if not isinstance(amap, dict):
                amap = {}
                game._attn_agg_id_to_ent = amap
            amap[str(agg_id)] = ent
        except Exception:
            pass


        in_scope_aggs.add(agg_id)

        slot_to_eid = game._attn_active_agg_children.get(agg_id)
        if not isinstance(slot_to_eid, dict):
            slot_to_eid = {}
            game._attn_active_agg_children[agg_id] = slot_to_eid

        child_type = str(tags.get("detail_child_type", "entity") or "entity").lower().strip()
        is_harvestable = (str(kind) == "berry_patch") or (child_type == "actor")

        # Harvest persistence stored on the aggregate itself (truthy macro object)
        harvested = getattr(ent, "_agg_harvested_slots", None)
        if not isinstance(harvested, set):
            harvested = set()
            if is_harvestable:
                try:
                    setattr(ent, "_agg_harvested_slots", harvested)
                except Exception:
                    pass

        # If something removed a harvestable child from a *loaded zone* (pickup), record its slot harvested.
        # (This keeps existing gameplay interactions working while zones still exist locally.)
        try:
            level = game.get_zone_for_render(zc)
        except Exception:
            level = None




        # Harvest persistence (berries only): only treat "missing from level.entities" as pickup
        # if we KNOW this eid was previously mirrored into this loaded level.
        if is_harvestable and level is not None and child_type != "actor":
            try:
                mirrored = getattr(level, "_attn_mirrored_ids", None)
                if not isinstance(mirrored, set):
                    mirrored = set()
                    setattr(level, "_attn_mirrored_ids", mirrored)

                for s, eid in list(slot_to_eid.items()):
                    if (eid in mirrored) and (eid not in level.entities) and (eid in attn_store.entities):
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

            # If this is an actor slot and the zone is loaded, the loaded level is authoritative.
            # This prevents re-staging "graduated" actors and avoids invisible/ghost desync.
            if child_type == "actor" and level is not None:
                try:
                    if eid in level.actors:
                        if eid in attn_store.entities:
                            attn_store.despawn(eid)
                        slot_to_eid[slot] = eid
                        continue
                except Exception:
                    pass

            if eid in attn_store.entities:
                slot_to_eid[slot] = eid

                # If this slot is an ACTOR and we have a loaded level, promote it into simulation
                # instead of leaving it as a non-simulated render-only ghost.
                if child_type == "actor" and level is not None:
                    try:
                        # Ensure it exists in the level entity registry (some systems expect this)
                        if eid not in level.entities:
                            level.entities[eid] = attn_store.entities[eid]
                            level.spatial_dirty = True

                        # Register/promote into the actor system if not already there
                        if eid not in level.actors:
                            try:
                                # Prefer the canonical path if present
                                spawning_system.register_actor(game, level, attn_store.entities[eid], schedule_ai=True)
                            except Exception:
                                print("What an exception")
                                # Fallback: direct insertion if register_actor signature differs
                                level.actors[eid] = attn_store.entities[eid]
                                level.spatial_dirty = True

                        # Once promoted, remove from attention-store to avoid duplicate ownership
                        try:
                            attn_store.despawn(eid)
                        except Exception:
                            pass
                    except Exception:
                        pass

                    continue

                # Non-actor: mirror into loaded zone if present (enables pickup/look)
                if level is not None and eid not in level.entities:
                    try:
                        level.entities[eid] = attn_store.entities[eid]
                        level.spatial_dirty = True
                    except Exception:
                        pass

                continue


            child_type = str(tags.get("detail_child_type", "entity") or "entity").lower().strip()

            if child_type == "actor":
                # Deterministic actor child (e.g., ecology_controller -> wolves).
                # CRITICAL: actor id must be the deterministic slot eid, otherwise we spawn forever.

                # If already staged, we're done.
                if eid in attn_store.entities:
                    slot_to_eid[slot] = eid
                    # If this zone is loaded, ensure it is registered once for simulation,
                    # then remove from attn_store so zone rendering/simulation stays coherent for moving actors.
                    if level is not None:
                        try:
                            if eid not in level.actors:
                                spawning_system.register_actor(game, level, attn_store.entities[eid], schedule_ai=True)
                                level.spatial_dirty = True
                            try:
                                attn_store.despawn(eid)
                            except Exception:
                                pass
                        except Exception:
                            pass
                    continue

                # Build Actor from spec with deterministic id.
                try:
                    actor_spec = prototypes.resolve_proto(str(child_id))
                except Exception:
                    continue
                if not isinstance(actor_spec, dict) or not actor_spec:
                    continue

                try:
                    actor_obj = spawn_factory.build_actor_from_spec(
                        spec=actor_spec,
                        aid=eid,  # deterministic!
                        pos=(int(lx), int(ly)),
                        abs_pos=(int(ax), int(ay)),
                        overrides={
                            "tags": {
                                "from_aggregate": agg_id,
                                "aggregate_slot": int(slot),
                                "aggregate_kind": str(kind),
                            }
                        },
                    )
                except Exception:
                    continue

                # Stage into attention store (primary truth)
                try:
                    attn_store.stage(
                        actor_obj,
                        abs_x=ax,
                        abs_y=ay,
                        zz=zz,
                        lineage_id=f"{agg_id}:{child_id}:{slot}",
                    )
                except Exception:
                    continue

                # If this zone is loaded, register exactly once (schedules AI).
                if level is not None:
                    try:
                        if eid not in level.actors:
                            spawning_system.register_actor(game, level, actor_obj, schedule_ai=True)
                            # IMPORTANT: once an actor is simulating in a loaded zone, don't keep it in attn_store,
                            # otherwise attn_store bin staleness makes it "invisible" while still attacking.
                            try:
                                attn_store.despawn(eid)
                            except Exception:
                                pass

                            level.spatial_dirty = True
                    except Exception:
                        pass

                slot_to_eid[slot] = eid
                continue


            else:
                # Generic entity child (berry patch style)
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
                                "aggregate_kind": str(kind),
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
                        try:
                            mirrored = getattr(level, "_attn_mirrored_ids", None)
                            if not isinstance(mirrored, set):
                                mirrored = set()
                                setattr(level, "_attn_mirrored_ids", mirrored)
                            mirrored.add(eid)
                        except Exception:
                            pass
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

    
    # Shared actor helpers used by detail resolvers.
    #
    # We intentionally keep these outside any legacy kill-switch so modern
    # attention paths (site-detail resolution) never depend on dead code.
    def _build_staged_actor(
        *,
        eid: str,
        npc_id: str,
        name: str,
        glyph: str,
        color,
        abs_pos: tuple[int, int],
        local_pos: tuple[int, int],
        owner_id: str,
        ns,
    ):
        """Build a real staged actor used by attention detail passes."""
        spec = ns if isinstance(ns, dict) else {}
        spec_tags = (spec.get("tags") or {}) if isinstance(spec, dict) else {}

        # Special cases kept centralized for consistency with runtime NPC behavior.
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
            a.tags.update({"npc": True, "npc_id": npc_id, "owner_id": owner_id})
            a.tags["show_exact_hp"] = True
            try:
                a.show_exact_hp = True
            except Exception:
                pass
            desc = spec.get("description") or getattr(a, "description", None)
            if desc:
                a.description = desc
            try:
                a.regen_per_tick = (1, 10)
                game._start_regen(
                    game.get_zone_for_render((abs_pos[0] // zone_w, abs_pos[1] // zone_h, zz)) or game._level(),
                    a.id,
                    amount=1,
                    interval=10,
                )
            except Exception:
                pass
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
            a.tags.update({"npc": True, "npc_id": npc_id, "owner_id": owner_id})
            a.tags["merchant_id"] = spec_tags.get("merchant_id", "general_store")
            a.name = name
            try:
                a.glyph = glyph
                a.color = color  # type: ignore[assignment]
            except Exception:
                pass
            desc = spec.get("description") or getattr(a, "description", None)
            if desc:
                a.description = desc
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
                tags={"npc": True, "npc_id": npc_id, "owner_id": owner_id},
                disposition=int(spec_tags.get("base_disposition", 0) or 0),
                affiliations=tuple(spec_tags.get("factions", [])),
                glyph=glyph,
                color=color,  # type: ignore[arg-type]
            )
        except TypeError:
            a = Human(
                id=eid,
                name=name,
                pos=local_pos,
                faction="npc",
                stats=Stats(hp=50, max_hp=50),
                tags={"npc": True, "npc_id": npc_id, "owner_id": owner_id},
                disposition=int(spec_tags.get("base_disposition", 0) or 0),
                affiliations=tuple(spec_tags.get("factions", [])),
                glyph=glyph,
                color=color,  # type: ignore[arg-type]
            )
            try:
                a.abs_pos = abs_pos
            except Exception:
                pass

        desc = spec.get("description")
        if desc:
            a.description = desc
        return a

    def _mirror_actor_into_loaded_zone(eid: str, actor_obj, abs_pos: tuple[int, int]) -> None:
        """If actor's zone is loaded, mirror into level.actors/entities."""
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

    # -----------------------------
    # C) GENERIC RESOLVE RECIPES (sites + buildings + any future resolve-tagged parents)
    #
    # attention.py decides *when/where* to resolve; aggregate_resolution decides *what/how*.
    # -----------------------------
    try:
        if not hasattr(game, "_attn_active_resolved_children"):
            game._attn_active_resolved_children = {}  # parent_eid -> set[child_eid]

        in_scope_parents: set[str] = set()

        # Same size-derived threshold regime as player-ish actors
        dmax = float(getattr(game, "_attn_render_dmax", 0.75) or 0.75)
        fade_w = float(getattr(cfg, "entity_lod_fade_w", 0.6) or 0.6)
        actor_size = 1.0
        resolve_thresh = math.log2(max(1e-9, actor_size)) + (dmax + fade_w)

        if float(cam_lod) <= float(resolve_thresh):
            # Parents come from TWO sources:
            #  1) macro WIE refs (sites, aggregates, etc.)
            #  2) already-staged children in attn_store (e.g., buildings), so they can resolve further
            parents: list[tuple[object, tuple[int, int, int], tuple[int, int]]] = []

            # 1) WIE refs already computed earlier as `refs`
            for r in refs:
                ent = getattr(r, "ent", None)
                if ent is None:
                    continue
                tags = getattr(ent, "tags", {}) or {}
                if not isinstance(tags, dict) or "resolve" not in tags:
                    continue
                zc = tuple(getattr(r, "zone_coord", (0, 0, zz)))
                lp = tuple(getattr(r, "local_pos", (0, 0)))
                parents.append((ent, zc, lp))

            # 2) staged entities inside warm rect
            try:
                staged = attn_store.query_abs_rect((wx0, wy0, wx1, wy1), zz=zz)
                for ent, ax, ay in staged:
                    tags = getattr(ent, "tags", {}) or {}
                    if not isinstance(tags, dict) or "resolve" not in tags:
                        continue
                    # derive zc/lp from abs
                    zxx = int(ax) // int(zone_w)
                    zyy = int(ay) // int(zone_h)
                    lp = (int(ax) - zxx * int(zone_w), int(ay) - zyy * int(zone_h))
                    parents.append((ent, (zxx, zyy, zz), lp))
            except Exception:
                pass

            # Frontier-expanding loop: if a newly spawned child itself has "resolve",
            # process it as a parent within the SAME attention sync (site -> building -> walls).
            i = 0
            while i < len(parents):
                parent_ent, zc, lp = parents[i]
                i += 1

                parent_id = str(getattr(parent_ent, "id", "") or "")
                if not parent_id:
                    continue
                in_scope_parents.add(parent_id)

                active = game._attn_active_resolved_children.get(parent_id)
                if not isinstance(active, set):
                    active = set()
                    game._attn_active_resolved_children[parent_id] = active
                desired: set[str] = set()

                # Ask aggregate_resolution for deterministic spawn intents
                try:
                    intents = aggregate_system.resolve_spawn_intents_from_recipe(
                        game,
                        parent_ent=parent_ent,
                        zone_coord=tuple(map(int, zc)),
                        local_pos=tuple(map(int, lp)),
                        zone_w=int(zone_w),
                        zone_h=int(zone_h),
                        zz=int(zz),
                        max_depth=2,
                    )
                except Exception:
                    intents = []

                for intent in intents:
                    ax = int(intent.abs_x)
                    ay = int(intent.abs_y)

                    # Keep within warm rect
                    if ax < wx0 or ax >= wx1 or ay < wy0 or ay >= wy1:
                        continue

                    eid = str(intent.eid)
                    desired.add(eid)

                    # Already staged?
                    if eid in attn_store.entities:
                        # If actor and loaded zone, promote it (same pattern as aggregate actor slots)
                        if str(intent.child_type) == "actor":
                            zc2 = (ax // int(zone_w), ay // int(zone_h), int(zz))
                            level = game.get_zone_for_render(zc2)
                            if level is not None:
                                try:
                                    if eid not in level.actors:
                                        spawning_system.register_actor(
                                            game, level, attn_store.entities[eid], schedule_ai=True
                                        )
                                        level.spatial_dirty = True
                                    # Once simulating in zone, remove from attn_store to avoid ghosting
                                    try:
                                        attn_store.despawn(eid)
                                    except Exception:
                                        pass
                                except Exception:
                                    pass
                        else:
                            # Mirror non-actor into loaded zone if present
                            zc2 = (ax // int(zone_w), ay // int(zone_h), int(zz))
                            level = game.get_zone_for_render(zc2)
                            if level is not None and eid not in level.entities:
                                try:
                                    level.entities[eid] = attn_store.entities[eid]
                                    level.spatial_dirty = True
                                except Exception:
                                    pass
                        continue

                    # Not staged yet: build it
                    zxx = ax // int(zone_w)
                    zyy = ay // int(zone_h)
                    lx = ax - zxx * int(zone_w)
                    ly = ay - zyy * int(zone_h)

                    child_type = str(intent.child_type or "entity").lower().strip()

                    # Track the actual spawned object if we create one (so we can frontier-expand)
                    spawned_obj = None

                    if child_type == "staged" and intent.staged:
                        sd = intent.staged
                        obj = _YogaStagedEntity(
                            id=eid,
                            pos=(int(lx), int(ly)),
                            abs_pos=(int(ax), int(ay)),
                            kind=str(sd.get("kind", "structure") or "structure"),
                            glyph=str(sd.get("glyph", "#") or "#")[0],
                            color=tuple(sd.get("color", (140, 120, 100))),
                            base_size=float(sd.get("base_size", 1.0) or 1.0),
                            tags=dict(sd.get("tags", {}) or {}),
                        )
                        attn_store.stage(
                            obj,
                            abs_x=ax,
                            abs_y=ay,
                            zz=zz,
                            lineage_id=str(intent.lineage_id or eid),
                        )
                        # staged geometry is not a resolver-parent in Phase 1
                        continue

                    # Actor child: use existing staged actor helper
                    if child_type == "actor":
                        npc_id = str(intent.proto_id)

                        # Pull actor presentation + metadata from the prototype (entities.yaml),
                        # not from legacy npcs.NPC_DEFS.
                        try:
                            spec = prototypes.resolve_proto(str(npc_id))
                        except Exception:
                            spec = None
                        if not isinstance(spec, dict):
                            spec = {}

                        glyph = (spec.get("glyph") or "@")
                        name = (spec.get("name") or npc_id.replace("_", " ").title())
                        color = (spec.get("color") or (255, 255, 255))

                        a = _build_staged_actor(
                            eid=eid,
                            npc_id=npc_id,
                            name=str(name),
                            glyph=str(glyph)[0] if glyph else "@",
                            color=tuple(color) if isinstance(color, (list, tuple)) else (255, 255, 255),
                            abs_pos=(int(ax), int(ay)),
                            local_pos=(int(lx), int(ly)),
                            owner_id=parent_id,
                            ns=spec,
                        )
                        # carry tags
                        try:
                            a.tags = getattr(a, "tags", {}) or {}
                            if intent.tags:
                                a.tags.update(dict(intent.tags))
                        except Exception:
                            pass

                        spawned_obj = a
                        attn_store.stage(
                            a,
                            abs_x=ax,
                            abs_y=ay,
                            zz=zz,
                            lineage_id=str(intent.lineage_id or eid),
                        )

                        # Promote if loaded
                        level = game.get_zone_for_render((zxx, zyy, int(zz)))
                        if level is not None:
                            try:
                                if eid not in level.actors:
                                    spawning_system.register_actor(game, level, a, schedule_ai=True)
                                    level.spatial_dirty = True
                                try:
                                    attn_store.despawn(eid)
                                except Exception:
                                    pass
                            except Exception:
                                pass
                    else:
                        # Generic entity child
                        child_proto = prototypes.resolve_proto(str(intent.proto_id))
                        if not isinstance(child_proto, dict):
                            continue
                        obj = spawn_factory.build_entity_from_spec(
                            spec=child_proto,
                            eid=eid,
                            pos=(int(lx), int(ly)),
                            abs_pos=(int(ax), int(ay)),
                            overrides={"tags": dict(intent.tags or {})},
                        )
                        spawned_obj = obj
                        attn_store.stage(
                            obj,
                            abs_x=ax,
                            abs_y=ay,
                            zz=zz,
                            lineage_id=str(intent.lineage_id or eid),
                        )

                        # Mirror if loaded
                        level = game.get_zone_for_render((zxx, zyy, int(zz)))
                        if level is not None:
                            try:
                                level.entities[eid] = obj
                                level.spatial_dirty = True
                            except Exception:
                                pass

                    # Frontier-expand: if the spawned child can itself resolve, process it this sync.
                    if spawned_obj is not None:
                        try:
                            stags = getattr(spawned_obj, "tags", {}) or {}
                        except Exception:
                            stags = {}
                        if isinstance(stags, dict) and "resolve" in stags:
                            try:
                                zpp = (int(ax) // int(zone_w), int(ay) // int(zone_h), int(zz))
                                lpp = (int(ax) - zpp[0] * int(zone_w), int(ay) - zpp[1] * int(zone_h))
                                parents.append((spawned_obj, zpp, lpp))
                            except Exception:
                                pass

                # Evict no-longer-desired children
                for ceid in list(active):
                    if ceid in desired:
                        continue
                    try:
                        obj = attn_store.entities.get(ceid)
                        ap = getattr(obj, "abs_pos", None) if obj is not None else None
                        if ap:
                            zc2 = (int(ap[0]) // int(zone_w), int(ap[1]) // int(zone_h), int(zz))
                            lvl = game.get_zone_for_render(zc2)
                            if lvl is not None:
                                lvl.entities.pop(ceid, None)
                                lvl.actors.pop(ceid, None)
                                lvl.spatial_dirty = True
                        attn_store.despawn(ceid)
                    except Exception:
                        pass
                    active.discard(ceid)

                for ceid in desired:
                    active.add(ceid)

        # Evict parents that left scope entirely
        for parent_id, active in list(getattr(game, "_attn_active_resolved_children", {}).items()):
            if parent_id in in_scope_parents:
                continue
            if isinstance(active, set):
                for ceid in list(active):
                    try:
                        attn_store.despawn(ceid)
                    except Exception:
                        pass
            del game._attn_active_resolved_children[parent_id]

    except Exception:
        # Never let resolve-recipe logic break the rest of attention.
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
    Rect is half-open: [x0,x1) Ãƒâ€” [y0,y1).

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

            # Keep attention instantiation thresholds consistent with the renderer LoD band.
            try:
                game._attn_render_dmax = float(dmax)
            except Exception:
                pass

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

    # Reuse a single handle to attention store throughout this function.
    attn_store = getattr(game, "attn_store", None)
    attn_ids = set(getattr(attn_store, "entities", {}) or {}) if attn_store is not None else set()


    # Camera center for scoring (raw camera center, not warm center)
    ccx = 0.5 * (ax0 + ax1)
    ccy = 0.5 * (ay0 + ay1)

    def _score(abs_size: float, abs_x: float, abs_y: float) -> float:
        dx = abs_x - ccx
        dy = abs_y - ccy
        return abs_size * 1000.0 - (dx * dx + dy * dy)

    # ------------------------------------------------------------
    # 1) WORLD INDEX ENTITIES (POIs and other world-markers)
    # NOTE: Sites are now placed directly into WorldEntityIndex upstream (site_placement.py).
    # ------------------------------------------------------------


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
            kinds=None,
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

                    # YOGA: If an object is managed by attention (staged into attn_store),
                    # do NOT also render it from the loaded-zone dictionaries. Zone load
                    # must not change LoD bands or visibility.
                    if obj_id in attn_ids:
                        continue

                    obj = None
                    if include_actors:
                        obj = level.actors.get(obj_id)
                    if obj is None and include_entities:
                        obj = level.entities.get(obj_id)
                    if obj is None:
                        continue

                    # Suppress legacy stamped *site/POI* children so attention remains the sole truth source for those.
                    # DO NOT suppress aggregate children here: they may have "graduated" into the loaded zone for simulation.
                    try:
                        _tags = getattr(obj, "tags", {}) or {}
                        if _tags.get("site_npc") or _tags.get("poi_npc"):
                            continue
                    except Exception:
                        pass


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
    """DEPRECATED (Yoga): do not stamp aggregate children into zones on entry.

    Aggregate detail is now attention-driven and staged via AttnStore so that
    god-vision / camera observation is sufficient to realize entities anywhere.

    Zone entry must not create a parallel population (duplication + perf cliffs).

    If, in the future, we want remote attention-staged actors to *simulate* while their
    zone is loaded, we should register/import the already-staged entities by stable eid/lineage.
    """
    return
