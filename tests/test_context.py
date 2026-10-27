import json

from omadot.context import active_window, clipboard, enrich, now_playing, screenshot, selection


def _runner(outputs: dict, calls: list | None = None):
    def run(argv, timeout=5.0):
        if calls is not None:
            calls.append(argv)
        for key, out in outputs.items():
            if argv[0] == key:
                if argv[0] == "grim":  # grim writes the file
                    open(argv[-1], "w").write("png")
                return out
        return ""

    return run


WINDOW = json.dumps({"title": "herdr · omadot", "class": "Alacritty", "at": [10, 30], "size": [900, 600]})


def test_clipboard_reads_omarchy_history_newest_first(tmp_path):
    hist = tmp_path / "clipboard-history.json"
    hist.write_text(json.dumps([
        {"type": "text", "text": "https://omarchy.org/news", "capturedAt": "Sunday 03:21"},
        {"type": "image", "path": "/tmp/x.png", "mime": "image/png", "capturedAt": "Sunday 03:20"},
        {"type": "text", "text": "  Beautiful,\n fun  "},
        "junk",
    ]))
    clips = clipboard(5, hist)
    assert [c["kind"] for c in clips] == ["link", "image", "text"]
    assert clips[0]["label"] == "https://omarchy.org/news" and clips[1]["label"] == "x.png" and clips[2]["label"] == "Beautiful, fun"
    assert clipboard(5, tmp_path / "missing") == []


def test_window_selection_and_player_parse_their_commands():
    run = _runner({"hyprctl": WINDOW, "wl-paste": " hey te mando lo que pediste \n", "playerctl": "Playing\tLo-fi focus\tChillhop"})
    assert active_window(run) == {"title": "herdr · omadot", "app": "Alacritty", "at": [10, 30], "size": [900, 600]}
    assert selection(run) == "hey te mando lo que pediste"
    assert now_playing(run) == {"title": "Lo-fi focus", "artist": "Chillhop", "playing": True}
    assert now_playing(_runner({"playerctl": "No players found"})) is None
    assert active_window(_runner({})) == {}


def test_screenshot_targets_the_active_window(tmp_path):
    calls = []
    run = _runner({"grim": ""}, calls)
    path = screenshot({"at": [10, 30], "size": [900, 600]}, run, tmp_path, now=lambda: 42)
    assert path == str(tmp_path / "shot-42.png")
    assert calls == [["grim", "-g", "10,30 900x600", path]]
    assert screenshot({}, _runner({}), tmp_path) == ""  # grim failed: no file, no path


def test_enrich_attaches_only_what_the_words_ask_for(tmp_path):
    hist = tmp_path / "clip.json"
    hist.write_text(json.dumps([{"type": "text", "text": "https://omarchy.org", "capturedAt": "03:21"}]))
    run = _runner({"hyprctl": WINDOW, "wl-paste": "hey", "grim": ""})

    plain = enrich("¿Qué hora es?", run=run, clipboard_path=hist, shots=tmp_path, now=lambda: 1_800_000_000)
    assert plain.text.startswith("¿Qué hora es?\n\n[Now: ") and plain.text.endswith("local time]\n[Active window: herdr · omadot (Alacritty)]") and plain.card == {}

    shot = enrich("¿Qué es esto?", run=run, clipboard_path=hist, shots=tmp_path, now=lambda: 7)
    assert "[Screenshot of the active window: " in shot.text and shot.card["shot"].endswith("shot-7.png")

    clip = enrich("abre el enlace que copié", run=run, clipboard_path=hist, shots=tmp_path)
    assert "[Clipboard, newest first: link: https://omarchy.org]" in clip.text
    assert clip.card["clips"] == [{"kind": "link", "label": "https://omarchy.org", "when": "03:21"}]

    sel = enrich("hazlo más formal", run=run, clipboard_path=hist, shots=tmp_path)
    assert "[Selected text: hey]" in sel.text and sel.card["selection"] == "hey"

    term = enrich("¿por qué falló?", run=run, clipboard_path=hist, shots=tmp_path)
    assert "is a terminal" in term.text


def test_enrich_without_a_compositor_is_the_words_and_the_time():
    t = enrich("hola", run=_runner({}), now=lambda: 1_800_000_000)
    assert t.text.startswith("hola\n\n[Now: ") and t.text.count("\n") == 2 and t.card == {}


def test_everyday_esto_does_not_take_a_screenshot():
    from omadot.context import _SCREEN

    for said in ["No quiero esto para la cena.", "Esto no tiene sentido.", "Dame otra oportunidad con esto.", "No me gusta eso que dijiste."]:
        assert not _SCREEN.search(said), said
    for said in ["¿Qué es esto?", "mira esto", "explícame esta ventana", "what is this?", "toma una captura"]:
        assert _SCREEN.search(said), said


def test_run_without_capture_does_not_wait_for_a_forking_tool():
    import time

    from omadot.context import run

    t = time.perf_counter()
    # like wl-copy: a child keeps stdout open after the parent exits
    run(["sh", "-c", "sleep 3 & exit 0"], timeout=2.0, capture=False)
    assert time.perf_counter() - t < 1.0
