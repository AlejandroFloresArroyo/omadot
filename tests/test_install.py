"""`omadot setup …`: what a fresh install writes and runs, against a temp HOME and a recorded command runner."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from omadot import install, shortcut
from omadot.install import Paths, SetupError

BIN = Path("/opt/tools/omadot/bin/omadot")


class Recorder:
    """Stands in for subprocess: records each argv and answers from a script keyed by the first two words."""

    def __init__(self, answers=None):
        self.calls: list[list[str]] = []
        self.answers = answers or {}

    def __call__(self, argv, capture=False):
        argv = [str(a) for a in argv]
        self.calls.append(argv)
        answer = self.answers.get(tuple(argv[:3])) or self.answers.get(tuple(argv[:2])) or ""
        return answer.pop(0) if isinstance(answer, list) else answer


@pytest.fixture
def paths(tmp_path):
    return Paths.from_env({"HOME": str(tmp_path / "home")})


# --- where things live ---------------------------------------------------------------------------------------


def test_paths_follow_xdg_and_hermes_home(tmp_path):
    p = Paths.from_env({"HOME": "/h", "XDG_CONFIG_HOME": "/c", "XDG_DATA_HOME": "/d", "HERMES_HOME": "/hh"})
    assert p.units == Path("/c/systemd/user")
    assert p.pipewire == Path("/c/pipewire/pipewire.conf.d")
    assert p.hermes == Path("/hh")
    assert p.speech("kokoro") == Path("/d/omadot/tts-kokoro")
    # omarchy-shell reads plugins from ~/.config/omarchy/plugins whatever XDG_CONFIG_HOME says.
    assert p.plugins == Path("/h/.config/omarchy/plugins")
    d = Paths.from_env({"HOME": "/h"})
    assert (d.units, d.hermes) == (Path("/h/.config/systemd/user"), Path("/h/.hermes"))


def test_the_binary_is_the_script_next_to_the_running_python_unresolved(tmp_path):
    # A venv's python is a symlink to the real interpreter; the `omadot` script lives beside the link.
    venv = tmp_path / "tools" / "omadot" / "bin"
    venv.mkdir(parents=True)
    (venv / "python").symlink_to(shutil.which("python3") or "/usr/bin/python3")
    assert install.omadot_bin(str(venv / "python")) == venv / "omadot"


def test_the_card_ships_inside_the_package_with_a_real_omi():
    card = install.card_dir()
    assert card.parent == Path(install.__file__).resolve().parent
    assert (card / "shell.qml").is_file()
    omi = (card / "Omi.qml").read_text()
    assert omi == (install.PLUGIN_SOURCE / "Omi.qml").read_text() and "Omi" in omi


def test_no_shipped_file_assumes_a_clone_layout():
    shipped = [*install.card_dir().iterdir(), *install.PLUGIN_SOURCE.iterdir(), install.ECHO_CANCEL]
    for f in shipped:
        text = f.read_text()
        assert ".venv" not in text and "dev/omadot" not in text, f


# --- the card ------------------------------------------------------------------------------------------------


def test_the_daemon_starts_the_card_with_the_binary_it_runs_from(monkeypatch):
    import omadot

    seen = {}

    def popen(argv, **kw):
        seen["argv"], seen["env"] = argv, kw.get("env")
        return "card"

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/qs" if name == "qs" else None)
    monkeypatch.setattr(subprocess, "Popen", popen)
    assert omadot._start_card() == "card"
    assert seen["argv"] == ["/usr/bin/qs", "-p", str(install.card_dir())]
    assert seen["env"]["OMADOT_BIN"] == str(install.omadot_bin())


def test_the_card_takes_its_binary_from_the_environment():
    qml = (install.card_dir() / "shell.qml").read_text()
    assert 'Quickshell.env("OMADOT_BIN")' in qml


# --- the bar plugin ------------------------------------------------------------------------------------------


def test_the_shipped_plugin_runs_omadot_from_path_until_installed():
    assert install.plugin_bin(install.PLUGIN_SOURCE) == "omadot"
    bar = (install.PLUGIN_SOURCE / "BarWidget.qml").read_text()
    assert f'import "{install.BIN_JS}" as OmadotBin' in bar and "OmadotBin.path" in bar


def test_plugin_install_copies_real_files_pointing_at_the_binary(paths):
    run = Recorder({("omarchy-shell", "shell", "enablePlugin"): ["unknown", "ok"]})
    slept = []
    install.install_plugin(BIN, paths, run, sleep=slept.append)
    dest = paths.plugins / install.PLUGIN_ID
    assert sorted(p.name for p in dest.iterdir()) == sorted(p.name for p in install.PLUGIN_SOURCE.iterdir())
    assert not any(p.is_symlink() for p in dest.iterdir())
    assert install.plugin_bin(dest) == str(BIN)
    assert json.loads((dest / "manifest.json").read_text())["id"] == install.PLUGIN_ID
    assert run.calls[0][0] == "omarchy-plugin-validate"
    assert run.calls[1:] == [
        ["omarchy-shell", "shell", "rescanPlugins"],
        ["omarchy-shell", "shell", "enablePlugin", install.PLUGIN_ID, "{}"],
        ["omarchy-shell", "shell", "enablePlugin", install.PLUGIN_ID, "{}"],
    ]
    assert len(slept) == 1  # the rescan is asynchronous: "unknown" until the shell has seen the folder
    assert not list(paths.plugins.parent.glob(".*staging*"))


def test_plugin_install_replaces_a_development_symlink_without_touching_its_target(paths, tmp_path):
    clone = tmp_path / "clone" / "omarchy-plugin"
    shutil.copytree(install.PLUGIN_SOURCE, clone)
    paths.plugins.mkdir(parents=True)
    (paths.plugins / install.PLUGIN_ID).symlink_to(clone)
    install.install_plugin(BIN, paths, Recorder(), enable=False)
    dest = paths.plugins / install.PLUGIN_ID
    assert dest.is_dir() and not dest.is_symlink()
    assert install.plugin_bin(clone) == "omadot"  # the clone was not rewritten through the link


def test_plugin_install_without_enable_runs_no_shell_command(paths):
    run = Recorder()
    install.install_plugin(BIN, paths, run, enable=False)
    assert [c[0] for c in run.calls] == ["omarchy-plugin-validate"]


def test_an_invalid_plugin_is_not_installed(paths):
    def run(argv, capture=False):
        if argv[0] == "omarchy-plugin-validate":
            raise SetupError("omarchy-plugin-validate: symlinks are not allowed")
        return ""

    with pytest.raises(SetupError, match="symlinks"):
        install.install_plugin(BIN, paths, run)
    assert not (paths.plugins / install.PLUGIN_ID).exists()
    assert not list(paths.plugins.parent.glob(".*staging*"))


def test_the_shell_refusing_the_plugin_is_an_error(paths):
    run = Recorder({("omarchy-shell", "shell", "enablePlugin"): ["bad placement"]})
    with pytest.raises(SetupError, match="bad placement"):
        install.install_plugin(BIN, paths, run, sleep=lambda s: None)


@pytest.mark.skipif(not shutil.which("omarchy-plugin-validate"), reason="needs Omarchy's plugin validator")
def test_the_installed_plugin_passes_omarchys_validator(paths):
    install.install_plugin(BIN, paths, install.system, enable=False)


# --- the systemd units ---------------------------------------------------------------------------------------


def test_the_service_runs_the_installed_binary():
    unit = install.service_unit(BIN)
    assert f"ExecStart={BIN}\n" in unit
    assert "WorkingDirectory" not in unit and ".venv" not in unit
    assert "KillSignal=SIGINT" in unit and "WantedBy=graphical-session.target" in unit
    assert "Wants=hermes-gateway.service omadot-tts-kokoro.service" in unit


def test_a_path_with_spaces_is_quoted_for_systemd():
    assert 'ExecStart="/home/a b/omadot"\n' in install.service_unit(Path("/home/a b/omadot"))


def test_service_install_writes_the_unit_enables_it_and_restarts_it(paths):
    # restart, not `enable --now`: re-running setup over a running daemon must swap it for the new binary.
    run = Recorder()
    install.install_service(BIN, paths, run)
    assert (paths.units / "omadot.service").read_text() == install.service_unit(BIN)
    assert run.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "omadot.service"],
        ["systemctl", "--user", "restart", "omadot.service"],
    ]


def test_service_install_replaces_a_linked_unit_without_writing_through_it(paths, tmp_path):
    # `systemctl --user link` left a symlink into a clone: writing through it would edit the clone.
    linked = tmp_path / "clone-omadot.service"
    linked.write_text("old")
    paths.units.mkdir(parents=True)
    (paths.units / "omadot.service").symlink_to(linked)
    install.install_service(BIN, paths, Recorder(), enable=False)
    assert linked.read_text() == "old"
    assert not (paths.units / "omadot.service").is_symlink()


def test_service_install_without_enable_runs_nothing(paths):
    run = Recorder()
    install.install_service(BIN, paths, run, enable=False)
    assert run.calls == []


# --- echo cancellation ---------------------------------------------------------------------------------------


def test_echo_cancel_lands_in_pipewire_conf_d(paths):
    install.install_echo_cancel(paths)
    assert (paths.pipewire / "omadot-echo-cancel.conf").read_text() == install.ECHO_CANCEL.read_text()


# --- the speech servers --------------------------------------------------------------------------------------


def test_kokoro_setup_builds_its_own_venv_downloads_the_model_and_runs_offline(paths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    run = Recorder()
    install.install_speech("kokoro", paths, run)
    home = paths.speech("kokoro")
    py = home / ".venv/bin/python"
    assert run.calls[0] == ["/usr/bin/uv", "venv", "--python", "3.12", str(home / ".venv")]
    assert run.calls[1] == ["/usr/bin/uv", "pip", "install", "--python", str(py), "--index-url", install.TORCH_INDEX, "torch"]
    assert run.calls[2][:5] == ["/usr/bin/uv", "pip", "install", "--python", str(py)] and "kokoro==0.9.4" in run.calls[2]
    # The model lands next to the engine and the unit reads it from there: the service's environment (no
    # XDG_CACHE_HOME/HF_HOME from your shell) still finds it offline.
    assert run.calls[3] == [
        str(home / ".venv/bin/hf"), "download", "--cache-dir", str(home / "models"), "hexgrad/Kokoro-82M",
        "config.json", "kokoro-v1_0.pth", "voices/ef_dora.pt", "voices/em_alex.pt", "voices/em_santa.pt",
    ]
    assert run.calls[4:] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "omadot-tts-kokoro.service"],
        ["systemctl", "--user", "restart", "omadot-tts-kokoro.service"],
    ]
    assert (home / "kokoro_server.py").read_text() == (install.SPEECH / "kokoro_server.py").read_text()
    assert json.loads((home / "lexicon.json").read_text())
    unit = (paths.units / "omadot-tts-kokoro.service").read_text()
    assert f"ExecStart={py} {home / 'kokoro_server.py'}\n" in unit
    assert "Environment=LOCAL_TTS_PORT=8880\n" in unit and "Environment=HF_HUB_OFFLINE=1\n" in unit
    assert f"Environment=HF_HUB_CACHE={home / 'models'}\n" in unit


def test_speech_setup_keeps_an_edited_lexicon_and_an_existing_venv(paths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    home = paths.speech("kokoro")
    (home / ".venv/bin").mkdir(parents=True)
    (home / ".venv/bin/python").write_text("")
    (home / "lexicon.json").write_text('{"Omarchy": "omárchi"}')
    run = Recorder()
    install.install_speech("kokoro", paths, run, enable=False)
    assert "venv" not in [c[1] for c in run.calls]
    assert (home / "lexicon.json").read_text() == '{"Omarchy": "omárchi"}'
    assert all(c[0] != "systemctl" for c in run.calls)


def test_omnivoice_setup_listens_where_the_tts_setting_expects_it(paths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/uv")
    install.install_speech("omni", paths, Recorder(), enable=False)
    home = paths.speech("omni")
    unit = (paths.units / "omadot-tts-omni.service").read_text()
    assert f"ExecStart={home / '.venv/bin/python'} {home / 'tts_server.py'} --engine omni --port 8882\n" in unit


def test_speech_setup_needs_uv(paths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SetupError, match="uv"):
        install.install_speech("kokoro", paths, Recorder())


# --- Hermes --------------------------------------------------------------------------------------------------


def keys():
    n = iter(range(100))
    return lambda: f"key{next(n)}"



def test_hermes_plan_turns_on_the_api_server_and_gives_the_profile_its_own_key():
    plan = install.hermes_plan({}, {}, "omadot", keys())
    assert ["hermes", "-p", "default", "config", "set", "API_SERVER_ENABLED", "true"] in plan
    assert ["hermes", "-p", "default", "config", "set", "API_SERVER_KEY", "key0"] in plan
    assert ["hermes", "-p", "omadot", "config", "set", "API_SERVER_KEY", "key1"] in plan


def test_hermes_plan_applies_the_tuned_voice_profile():
    plan = install.hermes_plan({"API_SERVER_ENABLED": "true", "API_SERVER_KEY": "r"}, {"API_SERVER_KEY": "p"}, "omadot", keys())
    profile = {c[-2]: c[-1] for c in plan if c[:3] == ["hermes", "-p", "omadot"]}
    assert profile == {
        "agent.reasoning_effort": "minimal",
        "memory.memory_enabled": "true",
        "memory.user_profile_enabled": "true",
        "moa.enabled": "false",
        "approvals.mode": "smart",
        "platform_toolsets.api_server": "[clarify, file, memory, session_search, skills, terminal, todo, vision, web]",
    }
    assert not [c for c in plan if c[2] == "default"]  # the root is left alone when it already serves


def test_a_cloned_root_key_or_listener_is_not_reused_by_the_profile():
    # `--clone-from default` copies the root .env: the gateway rejects the root key on /p/<profile>/, and a
    # secondary must not enable its own api_server.
    root = {"API_SERVER_ENABLED": "true", "API_SERVER_KEY": "same"}
    plan = install.hermes_plan(root, dict(root), "omadot", keys())
    assert ["hermes", "-p", "omadot", "config", "unset", "API_SERVER_ENABLED"] in plan
    assert ["hermes", "-p", "omadot", "config", "set", "API_SERVER_KEY", "key0"] in plan


def test_setup_hermes_creates_the_profile_once_and_never_prints_keys(paths):
    run = Recorder()
    paths.hermes.mkdir(parents=True)
    (paths.hermes / ".env").write_text("API_SERVER_ENABLED=true\nAPI_SERVER_KEY=rootsecret\n")
    notes = install.setup_hermes(paths, "omadot", run, keygen=lambda: "s3cret", hermes="hermes")
    assert run.calls[0] == ["hermes", "profile", "create", "omadot", "--clone-from", "default", "--no-alias",
                            "--description", install.PROFILE_DESCRIPTION]
    assert ["hermes", "-p", "omadot", "config", "set", "API_SERVER_KEY", "s3cret"] in run.calls
    assert "s3cret" not in "\n".join(notes) and "rootsecret" not in "\n".join(notes)
    assert any("hermes -p omadot model" in n for n in notes)  # the cloned profile has no model yet
    assert not any(n.startswith("$ ") for n in notes)  # hermes reports each change itself, as it runs
    run.calls.clear()
    (paths.hermes / "profiles/omadot").mkdir(parents=True)
    (paths.hermes / "profiles/omadot/.env").write_text("API_SERVER_KEY=s3cret\n")
    (paths.hermes / "profiles/omadot/config.yaml").write_text("model:\n  provider: anthropic\n  default: x\n")
    notes = install.setup_hermes(paths, "omadot", run, keygen=lambda: "other", hermes="hermes")
    assert not [c for c in run.calls if "create" in c or "API_SERVER_KEY" in c]
    assert not any("hermes -p omadot model" in n for n in notes)


def test_setup_hermes_dry_run_lists_the_commands_masked_and_runs_nothing(paths):
    run = Recorder()
    notes = install.setup_hermes(paths, "omadot", run, keygen=lambda: "s3cret", hermes="hermes", dry_run=True)
    assert run.calls == []
    text = "\n".join(notes)
    assert "hermes profile create omadot" in text and "API_SERVER_KEY '***'" in text and "s3cret" not in text
    assert f"--description '{install.PROFILE_DESCRIPTION}'" in text  # printed so it can be pasted


def test_setup_hermes_needs_hermes(paths, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(SetupError, match="Hermes Agent"):
        install.setup_hermes(paths, "omadot", Recorder())


def test_status_and_the_daemon_honour_hermes_home(monkeypatch, tmp_path):
    import argparse

    import omadot

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hh"))
    args = argparse.Namespace(hermes="http://127.0.0.1:8642", profile="omadot")
    assert omadot._hermes_target(args) == ("http://127.0.0.1:8642/p/omadot", tmp_path / "hh/profiles/omadot")


# --- binds ---------------------------------------------------------------------------------------------------


def test_binds_use_the_installed_binary_and_the_panel_still_reads_them():
    lines = install.binds(BIN)
    assert f'o.bind("SUPER + Super_L", "OmaDot", "{BIN} tap", {{ release = true }})' in lines
    assert f'o.bind("SUPER + SHIFT + PERIOD", "OmaDot: micrófono", "{BIN} mute")' in lines
    assert shortcut(lines) == "Super × 2"


# --- the command line ----------------------------------------------------------------------------------------


def test_setup_is_a_command(monkeypatch, tmp_path, capsys):
    import sys

    import omadot

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(sys, "argv", ["omadot", "setup", "service", "--no-enable"])
    omadot.main()
    assert (tmp_path / ".config/systemd/user/omadot.service").read_text() == install.service_unit(install.omadot_bin())


def test_setup_rejects_an_unknown_target(monkeypatch):
    import sys

    import omadot

    monkeypatch.setattr(sys, "argv", ["omadot", "setup", "everything"])
    with pytest.raises(SystemExit):
        omadot.main()
