from datetime import datetime

from omadot.services import Focus, Reminders, Tasks, parse_when


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_reminders_fire_once_persist_and_snooze(tmp_path):
    clock = Clock()
    rs = Reminders(tmp_path / "r.json", clock)
    r = rs.add("Pagar la factura", at=1300.0)
    assert rs.due() == [] and rs.pending() == [r]

    clock.t = 1301
    assert [x.id for x in rs.due()] == [r.id]
    assert rs.due() == []  # not again

    again = Reminders(tmp_path / "r.json", clock)  # survives a restart, already fired
    assert again.due() == [] and again.pending() == []

    assert again.snooze(r.id, minutes=1).at == 1301 + 60
    clock.t = 1362
    assert [x.text for x in again.due()] == ["Pagar la factura"]
    assert again.done(r.id) is True and again.done(r.id) is False
    assert Reminders(tmp_path / "r.json", clock).pending() == []


def test_parse_when_accepts_iso_local_and_rejects_garbage():
    ts = parse_when("2026-11-04T09:00")
    assert ts == datetime(2026, 11, 4, 9, 0).astimezone().timestamp()
    assert parse_when("mañana a las nueve") is None
    assert parse_when(None) is None


def test_focus_counts_down_pauses_and_ends():
    f = Focus(minutes=25, started=0.0)
    assert f.pill(0) == "25:00" and f.pill(61) == "23:59"
    f.pause(100)
    assert f.pill(500) == f.pill(100) and not f.over(5000)
    f.resume(500)
    assert f.pill(500) == "23:20"
    assert f.over(500 + 23 * 60 + 20) and f.pill(99999) == "00:00"


def test_tasks_report_the_least_advanced_in_the_bar():
    ts = Tasks()
    ts.update("vid", 10, "Convirtiendo 6 videos", "1/6", where="/tmp/out")
    ts.update("zip", 60, "Comprimiendo")
    assert ts.pill() == "1/6"
    ts.update("vid", 100)
    assert ts.pill() == "60%"
    assert ts.items["vid"].where == "/tmp/out" and ts.items["vid"].label == "Convirtiendo 6 videos"
    ts.update("zip", 100)
    assert ts.pill() == "" and ts.finish("zip").id == "zip" and ts.finish("nope") is None
