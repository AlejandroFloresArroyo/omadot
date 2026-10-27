from omadot import shortcut


def test_reads_the_double_super_that_summons_omadot():
    bindings = """
o.bind("SUPER + CTRL + PERIOD", "Emojis", "omarchy-launch-walker -m symbols")
o.bind("SUPER + Super_L", "OmaDot", os.getenv("HOME") .. "/dev/omadot/.venv/bin/omadot tap", { release = true })
"""
    assert shortcut(bindings) == "Super × 2"


def test_reads_a_bind_that_toggles_omadot():
    bindings = 'o.bind("SUPER + PERIOD", "OmaDot", os.getenv("HOME") .. "/dev/omadot/.venv/bin/omadot toggle")'
    assert shortcut(bindings) == "Super + ."


def test_no_bind_means_no_shortcut():
    assert shortcut('o.bind("SUPER + SPACE", "Launcher", "walker")') == ""
