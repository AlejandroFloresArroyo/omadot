"""Quickshell must load the card's QML: qmllint misses duplicate property assignments that make qs refuse the file."""
import os
import shutil
import subprocess

import pytest

from omadot.install import card_dir

CARD = card_dir()


@pytest.mark.skipif(not shutil.which("qs") or not os.environ.get("WAYLAND_DISPLAY"), reason="needs Quickshell on a Wayland session")
def test_quickshell_loads_the_card():
    p = subprocess.run(["timeout", "5", "qs", "-p", str(CARD)], capture_output=True, text=True, timeout=15, check=False)
    out = p.stdout + p.stderr
    assert "Failed to load configuration" not in out and "ERROR" not in out, out[-1500:]
