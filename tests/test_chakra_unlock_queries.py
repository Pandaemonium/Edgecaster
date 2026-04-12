from __future__ import annotations

from edgecaster import prototypes, spawn_factory
from edgecaster.systems.chakras import (
    ChakraState,
    check_resonance_bonuses,
    check_resonance_bonuses_from_active_nodes,
    list_unlockable_chakras,
    list_unlockable_chakras_for_entity,
)


def _make_actor():
    spec = prototypes.resolve_proto("human_base")
    actor = spawn_factory.build_actor_from_spec(
        spec=spec,
        aid="actor:test_unlocks",
        pos=(10, 10),
        abs_pos=(10, 10),
    )
    actor.zone_coord = (0, 0, 0)
    return actor


def test_entity_unlock_query_matches_legacy_body_schema_at_root_state() -> None:
    actor = _make_actor()
    chakra_state = ChakraState(unlocked={"body"}, active={"body"})

    legacy = list_unlockable_chakras(prototypes.resolve_body_schema(actor), chakra_state)
    runtime = list_unlockable_chakras_for_entity(actor, chakra_state)

    assert runtime == legacy


def test_entity_unlock_query_matches_legacy_body_schema_after_branch_unlock() -> None:
    actor = _make_actor()
    chakra_state = ChakraState(unlocked={"body", "arm"}, active={"body", "arm"})

    legacy = list_unlockable_chakras(prototypes.resolve_body_schema(actor), chakra_state)
    runtime = list_unlockable_chakras_for_entity(actor, chakra_state)

    assert runtime == legacy
    assert any(node_id.startswith("arm.") for node_id in runtime)


def test_resonance_helper_from_active_nodes_matches_compat_wrapper() -> None:
    active_nodes = {
        "body",
        "arm",
        "arm_m",
        "thumb",
        "index",
        "middle",
        "ring",
        "pinky",
    }
    chakra_state = ChakraState(unlocked=set(active_nodes), active=set(active_nodes))

    compat = check_resonance_bonuses({}, chakra_state)
    direct = check_resonance_bonuses_from_active_nodes(active_nodes)

    assert sorted(direct) == sorted(compat)
    assert "bilateral_arms" in direct
    assert "full_hand" in direct
    assert "centered" in direct
