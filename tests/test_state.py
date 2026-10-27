import json

from omadot.settings import Settings
from omadot.state import SLEEP_AFTER, StatePublisher, should_sleep


def _read(path):
    return json.loads(path.read_text())["state"]


def test_publishes_the_turn_cycle(tmp_path):
    path = tmp_path / "omadot" / "state"
    s = StatePublisher(path, mic_open=False)
    assert _read(path) == "idle"

    s.set_mic(True)
    assert _read(path) == "listening"
    s.turn_ended("hola")
    assert _read(path) == "thinking"
    s.bot_started()
    assert _read(path) == "speaking"
    s.bot_stopped()
    assert _read(path) == "listening"


def test_closing_the_mic_does_not_hide_a_reply_in_progress(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.turn_ended("hola")
    s.set_mic(False)
    assert _read(path) == "thinking"
    s.bot_started()
    s.bot_stopped()
    assert _read(path) == "idle"


def test_barge_in_goes_back_to_listening(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.turn_ended("hola")
    s.bot_started()
    s.user_started()
    assert _read(path) == "listening"


def test_filler_ending_before_the_reply_goes_back_to_thinking(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.turn_ended("hola")
    s.reply_started()
    s.bot_started()  # "A ver."
    s.bot_stopped()
    assert _read(path) == "thinking"
    s.bot_started()  # the real answer
    s.reply_ended()
    s.bot_stopped()
    assert _read(path) == "listening"


def test_publishes_what_was_said_for_the_card(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.user_started()
    s.user_said("¿Qué día es hoy?")
    s.turn_ended("¿Qué día es hoy?")
    s.omi_said("\n\nHoy es ")  # Hermes' replies often open with blank lines
    s.omi_said("sábado.")
    data = json.loads(path.read_text())
    assert data["you"] == "¿Qué día es hoy?"
    assert data["omi"] == "Hoy es sábado."

    s.user_started()  # a new turn starts from a clean card
    data = json.loads(path.read_text())
    assert data["you"] == "" and data["omi"] == ""


def test_a_turn_with_no_transcript_is_confused_and_says_so(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.user_started()
    s.turn_ended("")  # a cough, or speech Whisper could not make out: Hermes never runs
    data = json.loads(path.read_text())
    assert data["state"] == "confused"
    assert data["omi"] == "No te entendí."
    s.dismiss()  # the daemon does this after `linger` seconds
    assert _read(path) == "listening"


def test_an_error_stays_visible_until_the_next_turn(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.turn_ended("hola")
    s.reply_started()
    s.error("No pude hablar con Hermes.")
    s.bot_started()  # Omi says the error out loud
    s.reply_ended()
    s.bot_stopped()
    data = json.loads(path.read_text())
    assert data["state"] == "error"
    assert data["omi"] == "No pude hablar con Hermes."

    s.user_started()
    assert _read(path) == "listening"


def test_toggling_the_mic_clears_an_error(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.error("No pude hablar con Hermes.")
    s.set_mic(False)
    data = json.loads(path.read_text())
    assert data["state"] == "idle" and data["omi"] == ""


def test_a_reply_after_a_retry_clears_the_error(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.turn_ended("hola")
    s.reply_started()
    s.error("Hermes no responde. Reintento 1 de 6…")
    s.omi_said("Hola.")  # Hermes came back
    data = json.loads(path.read_text())
    assert data["state"] == "thinking"
    assert data["omi"] == "Hola."


def test_sleeping_closes_the_mic_and_a_toggle_wakes_omi_up(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.sleep()
    assert _read(path) == "sleeping"
    s.set_mic(True)
    assert _read(path) == "listening"


def test_without_voice_the_text_reply_drives_speaking_and_done(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.configure(Settings(speak=False))
    s.turn_ended("hola")
    s.reply_started()
    assert _read(path) == "thinking"
    s.omi_said("Hola.")
    assert _read(path) == "speaking"
    s.reply_ended()
    data = json.loads(path.read_text())
    assert data["state"] == "done" and data["omi"] == "Hola."  # the card keeps the answer until it lingers out


def test_a_hidden_transcript_still_shows_notices(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=True)
    s.configure(Settings(transcript=False))
    s.user_said("¿Qué día es hoy?")
    s.turn_ended("¿Qué día es hoy?")
    s.omi_said("Sábado.")
    data = json.loads(path.read_text())
    assert data["you"] == "" and data["omi"] == ""

    s.error("No pude hablar con Hermes.")
    assert json.loads(path.read_text())["omi"] == "No pude hablar con Hermes."


def test_close_removes_the_file_so_the_bar_shows_off(tmp_path):
    path = tmp_path / "state"
    s = StatePublisher(path, mic_open=False)
    s.close()
    assert not path.exists()


def test_omi_falls_asleep_only_when_waiting_for_you():
    on = Settings(sleep=True)
    assert should_sleep(on, "listening", idle_for=SLEEP_AFTER)
    assert should_sleep(on, "error", idle_for=SLEEP_AFTER)  # an error nobody came back to
    assert not should_sleep(on, "listening", idle_for=SLEEP_AFTER - 1)
    assert not should_sleep(on, "speaking", idle_for=SLEEP_AFTER * 2)  # a long answer is not idleness
    assert not should_sleep(on, "idle", idle_for=SLEEP_AFTER * 2)  # mic already closed
    assert not should_sleep(Settings(sleep=False), "listening", idle_for=SLEEP_AFTER * 2)
