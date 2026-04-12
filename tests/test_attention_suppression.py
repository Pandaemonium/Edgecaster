"""Unit tests for attention suppression bridge helpers."""

from types import SimpleNamespace
from unittest.mock import patch

from edgecaster.systems import attention


class _DummyGame:
    def __init__(self) -> None:
        self.entity_blocked: set[str] = set()
        self.entity_state_updates: list[tuple[str, dict]] = []
        self.entity_state: dict[str, dict] = {}

    def entity_is_suppressed(self, entity_id: str) -> bool:
        return str(entity_id) in self.entity_blocked

    def patch_entity_state(self, entity_id: str, **fields) -> None:
        self.entity_state_updates.append((str(entity_id), dict(fields)))
        st = dict(self.entity_state.get(str(entity_id), {}) or {})
        st.update(dict(fields))
        self.entity_state[str(entity_id)] = st


def test_normalize_optional_lineage_id_preserves_absence() -> None:
    assert attention._normalize_optional_lineage_id(None) is None
    assert attention._normalize_optional_lineage_id("") is None
    assert attention._normalize_optional_lineage_id("  ") is None
    assert attention._normalize_optional_lineage_id("lineage:child:1") == "lineage:child:1"


def test_is_suppressed_checks_entity_state_first() -> None:
    g = _DummyGame()
    g.entity_blocked.add("ent:1")
    assert attention._is_suppressed(g, entity_id="ent:1") is True


def test_mark_removed_prefers_entity_state_when_entity_id_present() -> None:
    g = _DummyGame()
    attention._mark_removed(g, entity_id="ent:3", reason="pickup")
    assert g.entity_state_updates == [("ent:3", {"removed": True, "removed_reason": "pickup"})]


def test_is_suppressed_checks_lineage_state() -> None:
    g = _DummyGame()
    g.entity_state["lineage:door:1"] = {"removed": True}
    assert attention._is_suppressed(
        g,
        entity_id="runtime:door:1",
        lineage_id="lineage:door:1",
    ) is True


def test_is_suppressed_prefers_entity_state_over_legacy_lineage_lifecycle() -> None:
    g = _DummyGame()
    g.entity_state["runtime:door:2"] = {"removed": False}
    g.entity_state["lineage:door:2"] = {"removed": True}
    assert attention._is_suppressed(
        g,
        entity_id="runtime:door:2",
        lineage_id="lineage:door:2",
    ) is False


def test_apply_persisted_entity_state_applies_runtime_fields() -> None:
    class _Stats:
        def __init__(self) -> None:
            self.hp = 8
            self.max_hp = 10
            self.mana = 1
            self.max_mana = 3

        def clamp(self) -> None:
            if self.hp < 0:
                self.hp = 0
            if self.hp > self.max_hp:
                self.hp = self.max_hp
            if self.mana < 0:
                self.mana = 0
            if self.mana > self.max_mana:
                self.mana = self.max_mana

    g = _DummyGame()
    g.entity_state["lineage:obj:1"] = {
        "last_known_hp": 3,
        "last_known_max_hp": 12,
        "last_known_mana": 2,
        "last_known_max_mana": 5,
        "last_known_tags": {"door_state": "open"},
        "last_known_glyph": "/",
        "last_known_blocks_movement": False,
        "last_known_statuses": {"frozen": 2},
    }
    obj = SimpleNamespace(
        tags={"lineage_id": "lineage:obj:1", "door_state": "closed"},
        glyph="+",
        blocks_movement=True,
        stats=_Stats(),
        statuses={},
        cooldowns={},
    )
    ok = attention._apply_persisted_entity_state(
        g,
        obj,
        entity_id="runtime:obj:1",
        lineage_id="lineage:obj:1",
    )
    assert ok is True
    assert obj.stats.hp == 3
    assert obj.stats.max_hp == 12
    assert obj.stats.mana == 2
    assert obj.stats.max_mana == 5
    assert obj.tags.get("door_state") == "open"
    assert obj.glyph == "/"
    assert bool(obj.blocks_movement) is False
    assert obj.statuses.get("frozen") == 2


def test_apply_persisted_entity_state_returns_false_when_removed() -> None:
    g = _DummyGame()
    g.entity_state["lineage:obj:dead"] = {"removed": True}
    obj = SimpleNamespace(tags={"lineage_id": "lineage:obj:dead"})
    ok = attention._apply_persisted_entity_state(
        g,
        obj,
        entity_id="runtime:obj:dead",
        lineage_id="lineage:obj:dead",
    )
    assert ok is False


def test_apply_persisted_entity_state_prefers_entity_record_over_legacy_removed() -> None:
    class _Stats:
        def __init__(self) -> None:
            self.hp = 8
            self.max_hp = 10
            self.mana = 1
            self.max_mana = 3

        def clamp(self) -> None:
            pass

    g = _DummyGame()
    g.entity_state["runtime:obj:live"] = {"removed": False, "last_known_hp": 6}
    g.entity_state["lineage:obj:live"] = {"removed": True, "last_known_tags": {"legacy_flag": True}}
    obj = SimpleNamespace(
        tags={"lineage_id": "lineage:obj:live"},
        glyph="+",
        blocks_movement=True,
        stats=_Stats(),
        statuses={},
        cooldowns={},
    )
    ok = attention._apply_persisted_entity_state(
        g,
        obj,
        entity_id="runtime:obj:live",
        lineage_id="lineage:obj:live",
    )
    assert ok is True
    assert obj.stats.hp == 6
    assert obj.tags.get("legacy_flag") is True


def test_resolve_depth_defaults_follow_lod_curve() -> None:
    g = _DummyGame()
    assert attention._resolve_max_depth_for_lod(g, cam_lod=2.5, parent_tags={}) == 0
    assert attention._resolve_max_depth_for_lod(g, cam_lod=1.0, parent_tags={}) == 1
    assert attention._resolve_max_depth_for_lod(g, cam_lod=0.4, parent_tags={}) == 2
    assert attention._resolve_max_depth_for_lod(g, cam_lod=-1.0, parent_tags={}) == 4


def test_sync_attention_instantiation_skips_unchanged_clean_view() -> None:
    class _WorldIndex:
        def query_abs_rect(self, *_args, **_kwargs):
            raise AssertionError("unchanged clean attention sync should return early")

    level = SimpleNamespace(spatial_dirty=False)
    g = SimpleNamespace(
        levels={(0, 0, 0): level},
        world_entity_index=_WorldIndex(),
        _attn_last_sync_abs_rect=(0.0, 0.0, 10.0, 10.0),
        _attn_last_sync_cam_lod=0.0,
    )

    attention.sync_attention_instantiation(g, (0.0, 0.0, 10.0, 10.0), cam_lod=0.0)

    assert g._last_view_abs_rect == (0.0, 0.0, 10.0, 10.0)
    assert g._last_view_cam_lod == 0.0


def test_sync_attention_instantiation_rechecks_when_loaded_level_is_dirty() -> None:
    class _WorldIndex:
        def __init__(self) -> None:
            self.calls = 0

        def query_abs_rect(self, *_args, **_kwargs):
            self.calls += 1
            return []

    level = SimpleNamespace(
        spatial_dirty=True,
        world=SimpleNamespace(width=10, height=10),
    )
    world_index = _WorldIndex()
    g = SimpleNamespace(
        levels={(0, 0, 0): level},
        zone_coord=(0, 0, 0),
        cfg=SimpleNamespace(world_width=10, world_height=10, entity_render_pad_tiles=0.0, render_max_zone_span=1),
        world_entity_index=world_index,
        attn_store=object(),
        _attn_last_sync_abs_rect=(0.0, 0.0, 10.0, 10.0),
        _attn_last_sync_cam_lod=0.0,
        _ensure_world_aggregate_entities=lambda **_kwargs: None,
        _clamp_zone_window=lambda zx0, zx1, zy0, zy1, **_kwargs: (zx0, zx1, zy0, zy1, False),
        _get_player_abs=lambda: (5.0, 5.0),
        _level=lambda: level,
    )

    with patch("edgecaster.systems.site_placement.ensure_world_sites", lambda _game: None):
        attention.sync_attention_instantiation(g, (0.0, 0.0, 10.0, 10.0), cam_lod=0.0)

    assert world_index.calls == 1


def test_resolve_depth_respects_parent_cap_and_bias() -> None:
    g = _DummyGame()
    # Positive lod bias deepens detail one notch at same camera zoom.
    assert attention._resolve_max_depth_for_lod(
        g,
        cam_lod=1.0,
        parent_tags={"resolve_lod_bias": 0.8},
    ) >= 2
    # Parent cap is authoritative.
    assert attention._resolve_max_depth_for_lod(
        g,
        cam_lod=-2.0,
        parent_tags={"resolve_max_depth": 2},
    ) == 2


def test_resolve_depth_allows_cfg_override() -> None:
    g = _DummyGame()
    g.cfg = SimpleNamespace(
        resolve_depth_ladder=[(1.0, 1), (0.0, 3), (-1.0, 5)],
        resolve_disable_above_lod=5.0,
        resolve_depth_cap_default=5,
    )
    assert attention._resolve_max_depth_for_lod(g, cam_lod=0.2, parent_tags={}) == 1
    assert attention._resolve_max_depth_for_lod(g, cam_lod=-0.2, parent_tags={}) == 3


def test_resolve_distance_depth_defaults_follow_distance_curve() -> None:
    g = _DummyGame()
    assert attention._resolve_max_depth_for_distance(g, distance_screens=0.5, parent_tags={}) == 6
    assert attention._resolve_max_depth_for_distance(g, distance_screens=2.0, parent_tags={}) == 5
    assert attention._resolve_max_depth_for_distance(g, distance_screens=5.0, parent_tags={}) == 3
    assert attention._resolve_max_depth_for_distance(g, distance_screens=25.0, parent_tags={}) == 1


def test_resolve_distance_depth_honors_parent_bias() -> None:
    g = _DummyGame()
    # Positive bias effectively makes a parent "closer", allowing deeper resolution.
    assert attention._resolve_max_depth_for_distance(
        g,
        distance_screens=3.0,
        parent_tags={"resolve_distance_bias_screens": 1.2},
    ) == 5


def test_resolve_distance_depth_allows_cfg_override() -> None:
    g = _DummyGame()
    g.cfg = SimpleNamespace(
        resolve_distance_depth_ladder=[(1.0, 7), (3.0, 4), (999.0, 1)],
    )
    assert attention._resolve_max_depth_for_distance(g, distance_screens=0.7, parent_tags={}) == 7
    assert attention._resolve_max_depth_for_distance(g, distance_screens=2.0, parent_tags={}) == 4
    assert attention._resolve_max_depth_for_distance(g, distance_screens=30.0, parent_tags={}) == 1
