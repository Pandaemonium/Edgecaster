from __future__ import annotations

from types import SimpleNamespace

from edgecaster import prototypes
from edgecaster.systems import aggregate_resolution as ar
from edgecaster.systems.world_entity_index import WorldEntityIndex


def _dummy_game(seed: int = 1234) -> object:
    return SimpleNamespace(fractal_seed=int(seed), cfg=SimpleNamespace(seed=int(seed)))


def test_build_lineage_id_compose_segments() -> None:
    lid = ar.build_lineage_id("root:a", "child", "", 3, None, "leaf")
    assert lid == "root:a:child:3:leaf"


def test_lineage_root_prefers_explicit_tag() -> None:
    ent = SimpleNamespace(id="runtime_id_1", tags={"lineage_id": "site:start/building:inn:0"})
    assert ar.lineage_root_for_entity(ent) == "site:start/building:inn:0"


def test_aggregate_slot_lineage_id_format() -> None:
    lid = ar.aggregate_slot_lineage_id("agg:berries:1,2,0:0", "blueberry", 7)
    assert lid == "agg:berries:1,2,0:0:blueberry:7"


def test_entity_id_from_lineage_is_deterministic_and_separate() -> None:
    lid = "site:starttsgard@10,10/building:inn:0:child:npcs:merchant:0"
    eid_a = ar.entity_id_from_lineage(lid)
    eid_b = ar.entity_id_from_lineage(lid)
    assert eid_a == eid_b
    assert eid_a != lid
    assert eid_a.startswith("world:")


def test_unique_world_root_lineage_id_is_deterministic_and_separate() -> None:
    lid_a = ar.unique_world_root_lineage_id("world_continent", 0)
    lid_b = ar.unique_world_root_lineage_id("world_continent", 0)
    assert lid_a == lid_b
    assert lid_a == "world_root:world_continent:0"
    assert lid_a != "agg:world_continent:root:0"


def test_resolve_spawn_intents_use_parent_lineage_root() -> None:
    game = _dummy_game(777)
    parent = SimpleNamespace(
        id="runtime_parent_42",
        tags={
            "lineage_id": "site:starttsgard@10,10/building:inn:0",
            "resolve": [
                {"kind": "geom_rect", "w": 5, "h": 5},
                {
                    "kind": "children_fixed",
                    "children": ["merchant"],
                    "placement": {"pattern": "scatter_interior", "salt": "npcs"},
                },
            ],
        },
    )

    intents = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(10, 10),
        zone_w=64,
        zone_h=64,
        zz=0,
    )

    merchant = [i for i in intents if i.proto_id == "merchant"]
    assert merchant
    child = merchant[0]
    assert str(child.lineage_id).startswith("site:starttsgard@10,10/building:inn:0:child:npcs:merchant:")
    assert child.eid == ar.entity_id_from_lineage(child.lineage_id)


def test_resolve_spawn_intents_deterministic_same_seed() -> None:
    game = _dummy_game(111)
    parent = SimpleNamespace(
        id="runtime_parent_99",
        tags={
            "lineage_id": "site:test@4,4/building:bazaar:0",
            "resolve": [
                {"kind": "geom_rect", "w": 7, "h": 7},
                {
                    "kind": "children_pool",
                    "pool": ["sage_cap", "vital_belt"],
                    "count": 5,
                    "placement": {"pattern": "scatter_interior", "salt": "shelves"},
                },
            ],
        },
    )

    intents_a = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(20, 20),
        zone_w=64,
        zone_h=64,
        zz=0,
    )
    intents_b = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(20, 20),
        zone_w=64,
        zone_h=64,
        zz=0,
    )

    def _sig(xs):
        return [(i.eid, i.proto_id, i.abs_x, i.abs_y, i.child_type, i.lineage_id) for i in xs]

    assert _sig(intents_a) == _sig(intents_b)


def test_children_fixed_spawn_kind_from_placement_overrides_proto() -> None:
    game = _dummy_game(2026)
    parent = SimpleNamespace(
        id="runtime_parent_spawn_kind",
        tags={
            "lineage_id": "site:test/building:odd:0",
            "resolve": [
                {"kind": "geom_rect", "w": 5, "h": 5},
                {
                    "kind": "children_fixed",
                    "children": ["wall"],
                    "placement": {
                        "pattern": "scatter_interior",
                        "salt": "forced_kind",
                        "spawn_kind": "actor",
                    },
                },
            ],
        },
    )

    intents = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(10, 10),
        zone_w=64,
        zone_h=64,
        zz=0,
    )
    wall_children = [
        i for i in intents
        if i.proto_id == "wall" and ":child:forced_kind:wall:" in str(i.lineage_id or "")
    ]
    assert wall_children
    assert all(i.child_type == "actor" for i in wall_children)


def test_children_fixed_actor_inference_without_hardcoded_id_list() -> None:
    game = _dummy_game(77)
    parent = SimpleNamespace(
        id="runtime_parent_actor_infer",
        tags={
            "lineage_id": "site:test/building:bazaar:0",
            "resolve": [
                {"kind": "geom_rect", "w": 5, "h": 5},
                {
                    "kind": "children_fixed",
                    "children": ["caged_demon"],
                    "placement": {"pattern": "scatter_interior", "salt": "actor_infer"},
                },
            ],
        },
    )

    intents = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(14, 14),
        zone_w=64,
        zone_h=64,
        zz=0,
    )
    inferred = [i for i in intents if i.proto_id == "caged_demon"]
    assert inferred
    assert inferred[0].child_type == "actor"


def test_unique_world_root_continent_spawns_once() -> None:
    prototypes.clear_proto_caches()
    game = _dummy_game(4242)
    game.world_entity_index = WorldEntityIndex(zone_w=64, zone_h=64)
    game._agg_worldgen_done = set()
    game.cfg = SimpleNamespace(
        seed=4242,
        world_map_screens=128,
        world_width=64,
        world_height=40,
    )

    ar.ensure_world_aggregates(
        game,
        zone_w=64,
        zone_h=64,
        zx0=0,
        zx1=6,
        zy0=0,
        zy1=6,
        zz=0,
        kinds=("world_continent",),
    )
    ar.ensure_world_aggregates(
        game,
        zone_w=64,
        zone_h=64,
        zx0=80,
        zx1=86,
        zy0=80,
        zy1=86,
        zz=0,
        kinds=("world_continent",),
    )

    refs = game.world_entity_index.query_abs_rect((0.0, 0.0, 20000.0, 20000.0), z=0)
    continents = []
    for r in refs:
        ent = getattr(r, "ent", None)
        if ent is None:
            continue
        tags = getattr(ent, "tags", {}) or {}
        if isinstance(tags, dict) and str(tags.get("aggregate_kind", "")) == "world_continent":
            continents.append(ent)
    ids = {str(getattr(e, "id", "")) for e in continents}
    assert ids == {"agg:world_continent:root:0"}
    assert len(continents) == 1
    continent_tags = getattr(continents[0], "tags", {}) or {}
    assert continent_tags.get("lineage_id") == ar.unique_world_root_lineage_id("world_continent", 0)
    assert continent_tags.get("lineage_id") != getattr(continents[0], "id", "")


def test_children_scaled_is_deterministic_and_honors_count_bounds() -> None:
    game = _dummy_game(9090)
    parent = SimpleNamespace(
        id="runtime_parent_scaled",
        tags={
            "lineage_id": "world:region:alpha",
            "abs_size": 220,
            "resolve": [
                {
                    "kind": "children_scaled",
                    "children": ["world_city", "world_neighborhood"],
                    "count": {"base": 5, "min": 4, "max": 7, "jitter": 1},
                    "placement": {"pattern": "cluster", "radius": 40, "salt": "scaled_test"},
                }
            ],
        },
    )

    intents_a = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(32, 24),
        zone_w=64,
        zone_h=64,
        zz=0,
    )
    intents_b = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(32, 24),
        zone_w=64,
        zone_h=64,
        zz=0,
    )

    def _sig(xs):
        return [(i.eid, i.proto_id, i.abs_x, i.abs_y, i.child_type, i.lineage_id) for i in xs]

    assert _sig(intents_a) == _sig(intents_b)
    assert 4 <= len(intents_a) <= 7
    assert {i.proto_id for i in intents_a}.issubset({"world_city", "world_neighborhood"})


def test_children_scaled_per_abs_size_influences_count() -> None:
    game = _dummy_game(77)
    parent = SimpleNamespace(
        id="runtime_parent_scaled_abs",
        tags={
            "lineage_id": "world:city:beta",
            "abs_size": 300,
            "resolve": [
                {
                    "kind": "children_scaled",
                    "children": ["world_neighborhood"],
                    "count": {"base": 1, "per_abs_size": 0.01, "min": 1, "max": 8, "jitter": 0},
                    "placement": {"pattern": "cluster", "radius": 50, "salt": "scaled_abs"},
                }
            ],
        },
    )

    intents = ar.resolve_spawn_intents_from_recipe(
        game,
        parent_ent=parent,
        zone_coord=(0, 0, 0),
        local_pos=(30, 30),
        zone_w=64,
        zone_h=64,
        zz=0,
    )

    # base + round(per_abs_size * abs_size) = 1 + round(0.01 * 300) = 4
    assert len(intents) == 4
    assert all(i.proto_id == "world_neighborhood" for i in intents)
