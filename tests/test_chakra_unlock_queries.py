from __future__ import annotations

from edgecaster import prototypes, spawn_factory
from edgecaster.systems.chakras import (
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


def test_unlockable_query_returns_arm_nodes_when_arm_is_unlocked() -> None:
    actor = _make_actor()
    result = list_unlockable_chakras_for_entity_from_unlocked(actor, {"body", "arm"})
    assert any(n.startswith("arm.") for n in result), "expected arm sub-nodes when arm is unlocked"


def test_unlockable_query_excludes_arm_nodes_when_arm_not_unlocked() -> None:
    """Prereq gate: arm sub-nodes must not appear when arm itself is locked."""
    actor = _make_actor()
    result = list_unlockable_chakras_for_entity_from_unlocked(actor, {"body"})
    arm_sub = [n for n in result if n.startswith("arm.")]
    assert not arm_sub, f"arm sub-nodes {arm_sub!r} should be blocked when arm is not unlocked"


def test_unlockable_query_excludes_already_unlocked_nodes() -> None:
    actor = _make_actor()
    unlocked = {"body", "arm"}
    result = list_unlockable_chakras_for_entity_from_unlocked(actor, unlocked)
    for nid in unlocked:
        assert nid not in result, f"already-unlocked node {nid!r} must not appear in unlockable list"


def test_resonance_helper_from_active_nodes() -> None:
    active_nodes = {
        "body", "arm", "arm_m", "thumb", "index", "middle", "ring", "pinky",
    }
    from edgecaster.systems.chakra_reducer import get_active_resonances
    direct = get_active_resonances(active_nodes)
    assert "bilateral_arms" in direct
    assert "full_hand" in direct
    assert "centered" in direct
