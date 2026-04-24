from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from edgecaster.state.entities import Entity
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.systems import aggregate_resolution as aggregate_system
from edgecaster.systems import attention
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_lifecycle as entity_lifecycle_system
from edgecaster.systems.spatial_index import SpatialIndex


class _DummyGame:
    def __init__(self) -> None:
        self.cfg = SimpleNamespace(world_width=64, world_height=64, seed=123)
        self.entity_graph = EntityGraphStore()
        self.entity_state: dict[str, dict] = {}
        self.spatial_index = SpatialIndex(bin_size=16)
        self.attn_store = attention.AttentionCellStore(spatial_index=self.spatial_index)
        self.levels: dict[tuple[int, int, int], object] = {}
        self._expanded_entity_children: dict[str, set[str]] = {}

    def patch_entity_state(self, entity_or_id, patch=None, **fields) -> None:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if isinstance(patch, dict):
            state.update(dict(patch))
        if fields:
            state.update(dict(fields))
        self.entity_state[key] = state

    def get_effective_entity_state(self, entity_or_id) -> dict:
        key = str(entity_or_id)
        return dict(self.entity_state.get(key, {}) or {})

    def get_zone_for_render(self, coord):  # noqa: ANN001
        return None


def _resolver_parent() -> Entity:
    return Entity(
        id="site:test_parent",
        entity_id="site:test_parent",
        name="Test Parent",
        pos=(12, 12),
        abs_pos=(12, 12),
        kind="site",
        tags={
            "world_entity": True,
            "resolve": [
                {
                    "kind": "children_fixed",
                    "children": ["wall"],
                    "placement": {
                        "pattern": "cluster",
                        "radius": 2,
                        "salt": "resolver_child",
                    },
                }
            ],
        },
    )


def test_staged_merchant_actor_defers_stock_initialization() -> None:
    game = _DummyGame()

    base_actor = SimpleNamespace(tags={})
    with patch("edgecaster.systems.entity_lifecycle.enemy_factory.spawn_enemy", return_value=base_actor):
        actor = entity_lifecycle_system._build_staged_actor(
            game,
            eid="merchant:test",
            npc_id="merchant",
            name="Merchant",
            glyph="@",
            color=(255, 255, 255),
            abs_pos=(12, 12),
            local_pos=(12, 12),
            owner_id="site:test_parent",
            zz=0,
            spec={"tags": {"merchant_id": "general_store"}},
        )

    assert actor.tags["merchant_id"] == "general_store"
    assert actor.tags["merchant_initialization_deferred"] is True
    assert "merchant_initialized" not in actor.tags


def test_expand_and_collapse_are_idempotent_and_persist_snapshots() -> None:
    game = _DummyGame()
    parent = _resolver_parent()
    entity_graph_ops_system.register_entity(game, parent, lod_state="collapsed")
    game.attn_store.stage(parent, abs_x=12, abs_y=12, zz=0)

    child_ids_a = entity_lifecycle_system.expand_entity(game, parent.id)
    child_ids_b = entity_lifecycle_system.expand_entity(game, parent.id)

    assert child_ids_a == child_ids_b
    assert len(child_ids_a) == 1
    assert game.entity_graph.get_node(parent.id).lod_state == "expanded"
    assert set(game.entity_graph.get_children(parent.id, socket_id="resolve")) == set(child_ids_a)
    assert game._expanded_entity_children[parent.id] == set(child_ids_a)

    child_id = child_ids_a[0]
    child = entity_lifecycle_system.find_runtime_entity(game, child_id)
    assert child is not None
    child.tags["door_state"] = "open"
    child.glyph = "/"
    child.blocks_movement = False

    entity_lifecycle_system.collapse_entity(game, parent.id)

    assert game.entity_graph.get_node(parent.id).lod_state == "collapsed"
    assert game.entity_graph.get_node(child_id) is None
    assert game.attn_store.get(child_id) is None
    state = game.entity_state[child_id]
    assert state["tags_patch"]["door_state"] == "open"
    assert state["glyph"] == "/"
    assert state["blocks_movement"] is False

    entity_lifecycle_system.collapse_entity(game, parent.id)
    assert game.entity_graph.get_node(parent.id).lod_state == "collapsed"


def test_collapse_persists_resolved_child_state_only_by_entity_id() -> None:
    game = _DummyGame()
    parent = _resolver_parent()
    entity_graph_ops_system.register_entity(game, parent, lod_state="collapsed")
    game.attn_store.stage(parent, abs_x=12, abs_y=12, zz=0)

    intent = aggregate_system.SpawnIntent(
        eid="resolved:test_child",
        proto_id="wall",
        abs_x=13,
        abs_y=12,
        zz=0,
        child_type="staged",
        staged={
            "glyph": "+",
            "kind": "structure",
            "tags": {"blocks_movement": True},
        },
        lineage_id="legacy:lineage:test_child",
    )

    with patch(
        "edgecaster.systems.entity_lifecycle.aggregate_system.resolve_spawn_intents_from_recipe",
        return_value=[intent],
    ):
        child_ids = entity_lifecycle_system.expand_entity(game, parent.id)

    assert child_ids == ["resolved:test_child"]
    child = entity_lifecycle_system.find_runtime_entity(game, "resolved:test_child")
    assert child is not None
    assert child.tags.get("lineage_id") == "legacy:lineage:test_child"

    child.tags["door_state"] = "open"
    child.glyph = "/"
    child.blocks_movement = False

    entity_lifecycle_system.collapse_entity(game, parent.id)

    assert "resolved:test_child" in game.entity_state
    assert "legacy:lineage:test_child" not in game.entity_state
    assert game.entity_state["resolved:test_child"]["tags_patch"]["door_state"] == "open"
