from __future__ import annotations

from types import SimpleNamespace

from edgecaster.systems.attention import AttentionCellStore
from edgecaster.systems import attention as attention_system
from edgecaster.systems.spatial_index import SpatialIndex
from edgecaster.systems.poi_registry import POIRegistry
from edgecaster.systems.world_entity_index import WorldEntityIndex
from edgecaster.state.pois import ABSRect, POISpec, StructureSpec


def test_spatial_index_add_and_get() -> None:
    idx = SpatialIndex()
    obj = SimpleNamespace(id="test_1", kind="actor")
    idx.add_or_update(obj, (0.0, 0.0, 10.0, 10.0), 0, "realized")

    entry = idx.get("test_1")
    assert entry is not None
    assert entry.entity_id == "test_1"
    assert entry.rect == (0.0, 0.0, 10.0, 10.0)
    assert entry.zz == 0
    assert entry.realization_state == "realized"


def test_spatial_index_query_rect() -> None:
    idx = SpatialIndex(bin_size=10)
    obj1 = SimpleNamespace(id="e1")
    idx.add_or_update(obj1, (5.0, 5.0, 15.0, 15.0), 0, "staged")

    obj2 = SimpleNamespace(id="e2")
    idx.add_or_update(obj2, (20.0, 20.0, 30.0, 30.0), 0, "staged")

    # Overlaps obj1 but not obj2
    res = idx.query_rect((0.0, 0.0, 6.0, 6.0), 0)
    assert len(res) == 1
    assert res[0].entity_id == "e1"


def test_spatial_index_semantic_and_kind_queries() -> None:
    idx = SpatialIndex()
    obj = SimpleNamespace(id="e1", kind="goblin", semantic_id="boss_1")
    idx.add_or_update(obj, (0.0, 0.0, 1.0, 1.0), 0, "realized")

    assert len(idx.query_semantic_id("boss_1")) == 1
    assert len(idx.query_kind("goblin")) == 1
    assert len(idx.query_kind("troll")) == 0


def test_spatial_index_query_rect_filters_by_realization_source_and_kind() -> None:
    idx = SpatialIndex(bin_size=8)
    proxy = SimpleNamespace(id="site_1", kind="site")
    staged = SimpleNamespace(id="npc_1", kind="npc")
    realized = SimpleNamespace(id="actor_1", kind="actor")

    idx.add_or_update(proxy, (1.0, 1.0, 2.0, 2.0), 0, "proxy", source="world_entity_index")
    idx.add_or_update(staged, (2.0, 1.0, 3.0, 2.0), 0, "staged", source="attention")
    idx.add_or_update(realized, (3.0, 1.0, 4.0, 2.0), 0, "realized")

    assert [entry.entity_id for entry in idx.query_rect((0.0, 0.0, 5.0, 5.0), 0, realization_state="staged")] == ["npc_1"]
    assert [entry.entity_id for entry in idx.query_rect((0.0, 0.0, 5.0, 5.0), 0, source="world_entity_index")] == ["site_1"]
    assert [entry.entity_id for entry in idx.query_rect((0.0, 0.0, 5.0, 5.0), 0, kind="actor")] == ["actor_1"]
    assert {
        entry.entity_id
        for entry in idx.query_rect((0.0, 0.0, 5.0, 5.0), 0, realization_state=("proxy", "staged"))
    } == {"site_1", "npc_1"}


def test_spatial_index_query_tag_uses_named_api_for_tagged_entries() -> None:
    idx = SpatialIndex(bin_size=8)
    starttsgard = SimpleNamespace(id="site_starttsgard", kind="site", tags={"site_kind": "starttsgard"})
    academy = SimpleNamespace(id="site_academy", kind="site", tags={"site_kind": "academy"})
    npc = SimpleNamespace(id="npc_1", kind="npc", tags={"site_kind": "starttsgard"})

    idx.add_or_update(starttsgard, (1.0, 1.0, 2.0, 2.0), 0, "collapsed")
    idx.add_or_update(academy, (2.0, 1.0, 3.0, 2.0), 0, "collapsed")
    idx.add_or_update(npc, (3.0, 1.0, 4.0, 2.0), 0, "staged")

    assert {
        entry.entity_id
        for entry in idx.query_tag("site_kind", "starttsgard")
    } == {"site_starttsgard", "npc_1"}
    assert [
        entry.entity_id
        for entry in idx.query_tag("site_kind", "starttsgard", kind="site")
    ] == ["site_starttsgard"]
    assert [
        entry.entity_id
        for entry in idx.query_tag("site_kind", "starttsgard", realization_state="staged")
    ] == ["npc_1"]


def test_spatial_index_remove() -> None:
    idx = SpatialIndex()
    obj = SimpleNamespace(id="e1", kind="goblin", semantic_id="boss_1")
    idx.add_or_update(obj, (0.0, 0.0, 1.0, 1.0), 0, "realized")

    assert idx.remove("e1") is True
    assert idx.get("e1") is None
    assert len(idx.query_semantic_id("boss_1")) == 0
    assert len(idx.query_rect((0.0, 0.0, 10.0, 10.0), 0)) == 0


def test_spatial_index_source_guard_prevents_proxy_downgrade() -> None:
    idx = SpatialIndex()
    staged = SimpleNamespace(id="e1", kind="item")
    proxy = SimpleNamespace(id="e1", kind="site")

    idx.add_or_update(staged, (1.0, 1.0, 2.0, 2.0), 0, "staged", source="attention")
    idx.add_or_update(proxy, (10.0, 10.0, 11.0, 11.0), 0, "proxy", source="world_entity_index")

    entry = idx.get("e1")
    assert entry is not None
    assert entry.obj is staged
    assert entry.realization_state == "staged"
    assert idx.remove("e1", source="world_entity_index") is False
    assert idx.get("e1") is entry


def test_attention_store_mirrors_staged_entities_to_spatial_index() -> None:
    idx = SpatialIndex(bin_size=8)
    store = AttentionCellStore(bin_size=8, spatial_index=idx)
    obj = SimpleNamespace(id="berry_1", kind="item", semantic_id="berry_patch:1")

    store.stage(obj, abs_x=4, abs_y=5, zz=0)

    entry = idx.get("berry_1")
    assert entry is not None
    assert entry.realization_state == "staged"
    assert entry.source == "attention"
    assert entry.semantic_id == "berry_patch:1"
    assert idx.query_rect((4.0, 5.0, 5.0, 6.0), 0)[0].entity_id == "berry_1"

    store.despawn("berry_1")
    assert idx.get("berry_1") is None


def test_world_entity_index_mirrors_proxy_entities_to_spatial_index() -> None:
    idx = SpatialIndex(bin_size=8)
    world_index = WorldEntityIndex(zone_w=10, zone_h=10, spatial_index=idx)
    obj = SimpleNamespace(id="site_1", kind="site", semantic_id="starttsgard:market")

    world_index.add(obj, zone_coord=(2, 3, 0), local_pos=(4, 5))

    entry = idx.get("site_1")
    assert entry is not None
    assert entry.realization_state == "proxy"
    assert entry.source == "world_entity_index"
    assert obj.local_pos == (4, 5)
    assert entry.rect == (24.0, 35.0, 25.0, 36.0)
    assert idx.query_semantic_id("starttsgard:market")[0].entity_id == "site_1"

    world_index.clear()
    assert idx.get("site_1") is None


def test_poi_registry_mirrors_pois_to_spatial_index() -> None:
    idx = SpatialIndex(bin_size=8)
    registry = POIRegistry(zone_w=10, zone_h=10, spatial_index=idx)
    footprint = ABSRect.from_zone_coord(2, 3, 10, 10)
    poi = POISpec(
        id="poi_colosseum",
        kind="colosseum",
        name="Colosseum",
        footprint=footprint,
        depth=0,
        anchor_abs=footprint.center,
        structure_specs=[StructureSpec(kind="colosseum_arena")],
    )

    registry.add(poi)

    entry = idx.get("poi_colosseum")
    assert entry is not None
    assert entry.obj is poi
    assert entry.source == "poi_registry"
    assert entry.realization_state == "collapsed"
    assert entry.semantic_id == "poi_colosseum"
    assert entry.kind == "colosseum"
    assert entry.rect == (20.0, 30.0, 30.0, 40.0)

    assert registry.remove("poi_colosseum") is True
    assert idx.get("poi_colosseum") is None


def test_poi_registry_attach_spatial_index_mirrors_existing_pois() -> None:
    registry = POIRegistry(zone_w=10, zone_h=10)
    footprint = ABSRect.from_zone_coord(1, 1, 10, 10)
    registry.add(
        POISpec(
            id="legendary_lair_000",
            kind="legendary_lair",
            name="Lair",
            footprint=footprint,
            depth=0,
            anchor_abs=footprint.center,
            structure_specs=[StructureSpec(kind="legendary_lair")],
        )
    )
    idx = SpatialIndex(bin_size=8)

    registry.attach_spatial_index(idx)

    assert idx.get("legendary_lair_000") is not None
    assert idx.query_kind("legendary_lair")[0].entity_id == "legendary_lair_000"


def test_renderables_read_world_proxies_from_spatial_index_first() -> None:
    class _LegacyWorldIndex:
        def query_abs_rect(self, *_args, **_kwargs):
            raise AssertionError("SpatialIndex-backed render should not query WorldEntityIndex")

    spatial_index = SpatialIndex(bin_size=8)
    obj = SimpleNamespace(
        id="site_1",
        kind="site",
        abs_pos=(4, 5),
        zone_coord=(0, 0, 0),
        local_pos=(4, 5),
    )
    spatial_index.add_or_update(
        obj,
        (4.0, 5.0, 5.0, 6.0),
        0,
        "proxy",
        source="world_entity_index",
    )
    game = SimpleNamespace(
        cfg=SimpleNamespace(world_width=10, world_height=10, entity_render_pad_tiles=0.0),
        zone_coord=(0, 0, 0),
        spatial_index=spatial_index,
        world_entity_index=_LegacyWorldIndex(),
        attn_store=None,
        levels={},
        _attn_last_sig=(0.0, 0.0, 10.0, 10.0, 0.0, 0),
        _entity_active_band=0,
        _clamp_zone_window=lambda zx0, zx1, zy0, zy1, **_kwargs: (zx0, zx1, zy0, zy1, False),
        _ensure_world_aggregate_entities=lambda **_kwargs: None,
        _size_for_render=lambda _obj: 1.0,
        get_zone_for_render=lambda _coord: None,
    )

    renderables = attention_system.renderables_in_abs_rect(
        game,
        (0.0, 0.0, 10.0, 10.0),
        cam_lod=0.0,
        proxy_cls=SimpleNamespace,
    )

    assert len(renderables) == 1
    assert renderables[0].obj is obj
    assert renderables[0].abs_x == 4.0
    assert renderables[0].local_pos == (4, 5)


def test_renderables_read_collapsed_spatial_entry_without_legacy_stores() -> None:
    spatial_index = SpatialIndex(bin_size=8)
    obj = SimpleNamespace(
        id="city_1",
        kind="city",
        abs_pos=(4, 5),
        zone_coord=(0, 0, 0),
        local_pos=(4, 5),
    )
    spatial_index.add_or_update(
        obj,
        (4.0, 5.0, 5.0, 6.0),
        0,
        "collapsed",
    )
    game = SimpleNamespace(
        cfg=SimpleNamespace(world_width=10, world_height=10, entity_render_pad_tiles=0.0),
        zone_coord=(0, 0, 0),
        spatial_index=spatial_index,
        world_entity_index=None,
        attn_store=None,
        levels={},
        _attn_last_sig=(0.0, 0.0, 10.0, 10.0, 0.0, 0),
        _entity_active_band=0,
        _clamp_zone_window=lambda zx0, zx1, zy0, zy1, **_kwargs: (zx0, zx1, zy0, zy1, False),
        _ensure_world_aggregate_entities=lambda **_kwargs: None,
        _size_for_render=lambda _obj: 1.0,
        get_zone_for_render=lambda _coord: None,
    )

    renderables = attention_system.renderables_in_abs_rect(
        game,
        (0.0, 0.0, 10.0, 10.0),
        cam_lod=0.0,
        proxy_cls=SimpleNamespace,
    )

    assert len(renderables) == 1
    assert renderables[0].obj is obj


def test_renderables_read_staged_entities_from_spatial_index_first() -> None:
    class _LegacyAttentionStore:
        entities = {"npc_1": object()}

        def query_abs_rect(self, *_args, **_kwargs):
            raise AssertionError("SpatialIndex-backed render should not query AttentionCellStore")

    spatial_index = SpatialIndex(bin_size=8)
    obj = SimpleNamespace(id="npc_1", kind="npc", abs_pos=(6, 7))
    spatial_index.add_or_update(
        obj,
        (6.0, 7.0, 7.0, 8.0),
        0,
        "staged",
        source="attention",
    )
    game = SimpleNamespace(
        cfg=SimpleNamespace(world_width=10, world_height=10, entity_render_pad_tiles=0.0),
        zone_coord=(0, 0, 0),
        spatial_index=spatial_index,
        world_entity_index=None,
        attn_store=_LegacyAttentionStore(),
        levels={},
        _attn_last_sig=(0.0, 0.0, 10.0, 10.0, 0.0, 0),
        _entity_active_band=0,
        _clamp_zone_window=lambda zx0, zx1, zy0, zy1, **_kwargs: (zx0, zx1, zy0, zy1, False),
        _ensure_world_aggregate_entities=lambda **_kwargs: None,
        _size_for_render=lambda _obj: 1.0,
        get_zone_for_render=lambda _coord: None,
    )

    renderables = attention_system.renderables_in_abs_rect(
        game,
        (0.0, 0.0, 10.0, 10.0),
        cam_lod=0.0,
        proxy_cls=SimpleNamespace,
    )

    assert len(renderables) == 1
    assert renderables[0].obj is obj
    assert renderables[0].abs_x == 6.0
    assert renderables[0].local_pos == (6, 7)


def test_renderables_read_staged_entity_without_attention_store() -> None:
    spatial_index = SpatialIndex(bin_size=8)
    obj = SimpleNamespace(id="npc_1", kind="npc", abs_pos=(6, 7))
    spatial_index.add_or_update(
        obj,
        (6.0, 7.0, 7.0, 8.0),
        0,
        "staged",
    )
    game = SimpleNamespace(
        cfg=SimpleNamespace(world_width=10, world_height=10, entity_render_pad_tiles=0.0),
        zone_coord=(0, 0, 0),
        spatial_index=spatial_index,
        world_entity_index=None,
        attn_store=None,
        levels={},
        _attn_last_sig=(0.0, 0.0, 10.0, 10.0, 0.0, 0),
        _entity_active_band=0,
        _clamp_zone_window=lambda zx0, zx1, zy0, zy1, **_kwargs: (zx0, zx1, zy0, zy1, False),
        _ensure_world_aggregate_entities=lambda **_kwargs: None,
        _size_for_render=lambda _obj: 1.0,
        get_zone_for_render=lambda _coord: None,
    )

    renderables = attention_system.renderables_in_abs_rect(
        game,
        (0.0, 0.0, 10.0, 10.0),
        cam_lod=0.0,
        proxy_cls=SimpleNamespace,
    )

    assert len(renderables) == 1
    assert renderables[0].obj is obj
