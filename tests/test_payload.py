from omadot.payload import MARK, holdback, parse


def _exists(paths):
    return lambda p: p in paths


def test_plain_reply_has_no_card():
    r = parse("Son las tres y media.")
    assert r.spoken == "Son las tres y media." and r.card == {}


def test_marker_line_is_split_off_and_filtered(tmp_path):
    f = tmp_path / "finance-2026.xlsx"
    f.write_text("x")
    text = f'Aquí está el archivo. {MARK} {{"files": ["{f}"], "url": "https://omarchy.org", "bogus": 1, "draft": {{}}}}'
    r = parse(text)
    assert r.spoken == "Aquí está el archivo."
    assert r.card == {"files": [{"path": str(f), "label": "finance-2026.xlsx"}], "url": "https://omarchy.org"}


def test_media_tags_and_bare_paths_become_tiles_only_if_they_exist():
    text = "Te lo dejo en MEDIA:/home/ale/Documents/Finance/invoice.pdf y también /tmp/nope.pdf y ~/Documents/plan.md"
    r = parse(text, exists=_exists({"/home/ale/Documents/Finance/invoice.pdf", "/home/ale/Documents/plan.md"}))
    assert r.spoken == "Te lo dejo en y también /tmp/nope.pdf y ~/Documents/plan.md"
    assert [f["label"] for f in r.card["files"]] == ["invoice.pdf", "plan.md"]


def test_broken_json_keeps_the_spoken_part():
    r = parse(f"Listo. {MARK} {{not json")
    assert r.spoken == "Listo." and r.card == {}


def test_holdback_never_lets_the_marker_reach_the_tts():
    assert holdback("Hola ") == ("Hola ", "")
    assert holdback("Hola @") == ("Hola ", "@")
    assert holdback("Hola @@om") == ("Hola ", "@@om")
    assert holdback(f"Hola {MARK} {{") == ("Hola ", f"{MARK} {{")
    assert holdback("mail@example.com dice") == ("mail@example.com dice", "")


def test_a_second_marker_adds_to_the_card_and_trailing_words_are_kept_for_the_voice():
    r = parse(f'Primero. {MARK} {{"url": "https://a.com"}} Segundo. {MARK} {{"ask": {{"question": "¿Sigo?"}}}} Gracias por esperar.')
    assert r.card == {"url": "https://a.com", "ask": {"question": "¿Sigo?"}}
    assert r.spoken == "Primero. Segundo. Gracias por esperar."
    assert r.tail == "Segundo. Gracias por esperar."


def test_fenced_json_and_a_single_file_as_a_string(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_text("x")
    r = parse(f'Aquí está. {MARK} ```json\n{{"files": "{f}"}}\n```')
    assert r.spoken == "Aquí está." and r.card == {"files": [{"path": str(f), "label": "a.pdf"}]}
    r = parse(f'Aquí está. {MARK} {{"files": "/etc/hostname"}}', exists=lambda p: p == "/etc/hostname")
    assert [x["path"] for x in r.card["files"]] == ["/etc/hostname"]  # not one tile per character


def test_a_non_object_card_is_dropped_but_the_voice_survives():
    r = parse(f'Hecho. {MARK} [1, 2] y listo.')
    assert r.card == {} and r.spoken == "Hecho. y listo."


def test_the_marker_survives_being_emphasised():
    r = parse(f'Listo. **{MARK}** {{"url": "https://a.com"}}')
    assert r.card == {"url": "https://a.com"} and r.spoken == "Listo."
