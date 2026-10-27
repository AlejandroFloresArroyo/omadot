import json

import pytest

from omadot.settings import Settings, load, set_value


def test_missing_file_gives_the_defaults(tmp_path):
    assert load(tmp_path / "settings.json") == Settings()


def test_a_bad_value_in_the_file_falls_back_for_that_key_only(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"voice": "nope", "speed": 1.2, "speak": "yes", "sleep": False}))
    s = load(path)
    assert s.voice == Settings().voice
    assert s.speed == 1.2
    assert s.speak is True
    assert s.sleep is False


def test_a_corrupt_file_gives_the_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert load(path) == Settings()


def test_set_value_parses_validates_and_persists(tmp_path):
    path = tmp_path / "omadot" / "settings.json"
    set_value(path, "voice", "em_alex")
    set_value(path, "speed", "1.25")
    set_value(path, "transcript", "off")
    s = load(path)
    assert (s.voice, s.speed, s.transcript) == ("em_alex", 1.25, False)


@pytest.mark.parametrize(
    ("key", "value"),
    [("voice", "alloy"), ("speed", "2"), ("speed", "rápido"), ("speak", "maybe"), ("colour", "x")],
)
def test_set_value_rejects_what_the_daemon_cannot_use(tmp_path, key, value):
    path = tmp_path / "settings.json"
    with pytest.raises(ValueError):
        set_value(path, key, value)
    assert not path.exists()


def test_the_card_shows_text_when_omi_does_not_speak():
    # With the voice off the card is the only way to get the answer, so it shows it even if the
    # transcript is hidden.
    assert Settings(speak=False, transcript=False).show_text is True
    assert Settings(speak=True, transcript=False).show_text is False


def test_tts_engine_and_omni_voice_are_settings(tmp_path):
    from omadot.settings import set_value

    path = tmp_path / "settings.json"
    s = set_value(path, "tts", "omni")
    assert s.tts == "omni" and s.tts_url == "http://127.0.0.1:8882" and s.tts_voice == "design:male, young adult, moderate pitch"
    s = set_value(path, "omni_voice", "female, young adult, low pitch")
    assert s.tts_voice == "design:female, young adult, low pitch"
    s = set_value(path, "tts", "kokoro")
    assert s.tts_url == "http://127.0.0.1:8880" and s.tts_voice == "ef_dora"
    import pytest

    with pytest.raises(ValueError):
        set_value(path, "tts", "qwen")


def test_fast_lane_is_opt_in(tmp_path):
    from omadot.settings import load, set_value

    path = tmp_path / "settings.json"
    assert load(path).fast is False
    assert set_value(path, "fast", "on").fast is True
    assert load(path).fast is True
    assert set_value(path, "fast", "off").fast is False


def test_fast_lane_starts_only_when_asked_for():
    from omadot import fast_lane_wanted
    from omadot.settings import Settings

    assert fast_lane_wanted(Settings(), flag=False) is False
    assert fast_lane_wanted(Settings(fast=True), flag=False) is True
    assert fast_lane_wanted(Settings(), flag=True) is True
