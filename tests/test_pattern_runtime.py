from __future__ import annotations

from types import SimpleNamespace

from edgecaster.systems import pattern_runtime


class _Log:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add(self, msg: str) -> None:
        self.messages.append(str(msg))


class _InputPattern:
    def __init__(self) -> None:
        self.vertices = [SimpleNamespace(tags={}), SimpleNamespace(tags={})]

    def to_segments(self):
        return [("s0",)]


class _PatternFactory:
    @staticmethod
    def from_segments(_segs):
        return SimpleNamespace(meta={})


def _make_game(effect_map: dict[str, float] | None = None):
    actor = SimpleNamespace(id="player")
    level = SimpleNamespace(
        actors={"player": actor},
        pattern=_InputPattern(),
        pattern_motion=object(),
        choking_vines_state={"x": 1},
        rune_choking_vines_state={"x": 1},
    )

    class _Game:
        def __init__(self) -> None:
            self.level = level
            self.cfg = SimpleNamespace(max_vertices=999)
            self.log = _Log()
            self.requested_effect_keys: list[str] = []

        def _level(self):
            return self.level

        def _param_value(self, group: str, key: str):
            if group == "chakra" and key == "amplitude":
                return 1.0
            return None

        def _chakra_modifiers(self, _actor_id: str):
            return None

        def chakra_effect_value(self, key: str, *, actor_id: str | None = None, default: float = 0.0):
            self.requested_effect_keys.append(str(key))
            return float((effect_map or {}).get(str(key), default))

        def _debug(self, _msg: str) -> None:
            return None

    return _Game(), actor


def _patch_chakra_seed_runtime(monkeypatch):
    dummy_projection = {
        "unlocked": {"body"},
        "active": {"body"},
        "charges": {},
        "alignments": {},
        "generators": {},
        "pattern_root": "body",
    }
    monkeypatch.setattr(
        pattern_runtime.chakra_items_system,
        "effective_chakra_projection",
        lambda _game, _actor: dict(dummy_projection),
    )

    import edgecaster.prototypes as prototypes
    import edgecaster.systems.chakras as chakra_system

    monkeypatch.setattr(
        prototypes,
        "resolve_body_schema",
        lambda _actor: {"root": "body", "nodes": {"body": {}}},
    )
    monkeypatch.setattr(
        chakra_system,
        "build_chakra_generator_seed",
        lambda *_args, **_kwargs: SimpleNamespace(
            root_id="body",
            terminus_id="body",
            base_len=1.0,
            node_order=["body"],
            verts=[(0.0, 0.0), (1.0, 0.0)],
            edges=[(0, 1)],
        ),
    )


def test_act_chakra_applies_generator_once_per_press(monkeypatch) -> None:
    _patch_chakra_seed_runtime(monkeypatch)
    apply_calls: list[int] = []

    class _FakeGenerator:
        def __init__(self, *_args, **_kwargs):
            return None

        def apply_segments(self, segs, max_segments=20000):
            apply_calls.append(int(max_segments))
            return list(segs) + [("s1",)]

    monkeypatch.setattr(pattern_runtime.builder, "CustomGraphGenerator", _FakeGenerator)
    monkeypatch.setattr(pattern_runtime.builder, "cleanup_duplicates", lambda segs: segs)
    monkeypatch.setattr(pattern_runtime.builder, "Pattern", _PatternFactory)

    game, _actor = _make_game(
        effect_map={
            # Regression guard: this should no longer cause extra passes.
            "generator_iteration_bonus": 3.0,
        }
    )
    pattern_runtime.act_chakra(game, "player")

    assert len(apply_calls) == 1
    assert "generator_iteration_bonus" not in game.requested_effect_keys


def test_act_chakra_applies_amp_bonus(monkeypatch) -> None:
    _patch_chakra_seed_runtime(monkeypatch)
    captured_amp: list[float] = []

    class _FakeGenerator:
        def __init__(self, _verts, _edges, *, amplitude=1.0, vertex_labels=None):
            captured_amp.append(float(amplitude))

        def apply_segments(self, segs, max_segments=20000):
            return list(segs)

    monkeypatch.setattr(pattern_runtime.builder, "CustomGraphGenerator", _FakeGenerator)
    monkeypatch.setattr(pattern_runtime.builder, "cleanup_duplicates", lambda segs: segs)
    monkeypatch.setattr(pattern_runtime.builder, "Pattern", _PatternFactory)

    game, _actor = _make_game(effect_map={"chakra_generator_amp_bonus": 0.2})
    pattern_runtime.act_chakra(game, "player")

    assert captured_amp
    assert captured_amp[0] == 1.2


def test_act_chakra_skips_schema_resolve_for_component_backed_actor(monkeypatch) -> None:
    dummy_projection = {
        "unlocked": {"body"},
        "active": {"body"},
        "charges": {},
        "alignments": {},
        "generators": {},
        "pattern_root": "body",
    }
    captured_body_schemas: list[dict] = []

    game, actor = _make_game()
    actor.chakra_component = object()

    monkeypatch.setattr(
        pattern_runtime.chakra_items_system,
        "effective_chakra_projection",
        lambda _game, _actor: dict(dummy_projection),
    )
    import edgecaster.prototypes as prototypes
    import edgecaster.systems.chakras as chakra_system

    def _fail_schema_resolve(_actor):
        raise AssertionError("resolve_body_schema should not be called for component-backed casts")

    def _fake_seed(chakra_state, *, body_schema=None, **_kwargs):
        captured_body_schemas.append(dict(body_schema or {}))
        return SimpleNamespace(
            root_id="body",
            terminus_id="body",
            base_len=1.0,
            node_order=["body"],
            verts=[(0.0, 0.0), (1.0, 0.0)],
            edges=[(0, 1)],
        )

    monkeypatch.setattr(prototypes, "resolve_body_schema", _fail_schema_resolve)
    monkeypatch.setattr(chakra_system, "build_chakra_generator_seed", _fake_seed)

    class _FakeGenerator:
        def __init__(self, *_args, **_kwargs):
            return None

        def apply_segments(self, segs, max_segments=20000):
            return list(segs)

    monkeypatch.setattr(pattern_runtime.builder, "CustomGraphGenerator", _FakeGenerator)
    monkeypatch.setattr(pattern_runtime.builder, "cleanup_duplicates", lambda segs: segs)
    monkeypatch.setattr(pattern_runtime.builder, "Pattern", _PatternFactory)

    pattern_runtime.act_chakra(game, "player")

    assert captured_body_schemas == [{}]


def test_chakra_modifiers_prefers_reduced_charge_snapshot(monkeypatch) -> None:
    game, actor = _make_game()
    actor._chakra_effective_channels = {
        "body": {"charge": 0.0},
        "head": {"charge": 1.0},
    }

    monkeypatch.setattr(
        pattern_runtime.chakra_items_system,
        "effective_active_nodes",
        lambda _game, _actor: {"body", "head"},
    )

    import edgecaster.prototypes as prototypes

    monkeypatch.setattr(
        prototypes,
        "resolve_body_schema",
        lambda _actor: {"root": "body", "nodes": {"body": {}, "head": {}}},
    )

    mods = pattern_runtime.chakra_modifiers(game, "player")

    assert mods is not None
    assert mods.damage_mult > 1.0
    assert mods.mana_cost_mult < 1.0


def test_chakra_modifiers_resonance_uses_effective_active_nodes(monkeypatch) -> None:
    """Resonance bonus detection must use effective_active_nodes, not raw chakra_state.

    Equipment items can grant temporary node activations.  Resonance bonuses
    (bilateral_arms, full_hand, etc.) should see those item-granted nodes the
    same way charge averaging does, so the two reads are consistent.
    """
    game, actor = _make_game()

    # Equipment grants arm + arm_m — enough to trigger bilateral_arms resonance.
    monkeypatch.setattr(
        pattern_runtime.chakra_items_system,
        "effective_active_nodes",
        lambda _game, _actor: {"body", "arm", "arm_m"},
    )

    import edgecaster.prototypes as prototypes

    monkeypatch.setattr(
        prototypes,
        "resolve_body_schema",
        lambda _actor: {"root": "body", "nodes": {"body": {}}},
    )

    mods = pattern_runtime.chakra_modifiers(game, "player")

    assert mods is not None
    # bilateral_arms grants damage_mult=1.05 and mana_cost_mult=0.90.
    assert mods.damage_mult >= 1.05, "bilateral_arms resonance bonus not applied"
    assert mods.mana_cost_mult <= 0.90, "bilateral_arms mana discount not applied"
