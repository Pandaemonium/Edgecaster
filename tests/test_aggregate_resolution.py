from __future__ import annotations

from types import SimpleNamespace

from edgecaster.systems import aggregate_resolution as ar


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
    assert child.eid.startswith("site:starttsgard@10,10/building:inn:0:child:npcs:merchant:")
    assert child.lineage_id == child.eid


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
        if i.proto_id == "wall" and ":child:forced_kind:wall:" in i.eid
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
