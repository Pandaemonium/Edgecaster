from __future__ import annotations

from types import SimpleNamespace

from edgecaster.state.entities import Entity
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.systems import attention
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems import entity_lifecycle as entity_lifecycle_system


class _DummyGame:
    def __init__(self) -> None:
        self.cfg = SimpleNamespace(world_width=64, world_height=64, seed=123)
        self.entity_graph = EntityGraphStore()
        self.entity_state: dict[str, dict] = {}
        self.attn_store = attention.AttentionCellStore(bin_size=16)
        self.levels: dict[tuple[int, int, int], object] = {}
        self._attn_active_resolved_children: dict[str, set[str]] = {}

    def patch_entity_state(self, entity_or_id, patch=None, *, lineage_id=None, **fields) -> None:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if isinstance(patch, dict):
            state.update(dict(patch))
        if fields:
            state.update(dict(fields))
        if lineage_id:
            state["lineage_id"] = str(lineage_id)
        self.entity_state[key] = state

    def get_effective_entity_state(self, entity_or_id, *, lineage_id=None) -> dict:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if lineage_id:
            fallback = self.entity_state.get(str(lineage_id), {})
            if isinstance(fallback, dict):
                merged = dict(fallback)
                merged.update(state)
                return merged
        return state

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
    assert game._attn_active_resolved_children[parent.id] == set(child_ids_a)

    child_id = child_ids_a[0]
    child = entity_lifecycle_system.find_runtime_entity(game, child_id)
    assert child is not None
    child.tags["door_state"] = "open"
    child.glyph = "/"
    child.blocks_movement = False

    entity_lifecycle_system.collapse_entity(game, parent.id)

    assert game.entity_graph.get_node(parent.id).lod_state == "collapsed"
    assert game.entity_graph.get_node(child_id) is None
    assert child_id not in game.attn_store.entities
    state = game.entity_state[child_id]
    assert state["last_known_tags"]["door_state"] == "open"
    assert state["last_known_glyph"] == "/"
    assert state["last_known_blocks_movement"] is False

    entity_lifecycle_system.collapse_entity(game, parent.id)
    assert game.entity_graph.get_node(parent.id).lod_state == "collapsed"
