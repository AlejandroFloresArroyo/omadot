"""`omadot setup TARGET`: puts OmaDot's pieces where the desktop finds them, pointing at the binary that runs it.

The card, the bar plugin, the speech servers and the echo-cancel config ship inside this package, so a
`uv tool install` and a clone's `uv sync` install the same way. Every external command goes through a
`run(argv, capture=False)` callable so tests can record them instead of touching the desktop.
"""

import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from omadot.hermes import read_env
from omadot.settings import ENGINES

PACKAGE = Path(__file__).resolve().parent
PLUGIN_SOURCE = PACKAGE / "omarchy-plugin"
SPEECH = PACKAGE / "speech"
ECHO_CANCEL = PACKAGE / "pipewire" / "omadot-echo-cancel.conf"
PLUGIN_ID = "ale.omadot"
# The bar plugin's only generated file: which `omadot` its clicks and settings run.
BIN_JS = "OmadotBin.js"
SERVICE = "omadot.service"
TORCH_INDEX = "https://download.pytorch.org/whl/cu128"
PROFILE_DESCRIPTION = "OmaDot voice assistant (fast voice turns)"
# The profile settings measured in the README's decision table ("Perfil de Hermes", "Aprobaciones de Hermes").
API_SERVER_TOOLSETS = ("clarify", "file", "memory", "session_search", "skills", "terminal", "todo", "vision", "web")
PROFILE_SETTINGS = (
    ("agent.reasoning_effort", "minimal"),
    ("memory.memory_enabled", "true"),
    ("memory.user_profile_enabled", "true"),
    ("moa.enabled", "false"),
    ("approvals.mode", "smart"),
    ("platform_toolsets.api_server", "[" + ", ".join(API_SERVER_TOOLSETS) + "]"),
)
TARGETS = ("echo-cancel", "kokoro", "omni", "hermes", "plugin", "service", "binds")

Run = Callable[..., str]


class SetupError(RuntimeError):
    """A setup step failed; the message says which command and why."""


def system(argv, capture: bool = False) -> str:
    """Runs argv; long installs stream to the terminal, `capture` returns stdout (for shell IPC answers)."""
    argv = [str(a) for a in argv]
    try:
        p = subprocess.run(argv, text=True, capture_output=capture, check=False)
    except FileNotFoundError as e:
        raise SetupError(f"{argv[0]} not found") from e
    if p.returncode != 0:
        detail = (p.stderr or p.stdout or "").strip() if capture else ""
        raise SetupError(detail or f"{' '.join(argv[:3])} failed (exit {p.returncode})")
    return (p.stdout or "").strip()


@dataclass(frozen=True)
class Paths:
    home: Path
    config: Path
    data: Path
    hermes: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Paths":
        home = Path(env.get("HOME") or Path.home())
        return cls(
            home=home,
            config=Path(env.get("XDG_CONFIG_HOME") or home / ".config"),
            data=Path(env.get("XDG_DATA_HOME") or home / ".local/share"),
            hermes=Path(env.get("HERMES_HOME") or home / ".hermes"),
        )

    @property
    def units(self) -> Path:
        return self.config / "systemd/user"

    @property
    def pipewire(self) -> Path:
        return self.config / "pipewire/pipewire.conf.d"

    @property
    def plugins(self) -> Path:
        # omarchy-shell reads third-party plugins from here, not from $XDG_CONFIG_HOME.
        return self.home / ".config/omarchy/plugins"

    def speech(self, engine: str) -> Path:
        return self.data / "omadot" / f"tts-{engine}"


def omadot_bin(executable: str = sys.executable) -> Path:
    """The `omadot` script of the environment this Python runs in (a uv tool venv or a clone's .venv).

    Not resolved: a venv's python is a symlink to the interpreter, and the script lives beside the link.
    """
    return Path(os.path.abspath(executable)).parent / "omadot"


def card_dir() -> Path:
    return PACKAGE / "card"


def _remove(path: Path) -> None:
    """Removes a file, a symlink (never its target) or a folder; nothing when absent."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _write(path: Path, text: str) -> None:
    """Writes a real file at `path`, replacing a symlink rather than writing through it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        path.unlink()
    path.write_text(text)


def _exec(*argv) -> str:
    """An ExecStart= command line: systemd splits on spaces, so a path with one is quoted."""
    return " ".join(f'"{a}"' if any(c.isspace() for c in str(a)) else str(a) for a in argv)


def _enable(unit: str, run: Run) -> None:
    """Enable and (re)start: a re-run over a running unit must pick up the new binary or server."""
    run(["systemctl", "--user", "daemon-reload"])
    run(["systemctl", "--user", "enable", unit])
    run(["systemctl", "--user", "restart", unit])


# --- bar plugin ----------------------------------------------------------------------------------------------


def plugin_bin(plugin: Path) -> str:
    """The binary a plugin folder runs, as its OmadotBin.js says."""
    text = (plugin / BIN_JS).read_text()
    return json.loads(text.split("var path = ", 1)[1].strip().rstrip(";"))


def _bin_js(binary: str) -> str:
    return (
        ".pragma library\n"
        "// The `omadot` the bar runs. `omadot setup plugin` writes the installed binary's absolute path here;\n"
        '// "omadot" (looked up on PATH) only in the copy shipped with the package.\n'
        f"var path = {json.dumps(binary)}\n"
    )


def install_plugin(binary: Path, paths: Paths, run: Run = system, *, sleep=time.sleep, enable: bool = True) -> list[str]:
    """Copies the plugin as real files (the validator refuses symlinks), staged and renamed in so the shell's
    watcher never sees half a plugin; then asks the running shell to rescan and enable it."""
    dest = paths.plugins / PLUGIN_ID
    staging = paths.plugins.parent / f".{PLUGIN_ID}.staging"
    _remove(staging)
    paths.plugins.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PLUGIN_SOURCE, staging, ignore=shutil.ignore_patterns(".*", "__pycache__"))
    (staging / BIN_JS).write_text(_bin_js(str(binary)))
    try:
        run(["omarchy-plugin-validate", staging], capture=True)
    except SetupError:
        _remove(staging)
        raise
    _remove(dest)
    staging.rename(dest)
    notes = [f"plugin copied to {dest}, running {binary}"]
    if not enable:
        return notes
    run(["omarchy-shell", "shell", "rescanPlugins"], capture=True)
    for _ in range(100):  # the rescan is asynchronous; omarchy-plugin-add waits about as long
        answer = run(["omarchy-shell", "shell", "enablePlugin", PLUGIN_ID, "{}"], capture=True)
        if answer == "ok":
            return [*notes, "Omi is in the bar"]
        if answer != "unknown":
            raise SetupError(f"omarchy-shell enablePlugin: {answer}")
        sleep(0.1)
    raise SetupError("the shell never listed the plugin; try `omarchy-shell shell rescanPlugins`")


# --- systemd units -------------------------------------------------------------------------------------------


def service_unit(binary: Path) -> str:
    return f"""# OmaDot voice assistant daemon (Omi), written by `omadot setup service`.
# Mic stays closed until Super is pressed twice or Omi is clicked.
# Logs: journalctl --user -u omadot -f
[Unit]
Description=OmaDot voice assistant (Omi)
After=graphical-session.target pipewire-pulse.service hermes-gateway.service omadot-tts-kokoro.service
Wants=hermes-gateway.service omadot-tts-kokoro.service
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart={_exec(binary)}
Restart=on-failure
RestartSec=3
# Ctrl+C equivalent: lets the pipeline, the card and the state file shut down cleanly.
KillSignal=SIGINT
TimeoutStopSec=10

[Install]
WantedBy=graphical-session.target
"""


def install_service(binary: Path, paths: Paths, run: Run = system, *, enable: bool = True) -> list[str]:
    unit = paths.units / SERVICE
    _write(unit, service_unit(binary))
    if enable:
        _enable(SERVICE, run)
    return [f"{unit} runs {binary}" + ("" if enable else f"; start it with: systemctl --user enable --now {SERVICE}")]


def install_echo_cancel(paths: Paths) -> list[str]:
    dest = paths.pipewire / ECHO_CANCEL.name
    _write(dest, ECHO_CANCEL.read_text())
    return [f"{dest} written; load it with: systemctl --user restart pipewire pipewire-pulse"]


# --- speech servers ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SpeechEngine:
    """An OpenAI-style speech server in its own venv: torch and the models never enter OmaDot's."""

    name: str
    server: str  # file under speech/
    torch: tuple[str, ...]
    packages: tuple[str, ...]
    args: tuple[str, ...] = ()
    environment: tuple[tuple[str, str], ...] = ()
    keep: tuple[str, ...] = ()  # copied only when absent, so the user's edits survive a re-run
    download: tuple[str, ...] = ()  # `hf download` arguments; empty = the server fetches on first start
    description: str = ""

    @property
    def unit(self) -> str:
        return f"omadot-tts-{self.name}.service"

    @property
    def port(self) -> int:
        return urlparse(ENGINES[self.name]).port


KOKORO_VOICES = ("ef_dora", "em_alex", "em_santa")
SPEECH_ENGINES = {
    "kokoro": SpeechEngine(
        name="kokoro",
        server="kokoro_server.py",
        torch=("torch",),
        packages=("kokoro==0.9.4", "fastapi", "uvicorn", "soundfile", "numpy", "huggingface-hub"),
        environment=(("LOCAL_TTS_PORT", str(urlparse(ENGINES["kokoro"]).port)), ("LOCAL_TTS_VOICE", "em_alex"), ("HF_HUB_OFFLINE", "1")),
        keep=("lexicon.json",),
        download=("hexgrad/Kokoro-82M", "config.json", "kokoro-v1_0.pth", *(f"voices/{v}.pt" for v in KOKORO_VOICES)),
        description="Kokoro-82M (CUDA), OpenAI-style",
    ),
    "omni": SpeechEngine(
        name="omni",
        server="tts_server.py",
        torch=("torch==2.8.0", "torchaudio==2.8.0"),
        packages=("omnivoice", "fastapi", "uvicorn", "soundfile", "numpy"),
        args=("--engine", "omni", "--port", str(urlparse(ENGINES["omni"]).port)),
        description="OmniVoice (CUDA), OpenAI-style",
    ),
}


def speech_unit(engine: SpeechEngine, home: Path) -> str:
    environment = list(engine.environment)
    if engine.download:  # the model `omadot setup` downloaded, wherever the service's HOME/XDG say
        environment.append(("HF_HUB_CACHE", home / "models"))
    env = "".join(f"Environment={_exec(f'{k}={v}')}\n" for k, v in environment)
    return f"""# OmaDot speech server, written by `omadot setup {engine.name}`.
[Unit]
Description=OmaDot speech server: {engine.description} on 127.0.0.1:{engine.port}
After=network.target

[Service]
Type=simple
WorkingDirectory={_exec(home)}
{env}ExecStart={_exec(home / ".venv/bin/python", home / engine.server, *engine.args)}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def install_speech(name: str, paths: Paths, run: Run = system, *, enable: bool = True) -> list[str]:
    engine = SPEECH_ENGINES[name]
    uv = shutil.which("uv")
    if not uv:
        raise SetupError("uv not found: https://docs.astral.sh/uv/")
    home = paths.speech(name)
    venv = home / ".venv"
    python = venv / "bin/python"
    if not python.exists():
        run([uv, "venv", "--python", "3.12", venv])
    run([uv, "pip", "install", "--python", python, "--index-url", TORCH_INDEX, *engine.torch])
    run([uv, "pip", "install", "--python", python, *engine.packages])
    _write(home / engine.server, (SPEECH / engine.server).read_text())
    for kept in engine.keep:
        if not (home / kept).exists():
            _write(home / kept, (SPEECH / kept).read_text())
    if engine.download:
        run([venv / "bin/hf", "download", "--cache-dir", home / "models", *engine.download])
    unit = paths.units / engine.unit
    _write(unit, speech_unit(engine, home))
    if enable:
        _enable(engine.unit, run)
    return [f"{unit} serves {engine.description} on 127.0.0.1:{engine.port} from {home}"]


# --- Hermes --------------------------------------------------------------------------------------------------


def hermes_plan(root_env: Mapping[str, str], profile_env: Mapping[str, str], profile: str, keygen) -> list[list[str]]:
    """The `hermes` commands that make the gateway serve `profile` at /p/<profile>/ with OmaDot's settings.

    The root (default) profile owns the one API listener; a named profile only brings its own key, which must
    differ from the root's (the gateway rejects the root key on a profile prefix).
    """
    root = ["hermes", "-p", "default", "config"]
    mine = ["hermes", "-p", profile, "config"]
    plan = []
    if root_env.get("API_SERVER_ENABLED", "").lower() != "true":
        plan.append([*root, "set", "API_SERVER_ENABLED", "true"])
    root_key = root_env.get("API_SERVER_KEY")
    if not root_key:
        root_key = keygen()
        plan.append([*root, "set", "API_SERVER_KEY", root_key])
    if "API_SERVER_ENABLED" in profile_env:
        plan.append([*mine, "unset", "API_SERVER_ENABLED"])
    if profile_env.get("API_SERVER_KEY") in (None, "", root_key):
        plan.append([*mine, "set", "API_SERVER_KEY", keygen()])
    plan += [[*mine, "set", "--force", key, value] for key, value in PROFILE_SETTINGS]
    return plan


def _shown(argv: list[str]) -> str:
    """A command as printed for a dry run, ready to paste: values that follow a *_KEY name are masked."""
    return shlex.join("***" if i and argv[i - 1].endswith("_KEY") else a for i, a in enumerate(argv))


def _has_model(profile_dir: Path) -> bool:
    import yaml

    try:
        config = yaml.safe_load((profile_dir / "config.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        return False
    model = config.get("model") or {}
    return bool(isinstance(model, dict) and model.get("provider") and model.get("default"))


def setup_hermes(
    paths: Paths,
    profile: str,
    run: Run = system,
    *,
    keygen=lambda: secrets.token_urlsafe(32),
    hermes: str | None = None,
    dry_run: bool = False,
) -> list[str]:
    """Creates the profile (cloned from the default one, so it inherits the provider credentials) once, then
    applies `hermes_plan`. Keys are generated here and never printed; a dry run lists the commands instead."""
    hermes = hermes or shutil.which("hermes")
    if not hermes:
        raise SetupError("Hermes Agent not found: install it from https://hermes-agent.nousresearch.com first")
    profile_dir = paths.hermes / "profiles" / profile
    root_env = read_env(paths.hermes / ".env")
    create = [["hermes", "profile", "create", profile, "--clone-from", "default", "--no-alias", "--description", PROFILE_DESCRIPTION]]
    create = create if not profile_dir.is_dir() else []
    if dry_run:
        # A fresh clone carries the root's .env; a dry run can only assume so.
        profile_env = dict(root_env) if create else read_env(profile_dir / ".env")
        notes = [f"$ {_shown(argv)}" for argv in create + hermes_plan(root_env, profile_env, profile, keygen)]
    else:
        for argv in create:
            run([hermes, *argv[1:]])
        for argv in hermes_plan(root_env, read_env(profile_dir / ".env"), profile, keygen):
            run([hermes, *argv[1:]])  # hermes reports each change itself
        notes = []
    if not _has_model(profile_dir):
        notes.append(f"pick the model OmaDot talks to: hermes -p {profile} model")
    notes.append("restart the gateway so it serves the profile: hermes gateway restart (first time: hermes gateway install)")
    return notes


# --- Hyprland ------------------------------------------------------------------------------------------------


def binds(binary: Path) -> str:
    """The lines for ~/.config/hypr/bindings.lua; printed, never written (the panel only reads that file).
    `omadot tap` on a lone Super release; the daemon turns two taps within 0.4 s into a toggle."""
    return (
        f'o.bind("SUPER + Super_L", "OmaDot", "{binary} tap", {{ release = true }})\n'
        f'o.bind("SUPER + SHIFT + PERIOD", "OmaDot: micrófono", "{binary} mute")\n'
    )


def setup(target: str, *, profile: str = "omadot", enable: bool = True, dry_run: bool = False) -> list[str]:
    """One `omadot setup TARGET` with the real paths and commands."""
    if dry_run and target != "hermes":
        raise SetupError("--dry-run is only for `omadot setup hermes`")
    paths = Paths.from_env()
    binary = omadot_bin()
    if target == "plugin":
        return install_plugin(binary, paths, enable=enable)
    if target == "service":
        return install_service(binary, paths, enable=enable)
    if target == "echo-cancel":
        return install_echo_cancel(paths)
    if target in SPEECH_ENGINES:
        return install_speech(target, paths, enable=enable)
    if target == "hermes":
        return setup_hermes(paths, profile, dry_run=dry_run)
    if target == "binds":
        return [binds(binary).rstrip("\n")]
    raise SetupError(f"unknown setup target {target!r}; one of: {', '.join(TARGETS)}")
