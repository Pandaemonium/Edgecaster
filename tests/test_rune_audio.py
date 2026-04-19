from types import SimpleNamespace

from edgecaster.systems import rune_audio as rune_audio_system


def _pattern_stub():
    vertex0 = SimpleNamespace(pos=(0.0, 0.0))
    vertex1 = SimpleNamespace(pos=(1.0, 0.0))
    edge = SimpleNamespace(a=0, b=1)
    return SimpleNamespace(vertices=[vertex0, vertex1], edges=[edge])


def test_sync_rune_drone_skips_resynth_when_signature_is_already_playing(monkeypatch):
    pattern = _pattern_stub()
    level = SimpleNamespace(pattern=pattern)
    game = SimpleNamespace(_level=lambda: level, _rune_audio_sig="sig:stable")
    audio = SimpleNamespace(_sfx_playing_sig={"rune_drone": "sig:stable"})

    monkeypatch.setattr(rune_audio_system, "_signature", lambda _pattern: "sig:stable")

    synth_calls = []

    def _unexpected_synth(_pattern, _cfg):
        synth_calls.append(True)
        return (object(), "sig:stable")

    monkeypatch.setattr(rune_audio_system, "synth_rune_drone", _unexpected_synth)

    rune_audio_system.sync_rune_drone(game, audio)

    assert synth_calls == []


def test_sync_rune_drone_replays_same_signature_after_previous_stop(monkeypatch):
    pattern = _pattern_stub()
    level = SimpleNamespace(pattern=pattern)
    play_calls = []
    stop_calls = []

    game = SimpleNamespace(_level=lambda: level, _rune_audio_sig="sig:stable")
    audio = SimpleNamespace(
        _sfx_playing_sig={},
        play_sfx_loop=lambda *args, **kwargs: play_calls.append((args, kwargs)),
        stop_sfx=lambda name: stop_calls.append(name),
    )

    monkeypatch.setattr(rune_audio_system, "_signature", lambda _pattern: "sig:stable")
    monkeypatch.setattr(
        rune_audio_system,
        "synth_rune_drone",
        lambda _pattern, _cfg: ("snd", "sig:stable"),
    )

    rune_audio_system.sync_rune_drone(game, audio)

    assert stop_calls == []
    assert len(play_calls) == 1
    assert getattr(game, "_rune_audio_sig", None) == "sig:stable"


def test_sync_rune_drone_clears_cached_signature_when_pattern_is_absent():
    game = SimpleNamespace(
        _level=lambda: SimpleNamespace(pattern=None),
        _rune_audio_sig="sig:old",
    )
    stop_calls = []
    audio = SimpleNamespace(stop_sfx=lambda name: stop_calls.append(name))

    rune_audio_system.sync_rune_drone(game, audio)

    assert stop_calls == ["rune_drone"]
    assert getattr(game, "_rune_audio_sig", None) is None
