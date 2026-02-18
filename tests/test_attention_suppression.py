"""Unit tests for attention suppression bridge helpers."""

from edgecaster.systems import attention


class _DummyGame:
    def __init__(self) -> None:
        self.entity_blocked: set[str] = set()
        self.entity_state_updates: list[tuple[str, dict]] = []

    def entity_is_suppressed(self, entity_id: str) -> bool:
        return str(entity_id) in self.entity_blocked

    def patch_entity_state(self, entity_id: str, **fields) -> None:
        self.entity_state_updates.append((str(entity_id), dict(fields)))


def test_is_suppressed_checks_entity_state_first() -> None:
    g = _DummyGame()
    g.entity_blocked.add("ent:1")
    assert attention._is_suppressed(g, entity_id="ent:1") is True


def test_mark_removed_prefers_entity_state_when_entity_id_present() -> None:
    g = _DummyGame()
    attention._mark_removed(g, entity_id="ent:3", reason="pickup")
    assert g.entity_state_updates == [("ent:3", {"removed": True, "removed_reason": "pickup"})]
