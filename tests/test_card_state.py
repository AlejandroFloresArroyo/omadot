import json

from omadot.settings import Settings
from omadot.state import LINGER_ACTIONS, LINGER_PLAIN, DoubleTap, StatePublisher, buttons_for, toggle_action


def _read(path):
    return json.loads(path.read_text())


def _turn(s, reply="Aquí está."):
    s.set_mic(True)
    s.user_started()
    s.user_said("dame el archivo")
    s.turn_ended("dame el archivo")
    s.reply_started()
    s.omi_said(reply)


def test_spoken_only_reply_lingers_briefly_as_done(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=False)
    _turn(s)
    s.bot_started()
    s.reply_ended()
    s.bot_stopped()
    assert _read(tmp_path / "state")["state"] == "done"
    assert s.linger == LINGER_PLAIN
    s.dismiss()
    d = _read(tmp_path / "state")
    assert d["state"] == "listening" and d["omi"] == "" and d["card"] == {}


def test_reply_with_files_lingers_longer_and_exposes_the_card(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=False)
    _turn(s)
    s.show({"files": [{"path": "/tmp/a.pdf", "label": "a.pdf"}]})
    s.bot_started()
    s.reply_ended()
    s.bot_stopped()
    d = _read(tmp_path / "state")
    assert d["state"] == "done" and d["card"]["files"][0]["label"] == "a.pdf"
    assert s.linger == LINGER_ACTIONS


def test_next_turn_clears_the_card(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=True)
    _turn(s)
    s.show({"url": "https://omarchy.org"})
    s.user_started()
    assert _read(tmp_path / "state")["card"] == {}


def test_working_only_while_thinking(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=True)
    s.working("terminal")
    assert s.state == "listening"
    s.turn_ended("abre herdr")
    s.working("terminal")
    assert _read(tmp_path / "state")["state"] == "working"
    s.bot_started()
    assert s.state == "speaking"


def test_nothing_understood_is_confused_then_back(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=True)
    s.turn_ended("")
    d = _read(tmp_path / "state")
    assert d["state"] == "confused" and d["omi"] == "No te entendí."
    assert s.linger == LINGER_PLAIN
    s.dismiss()
    assert s.state == "listening"


def test_catch_notify_muted_and_pill(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=False)
    s.catch(True)
    assert s.state == "catch"
    s.catch(False)
    s.pill("24:59")
    assert _read(tmp_path / "state")["pill"] == "24:59"
    s.notify("Pagar la factura", [{"label": "Hecho", "args": ["action", "r1", "done"], "primary": True}])
    d = _read(tmp_path / "state")
    assert d["state"] == "notify" and d["card"]["notify"]["text"] == "Pagar la factura"
    assert d["buttons"][0] == {"label": "Hecho", "args": ["action", "r1", "done"], "primary": True}
    s.dismiss()
    assert s.state == "idle"
    s.set_muted(True)
    assert s.state == "muted"
    s.set_mic(True)
    assert s.state == "listening"


def test_approval_from_hermes_puts_yes_no_first(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=True)
    s.turn_ended("manda el correo")
    s.approval("¿Envío el correo a Ana?", "req-1")
    d = _read(tmp_path / "state")
    assert d["card"]["approval"]["request_id"] == "req-1"
    assert d["buttons"][0]["args"] == ["approve", "once"] and d["buttons"][1]["args"] == ["approve", "deny"]
    s.approved()
    assert "approval" not in _read(tmp_path / "state")["card"]


def test_buttons_follow_the_card_kinds():
    b = buttons_for({"draft": {"to": "Ana", "text": "hola"}, "url": "https://x.y", "actions": [{"label": "Más", "say": "dime más"}]})
    assert [x["args"] for x in b] == [["ask", "Sí, envíalo a Ana"], ["ask", "Hazlo más corto"], ["ask", "Descarta el borrador"], ["open", "https://x.y", "mini"], ["open", "https://x.y"], ["ask", "dime más"]]
    assert b[0]["primary"] and not b[1]["primary"]
    assert buttons_for({"ask": {"question": "¿Lo agrego?", "yes": "Agrégalo", "no": "No"}})[0] == {"label": "Agrégalo", "args": ["ask", "Agrégalo"], "primary": True}


def test_no_voice_reply_ends_as_done(tmp_path):
    s = StatePublisher(tmp_path / "state", mic_open=True, settings=Settings(speak=False))
    _turn(s)
    s.reply_ended()
    assert s.state == "done"


def test_toggle_opens_at_rest_and_closes_everything_otherwise():
    assert toggle_action(False, "idle") == "open"
    assert toggle_action(False, "sleeping") == "open"
    assert toggle_action(False, "muted") == "open"
    assert toggle_action(True, "listening") == "close"
    assert toggle_action(True, "speaking") == "close"
    assert toggle_action(False, "done") == "close"  # the card lingers after an `omadot ask`: close it, don't open the mic
    assert toggle_action(False, "notify") == "close"
    assert toggle_action(False, "error") == "close"


def test_two_quick_super_taps_summon_omi_once():
    taps = DoubleTap(window=0.4)
    assert taps.tap(10.0) is False
    assert taps.tap(10.3) is True
    # A third tap right after starts a new pair instead of toggling again.
    assert taps.tap(10.5) is False
    assert taps.tap(10.8) is True


def test_slow_super_taps_never_summon_omi():
    # Super pressed alone now and then (a cancelled combo) must not open the mic.
    taps = DoubleTap(window=0.4)
    assert taps.tap(10.0) is False
    assert taps.tap(10.5) is False
    assert taps.tap(11.0) is False
    assert taps.tap(11.3) is True
