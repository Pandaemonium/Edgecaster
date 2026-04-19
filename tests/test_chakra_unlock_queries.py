from __future__ import annotations

from edgecaster import prototypes, spawn_factory
from edgecaster.systems.chakras import (
    ChakraState,
    can_unlock_chakra_for_entity,
    check_resonance_bonuses,
    check_resonance_bonuses_from_active_nodes,
    list_unlockable_chakras_for_entity,
    list_unlockable_chakras_for_entity_from_unlocked,
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


def test_entity_unlock_query_from_unlocked_matches_chakra_state_variant() -> None:
    actor = _make_actor()
    chakra_state = ChakraState(unlocked={"body", "arm"}, active={"body", "arm"})

    runtime = list_unlockable_chakras_for_entity(actor, chakra_state)
    thinner = list_unlockable_chakras_for_entity_from_unlocked(actor, chakra_state.unlocked)

    assert thinner == runtime


def test_can_unlock_chakra_for_entity_accepts_unlockable_node() -> None:
    actor = _make_actor()
    chakra_state = ChakraState(unlocked={"body"}, active={"body"})

    unlockable = list_unlockable_chakras_for_entity(actor, chakra_state)
    assert unlockable, "expected at least one unlockable node at root state"

    for node_id in unlockable:
        assert can_unlock_chakra_for_entity(actor, chakra_state, node_id), (
            f"can_unlock_chakra_for_entity should accept {node_id!r} "
            "which list_unlockable_chakras_for_entity already returned"
        )


def test_can_unlock_chakra_for_entity_rejects_already_unlocked_node() -> None:
    actor = _make_actor()
    chakra_state = ChakraState(unlocked={"body"}, active={"body"})

    assert not can_unlock_chakra_for_entity(actor, chakra_state, "body"), (
        "body is already unlocked; entity check must return False"
    )


def test_can_unlock_chakra_for_entity_rejects_unknown_node() -> None:
    actor = _make_actor()
    chakra_state = ChakraState(unlocked={"body"}, active={"body"})

    assert not can_unlock_chakra_for_entity(actor, chakra_state, "nonexistent_node_xyz")


def test_can_unlock_chakra_for_entity_rejects_locked_prereq() -> None:
    """Nodes whose dot-prefix ancestor is not yet unlocked must return False."""
    actor = _make_actor()
    # Only body unlocked — "arm" is NOT yet unlocked.
    base_state = ChakraState(unlocked={"body"}, active={"body"})

    # Ask what becomes unlockable once "arm" is granted.
    after_arm_state = ChakraState(unlocked={"body", "arm"}, active={"body", "arm"})
    unlockable_after_arm = list_unlockable_chakras_for_entity(actor, after_arm_state)

    # Keep only nodes whose immediate dot-prefix is "arm" — those require arm to be
    # unlocked first and should be blocked in the base state.
    arm_sub_nodes = [n for n in unlockable_after_arm if n.startswith("arm.")]
    assert arm_sub_nodes, "expected sub-nodes under 'arm' once arm is unlocked"

    for node_id in arm_sub_nodes:
        assert not can_unlock_chakra_for_entity(actor, base_state, node_id), (
            f"{node_id!r} should be blocked when 'arm' is not yet unlocked"
        )


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
