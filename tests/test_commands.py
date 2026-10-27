import json

import pytest

from omadot.commands import DROP_PROMPT, Commands
from omadot.services import Reminders
from omadot.state import LINGER_ACTIONS, LINGER_PLAIN, StatePublisher


class FakeAgent:
    def __init__(self):
        self.asked: list[str] = []
        self.approved: list[str] = []

    async def ask(self, text):
        self.asked.append(text)

    async def approve(self, choice):
        self.approved.append(choice)
        return True


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def _runner(calls):
    def run(argv, timeout=5.0, capture=True):
        calls.append(argv)
        return ""

    return run


def _setup(tmp_path, clock=None):
    clock = clock or Clock()
    calls = []
    state = StatePublisher(tmp_path / "state", mic_open=False)
    agent = FakeAgent()
    c = Commands(state, agent, reminders=Reminders(tmp_path / "r.json", clock), run=_runner(calls), clock=clock,
                 clipboard_path=tmp_path / "clip.json", shots=tmp_path)
    return c, state, agent, calls, clock


def _data(tmp_path):
    return json.loads((tmp_path / "state").read_text())


@pytest.mark.asyncio
async def test_a_dropped_file_becomes_a_turn_omi_answers(tmp_path):
    c, state, agent, _, _ = _setup(tmp_path)
    f = tmp_path / "invoice.pdf"
    f.write_text("x")
    state.catch(True)
    await c.drop({"path": str(f)})
    assert agent.asked == [DROP_PROMPT.format(path=str(f))]
    d = _data(tmp_path)
    assert d["state"] == "thinking" and d["you"] == "📎 invoice.pdf"
    with pytest.raises(ValueError):
        await c.drop({"path": str(tmp_path / "missing.pdf")})


@pytest.mark.asyncio
async def test_ask_carries_the_desktop_context_and_shows_what_it_can(tmp_path):
    c, state, agent, calls, _ = _setup(tmp_path)
    (tmp_path / "clip.json").write_text(json.dumps([{"type": "text", "text": "https://omarchy.org", "capturedAt": "03:21"}]))
    await c.ask({"text": "abre el enlace que copié"})
    assert agent.asked[0].startswith("abre el enlace que copié\n\n[Now: ") and "[Clipboard" in agent.asked[0]
    d = _data(tmp_path)
    assert d["you"] == "abre el enlace que copié" and d["card"]["clips"][0]["kind"] == "link" and d["state"] == "thinking"


@pytest.mark.asyncio
async def test_approve_answers_hermes_and_clears_the_question(tmp_path):
    c, state, agent, _, _ = _setup(tmp_path)
    state.turn_ended("manda el correo")
    state.approval("¿Envío?", "req-1")
    assert await c.approve({"choice": "deny"}) == {"answered": True}
    assert agent.approved == ["deny"] and "approval" not in _data(tmp_path)["card"]
    with pytest.raises(ValueError):
        await c.approve({"choice": "maybe"})


def test_card_side_effects_schedule_reminders_focus_tasks_and_remember_rewrites(tmp_path):
    c, state, _, _, clock = _setup(tmp_path)
    c.on_card({"reminder": {"text": "Pagar la factura", "at": "2099-01-01T09:00"}, "focus": {"minutes": 1},
               "task": {"id": "vid", "label": "Convirtiendo", "progress": 10, "detail": "1/6"}, "rewrite": {"before": "a", "after": "b"}})
    d = _data(tmp_path)
    assert c.reminders.pending()[0].text == "Pagar la factura" and d["card"]["reminder"]["id"] == c.reminders.pending()[0].id
    assert d["pill"] == "01:00" and c.tasks.items["vid"].detail == "1/6"
    clock.t += 30
    c.tick()
    assert _data(tmp_path)["pill"] == "00:30"
    clock.t += 31
    c.tick()
    d = _data(tmp_path)
    assert d["state"] == "notify" and d["buttons"][0]["args"] == ["focus", "1"] and d["pill"] == "1/6"


def test_reminders_ring_as_notifications_with_done_and_snooze(tmp_path):
    clock = Clock()
    c, state, _, calls, _ = _setup(tmp_path, clock)
    r = c.reminders.add("Llamar a Ana", at=1010.0)
    c.tick()
    assert state.state == "idle"
    clock.t = 1011
    c.tick()
    d = _data(tmp_path)
    assert d["state"] == "notify" and d["card"]["notify"]["text"] == "Llamar a Ana"
    assert d["buttons"][0]["args"] == ["action", "reminder", r.id, "done"]
    assert ["notify-send", "-a", "OmaDot", "Recordatorio", "Llamar a Ana"] in calls
    c.action({"kind": "reminder", "id": r.id, "op": "snooze", "minutes": 1})
    assert state.state == "idle" and c.reminders.pending()[0].at == 1011 + 60
    c.action({"kind": "reminder", "id": r.id, "op": "done"})
    assert c.reminders.pending() == []


def test_cards_linger_then_go_away(tmp_path):
    clock = Clock()
    c, state, _, _, _ = _setup(tmp_path, clock)
    state.set_mic(True)
    state.turn_ended("hola")
    state.reply_started()
    state.omi_said("Hola.")
    state.bot_started()
    state.reply_ended()
    state.bot_stopped()
    c.tick()
    clock.t += LINGER_PLAIN - 0.5
    c.tick()
    assert state.state == "done"
    clock.t += 1
    c.tick()
    assert state.state == "listening" and _data(tmp_path)["omi"] == ""

    state.turn_ended("dame el archivo")
    state.show({"files": [{"path": "/tmp/a", "label": "a"}]})
    state.bot_started()
    state.bot_stopped()
    c.tick()
    clock.t += LINGER_PLAIN + 1
    c.tick()
    assert state.state == "done"  # files wait longer
    clock.t += LINGER_ACTIONS
    c.tick()
    assert state.state == "listening"


def test_progress_updates_the_pill_and_finishes_with_a_notification(tmp_path):
    c, state, _, calls, _ = _setup(tmp_path)
    assert c.progress({"id": "vid", "pct": 34, "label": "Convirtiendo 6 videos", "detail": "2/6", "where": "/tmp/mp4"}) == {"pill": "2/6"}
    assert _data(tmp_path)["card"]["task"]["progress"] == 34
    c.progress({"id": "vid", "pct": 100})
    d = _data(tmp_path)
    assert d["state"] == "notify" and d["buttons"][0] == {"label": "Abrir carpeta", "args": ["open", "/tmp/mp4"], "primary": True}
    assert d["pill"] == "" and c.tasks.items == {}


@pytest.mark.asyncio
async def test_copy_replace_mute_and_catch(tmp_path):
    muted = []
    c, state, _, calls, _ = _setup(tmp_path)
    c._on_mute = muted.append
    c.on_card({"rewrite": {"before": "hey", "after": "Hola Ana"}})
    await c.copy({})
    await c.replace({})
    assert ["wl-copy", "--", "Hola Ana"] in calls and ["wtype", "--", "Hola Ana"] in calls
    assert _data(tmp_path)["card"]["rewrite"]["replaced"] is True
    assert c.mute({}) == {"muted": True} and state.state == "muted" and muted == [True]
    assert c.mute({}) == {"muted": False} and state.state == "idle"
    c.catch({"on": "on"})
    assert state.state == "catch"
    c.catch({"on": "off"})
    assert state.state == "idle"
    with pytest.raises(ValueError):
        c.catch({"on": "garbage"})
    with pytest.raises(ValueError):
        c.mute({"on": "garbage"})


def test_mute_toggles_the_real_flag_even_while_a_turn_is_showing(tmp_path):
    c, state, _, _, _ = _setup(tmp_path)
    c.mute({"on": "on"})
    state.turn_ended("cuenta hasta tres")  # thinking overlays "muted" in the rendered state
    assert state.state == "thinking" and state.muted
    assert c.mute({}) == {"muted": False}
    assert not state.muted


def test_focus_rejects_nonsense_and_unknown_reminders_are_an_error(tmp_path):
    c, state, _, _, _ = _setup(tmp_path)
    for bad in ("abc", "-5", "0", "99999"):
        with pytest.raises(ValueError, match="minutes"):
            c.start_focus({"minutes": bad})
    assert c.focus is None and state.state == "idle"
    with pytest.raises(ValueError, match="no such reminder"):
        c.action({"kind": "reminder", "id": "nope", "op": "done"})
    with pytest.raises(ValueError, match="no such reminder"):
        c.action({"kind": "reminder", "id": "nope", "op": "snooze"})


def test_a_finished_task_has_one_open_folder_button_and_dismiss_clears_you(tmp_path):
    c, state, _, _, _ = _setup(tmp_path)
    state.user_said("convierte los videos")
    c.progress({"id": "v", "pct": 100, "label": "Convirtiendo", "where": "/tmp/out"})
    d = _data(tmp_path)
    assert [b["label"] for b in d["buttons"]] == ["Abrir carpeta"]
    c.dismiss({})
    assert _data(tmp_path)["you"] == ""


@pytest.mark.asyncio
async def test_a_page_opens_as_a_small_floating_browser(tmp_path):
    c, state, _, calls, _ = _setup(tmp_path)
    await c.open({"target": "https://omarchy.org", "mode": "mini"})
    assert calls[-1][:3] == ["hyprctl", "dispatch", "exec"] and "chromium --app=https://omarchy.org" in calls[-1][3]
    assert calls[-1][3].startswith("[float; size 760 640; move 100%-772 60]")
    with pytest.raises(ValueError):
        await c.open({})


def test_several_notifications_queue_up_and_show_one_at_a_time(tmp_path):
    clock = Clock()
    c, state, _, calls, _ = _setup(tmp_path, clock)
    a = c.reminders.add("Llamar a Ana", at=1005.0)
    b = c.reminders.add("Pagar la factura", at=1006.0)
    clock.t = 1010
    c.tick()
    d = _data(tmp_path)
    assert d["state"] == "notify" and d["card"]["notify"]["text"] == "Llamar a Ana" and d["card"]["reminder"]["id"] == a.id
    c.action({"kind": "reminder", "id": a.id, "op": "done"})
    d = _data(tmp_path)
    assert d["state"] == "notify" and d["card"]["notify"]["text"] == "Pagar la factura" and d["card"]["reminder"]["id"] == b.id
    c.action({"kind": "reminder", "id": b.id, "op": "done"})
    assert state.state == "idle"


def test_ambient_updates_get_the_card_to_themselves_and_focus_means_do_not_disturb(tmp_path):
    clock = Clock()
    c, state, _, _, _ = _setup(tmp_path, clock)
    state.set_mic(True)
    state.user_said("pon un enfoque")
    state.turn_ended("pon un enfoque")
    state.omi_said("Listo, enfoque de un minuto.")
    state.bot_started()
    state.bot_stopped()
    c.remind({"text": "Revisar el correo", "at": "2099-01-01T09:00"})
    d = _data(tmp_path)
    assert d["omi"] == "Listo, enfoque de un minuto." and "reminder" in d["card"]  # joins the reply still on screen
    c.dismiss({})
    c.remind({"text": "Revisar el correo", "at": "2099-01-01T10:00"})
    d = _data(tmp_path)
    assert d["you"] == "" and d["omi"] == "" and list(d["card"]) == ["reminder"] and d["state"] == "done"

    r = c.reminders.add("Llamar a Ana", at=clock.t + 10)
    c.start_focus({"minutes": 1})
    clock.t += 20
    c.tick()
    assert state.state != "notify" and c.reminders.pending()[0].id == r.id  # waits for the focus to end
    clock.t += 60
    c.tick()  # focus over -> its own notification first, the reminder on the next tick
    assert _data(tmp_path)["card"]["notify"]["text"].startswith("Se acabó")
    c.dismiss({})
    c.tick()
    assert _data(tmp_path)["card"]["notify"]["text"] == "Llamar a Ana"


@pytest.mark.asyncio
async def test_repeat_and_say_use_the_voice_without_hermes(tmp_path):
    spoken = []

    async def say(text):
        spoken.append(text)

    c, state, _, _, _ = _setup(tmp_path)
    c._say = say
    with pytest.raises(ValueError, match="nothing to repeat"):
        await c.repeat({})
    state.turn_ended("hola")
    state.omi_said("Hola, ¿qué hacemos?")
    state.bot_started()
    state.bot_stopped()
    c.dismiss({})
    await c.repeat({})
    await c.say({"text": "  Se acabó   el tiempo "})
    assert spoken == ["Hola, ¿qué hacemos?", "Se acabó el tiempo"]
    with pytest.raises(ValueError):
        await c.say({"text": ""})


def test_focus_end_offers_the_same_length_again_and_reminders_list_a_readable_time(tmp_path):
    clock = Clock()
    c, state, _, _, _ = _setup(tmp_path, clock)
    c.start_focus({"minutes": 1})
    clock.t += 61
    c.tick()
    d = _data(tmp_path)
    assert d["card"]["notify"]["text"] == "Se acabó el tiempo de enfoque. ¿Otros 1?"
    assert d["buttons"][0] == {"label": "Otros 1", "args": ["focus", "1"], "primary": True}
    c.remind({"text": "x", "at": "2099-01-01T09:00"})
    assert c.list_reminders({})["reminders"][0]["when"] == "2099-01-01 09:00"


def test_a_progress_tick_does_not_wipe_an_answer_still_on_screen(tmp_path):
    c, state, _, _, _ = _setup(tmp_path)
    state.set_mic(True)
    state.user_said("dame el readme")
    state.turn_ended("dame el readme")
    state.omi_said("Aquí está.")
    state.show({"files": [{"path": "/tmp/a", "label": "a"}]})
    state.bot_started()
    state.bot_stopped()
    assert state.state == "done"
    c.progress({"id": "v", "pct": 40, "label": "Convirtiendo", "detail": "2/6"})
    d = _data(tmp_path)
    assert d["omi"] == "Aquí está." and "files" in d["card"] and d["card"]["task"]["detail"] == "2/6" and d["pill"] == "2/6"
    c.dismiss({})
    c.progress({"id": "v", "pct": 60, "label": "Convirtiendo", "detail": "3/6"})
    d = _data(tmp_path)
    assert d["omi"] == "" and list(d["card"]) == ["task"]
