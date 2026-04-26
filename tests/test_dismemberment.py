"""Tests for edgecaster/systems/dismemberment.py"""
from __future__ import annotations

import random
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import patch

from edgecaster.state.entities import Entity
from edgecaster.state.entity_graph import EntityGraphStore
from edgecaster.state import chakra_component as chakra_component_state
from edgecaster.systems import dismemberment as dismemberment_system
from edgecaster.systems import entity_graph_ops as entity_graph_ops_system
from edgecaster.systems.spatial_index import SpatialIndex


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

class _DummyGame:
    def __init__(self) -> None:
        self.cfg = SimpleNamespace(world_width=64, world_height=64, seed=42)
        self.entity_graph = EntityGraphStore()
        self.entity_state: Dict[str, dict] = {}
        self.spatial_index = SpatialIndex(bin_size=16)
        self.attn_store = None
        self.levels: dict = {}
        self._expanded_entity_children: Dict[str, set] = {}
        self.rng = random.Random(42)
        self.player_id = "player:1"
        self._log: list = []

    def patch_entity_state(self, entity_or_id, patch=None, **fields) -> None:
        key = str(entity_or_id)
        state = dict(self.entity_state.get(key, {}) or {})
        if isinstance(patch, dict):
            state.update(dict(patch))
        if fields:
            state.update(dict(fields))
        self.entity_state[key] = state

    def get_effective_entity_state(self, entity_or_id) -> dict:
        return dict(self.entity_state.get(str(entity_or_id), {}) or {})

    def get_zone_for_render(self, coord):
        return None

    class log:
        @staticmethod
        def add(msg: str) -> None:
            pass


def _make_actor(entity_id: str, pos=(5, 5)) -> Any:
    """Build a minimal actor-like object with a ChakraComponent."""
    comp = chakra_component_state.ChakraComponent(
        root_node_id="body",
        nodes={
            "body": chakra_component_state.ChakraNode("body", kind="body_root", active=True),
            "arm.forearm.hand": chakra_component_state.ChakraNode(
                "arm.forearm.hand", kind="limb", active=True
            ),
        },
        edges={},
        tags={},
    )
    actor = SimpleNamespace(
        id=entity_id,
        entity_id=entity_id,
        name="Test Actor",
        pos=pos,
        abs_pos=pos,
        kind="actor",
        chakra_component=comp,
        tags={},
        faction="enemy",
        alive=True,
    )
    actor.stats = SimpleNamespace(hp=20, max_hp=20)
    return actor


def _make_body_node_entity(
    entity_id: str,
    *,
    body_full_id: str,
    body_node_proto_id: str,
    owner_id: str,
    pos=(5, 5),
) -> Entity:
    """Build an internal body-node entity as entity_lifecycle would create it."""
    return Entity(
        id=entity_id,
        entity_id=entity_id,
        name=body_node_proto_id.capitalize(),
        pos=pos,
        abs_pos=pos,
        glyph="@",
        kind="body_node",
        tags={
            "body_node": True,
            "internal_entity": True,
            "body_full_id": body_full_id,
            "body_node_proto_id": body_node_proto_id,
            "body_owner_id": owner_id,
        },
    )


def _register_body_node(game, defender_eid, body_node_ent):
    """Attach a body-node entity to defender in the graph."""
    entity_graph_ops_system.register_entity(
        game,
        body_node_ent,
        lod_state="expanded",
    )
    entity_graph_ops_system.attach_entity_to_parent(
        game,
        body_node_ent,
        defender_eid,
        socket_id="body",
    )
    # Store obj reference on the graph node so _find_runtime can locate it.
    node = game.entity_graph.get_node(body_node_ent.entity_id)
    if node is not None:
        node.obj = body_node_ent


def _make_level():
    return SimpleNamespace(entities={}, actors={}, coord=(0, 0, 0))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sever_body_part_removes_node_from_graph():
    """After severing, the body-node entity is no longer a body-socket child."""
    game = _DummyGame()
    level = _make_level()
    defender = _make_actor("actor:defender")
    entity_graph_ops_system.register_entity(game, defender, lod_state="expanded")

    hand_ent = _make_body_node_entity(
        "actor:defender:body:arm.forearm.hand",
        body_full_id="arm.forearm.hand",
        body_node_proto_id="hand",
        owner_id="actor:defender",
    )
    _register_body_node(game, "actor:defender", hand_ent)

    # Confirm it is attached before severing.
    assert "actor:defender:body:arm.forearm.hand" in game.entity_graph.get_children(
        "actor:defender", socket_id="body"
    )

    with patch.object(
        dismemberment_system, "_proto_is_dismemberable", return_value=True
    ):
        result = dismemberment_system.sever_body_part_by_node_id(
            game, level, defender, "arm.forearm.hand"
        )

    assert result is True
    assert "actor:defender:body:arm.forearm.hand" not in game.entity_graph.get_children(
        "actor:defender", socket_id="body"
    )


def test_sever_body_part_places_entity_in_level_entities():
    """After severing, the entity appears in level.entities at the defender's pos."""
    game = _DummyGame()
    level = _make_level()
    defender = _make_actor("actor:defender", pos=(10, 12))
    entity_graph_ops_system.register_entity(game, defender, lod_state="expanded")

    hand_ent = _make_body_node_entity(
        "actor:defender:body:arm.forearm.hand",
        body_full_id="arm.forearm.hand",
        body_node_proto_id="hand",
        owner_id="actor:defender",
        pos=(10, 12),
    )
    _register_body_node(game, "actor:defender", hand_ent)

    with patch.object(
        dismemberment_system, "_proto_is_dismemberable", return_value=True
    ):
        dismemberment_system.sever_body_part_by_node_id(
            game, level, defender, "arm.forearm.hand"
        )

    eid = "actor:defender:body:arm.forearm.hand"
    assert eid in level.entities
    dropped = level.entities[eid]
    assert dropped.tags.get("severed") is True
    assert not dropped.tags.get("internal_entity")
    assert dropped.tags.get("body_node") is False
    assert dropped.glyph == "%"
    assert dropped.kind == "item"
    assert dropped.pos == (10, 12)


def test_sever_body_part_updates_entity_name():
    """The severed entity gets a 'Severed <Part>' name."""
    game = _DummyGame()
    level = _make_level()
    defender = _make_actor("actor:defender")
    entity_graph_ops_system.register_entity(game, defender, lod_state="expanded")

    hand_ent = _make_body_node_entity(
        "actor:defender:body:arm.forearm.hand",
        body_full_id="arm.forearm.hand",
        body_node_proto_id="hand",
        owner_id="actor:defender",
    )
    hand_ent.name = "hand"
    _register_body_node(game, "actor:defender", hand_ent)

    with patch.object(
        dismemberment_system, "_proto_is_dismemberable", return_value=True
    ):
        dismemberment_system.sever_body_part_by_node_id(
            game, level, defender, "arm.forearm.hand"
        )

    assert level.entities["actor:defender:body:arm.forearm.hand"].name == "Severed Hand"


def test_attempt_dismember_respects_chance_below_threshold():
    """attempt_dismember fires when rng.random() < dismember_chance."""
    game = _DummyGame()
    level = _make_level()

    attacker = SimpleNamespace(
        id="actor:attacker",
        entity_id="actor:attacker",
        tags={"dismember_chance": 0.50},
        name="Attacker",
    )
    defender = _make_actor("actor:defender")
    entity_graph_ops_system.register_entity(game, defender, lod_state="expanded")

    hand_ent = _make_body_node_entity(
        "actor:defender:body:arm.forearm.hand",
        body_full_id="arm.forearm.hand",
        body_node_proto_id="hand",
        owner_id="actor:defender",
    )
    _register_body_node(game, "actor:defender", hand_ent)

    # Force rng to return a value below the 0.50 threshold.
    game.rng = SimpleNamespace(
        random=lambda: 0.10,
        choice=lambda seq: seq[0],
    )

    with patch.object(
        dismemberment_system, "_proto_is_dismemberable", return_value=True
    ):
        result = dismemberment_system.attempt_dismember(game, level, attacker, defender)

    assert result is True
    assert "actor:defender:body:arm.forearm.hand" in level.entities


def test_attempt_dismember_no_fire_above_threshold():
    """attempt_dismember does not fire when rng.random() >= dismember_chance."""
    game = _DummyGame()
    level = _make_level()

    attacker = SimpleNamespace(
        id="actor:attacker",
        entity_id="actor:attacker",
        tags={"dismember_chance": 0.50},
        name="Attacker",
    )
    defender = _make_actor("actor:defender")
    entity_graph_ops_system.register_entity(game, defender, lod_state="expanded")

    hand_ent = _make_body_node_entity(
        "actor:defender:body:arm.forearm.hand",
        body_full_id="arm.forearm.hand",
        body_node_proto_id="hand",
        owner_id="actor:defender",
    )
    _register_body_node(game, "actor:defender", hand_ent)

    # Force rng above the threshold.
    game.rng = SimpleNamespace(random=lambda: 0.75)

    with patch.object(
        dismemberment_system, "_proto_is_dismemberable", return_value=True
    ):
        result = dismemberment_system.attempt_dismember(game, level, attacker, defender)

    assert result is False
    assert "actor:defender:body:arm.forearm.hand" not in level.entities


def test_already_severed_node_not_selected():
    """A body node tagged severed=True is excluded from candidates."""
    game = _DummyGame()
    level = _make_level()
    defender = _make_actor("actor:defender")
    entity_graph_ops_system.register_entity(game, defender, lod_state="expanded")

    hand_ent = _make_body_node_entity(
        "actor:defender:body:arm.forearm.hand",
        body_full_id="arm.forearm.hand",
        body_node_proto_id="hand",
        owner_id="actor:defender",
    )
    hand_ent.tags["severed"] = True  # already severed
    _register_body_node(game, "actor:defender", hand_ent)

    game.rng = SimpleNamespace(random=lambda: 0.0, choice=lambda seq: seq[0])

    with patch.object(
        dismemberment_system, "_proto_is_dismemberable", return_value=True
    ):
        result = dismemberment_system.sever_random_body_part(game, level, defender)

    assert result is False


def test_attempt_dismember_no_chance_no_sever():
    """attempt_dismember returns False immediately when dismember_chance is 0."""
    game = _DummyGame()
    level = _make_level()

    attacker = SimpleNamespace(
        id="actor:attacker",
        entity_id="actor:attacker",
        tags={},  # no dismember_chance
        name="Attacker",
    )
    defender = _make_actor("actor:defender")

    result = dismemberment_system.attempt_dismember(game, level, attacker, defender)
    assert result is False
