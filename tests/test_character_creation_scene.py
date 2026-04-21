from __future__ import annotations

from edgecaster.character import default_character
from edgecaster.scenes.character_creation_scene import (
    CHAR_CLASSES,
    CharCreateState,
    CharacterCreationScene,
)


def test_monk_available_nodes_preserve_parent_child_branch_order() -> None:
    """Monk picker should keep branch descendants grouped beneath their parent."""
    scene = CharacterCreationScene()
    state = CharCreateState(
        char=default_character(),
        class_idx=CHAR_CLASSES.index("Monk"),
    )
    state.monk_base = "body"
    state.monk_picks = ["arm"]
    scene.state = state
    scene._sync_monk_state()

    node_ids = [node_id for node_id, _display, _depth in scene._monk_available_nodes()]

    arm_idx = node_ids.index("arm")
    elbow_idx = node_ids.index("arm.elbow")
    hand_idx = node_ids.index("arm.hand")
    mirrored_arm_idx = node_ids.index("arm_m")

    assert arm_idx < elbow_idx < hand_idx < mirrored_arm_idx, (
        "Arm descendants should stay grouped under Arm before the next top-level branch"
    )
