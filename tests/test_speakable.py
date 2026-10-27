from omadot.kokoro import speakable


def test_strips_markdown_the_voice_would_read_aloud():
    assert speakable("**Listo**, abrí `~/dev`.") == "Listo, abrí ~/dev."
    assert speakable("## Pasos\n- uno\n- dos") == "Pasos\nuno\ndos"
    assert speakable("Mira [la doc](https://x.y/z) 🎉") == "Mira la doc"


def test_leaves_plain_text_alone():
    assert speakable("Hoy es sábado.") == "Hoy es sábado."
