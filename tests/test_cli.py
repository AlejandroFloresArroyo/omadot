import pytest

from omadot import SOCKET_COMMANDS


def test_positional_params_become_request_fields():
    c = SOCKET_COMMANDS
    assert c["drop"](["/tmp/a.pdf"]) == {"path": "/tmp/a.pdf"}
    assert c["ask"](["abre", "herdr", "en", "omadot"]) == {"text": "abre herdr en omadot"}
    assert c["approve"]([]) == {} and c["approve"](["deny"]) == {"choice": "deny"}
    assert c["progress"](["vid", "40", "Convirtiendo", "2/6"]) == {"id": "vid", "pct": "40", "label": "Convirtiendo", "detail": "2/6"}
    assert c["action"](["reminder", "r1", "snooze"]) == {"kind": "reminder", "id": "r1", "op": "snooze"}
    assert c["remind"](["2026-11-04T09:00", "pagar", "la", "factura"]) == {"at": "2026-11-04T09:00", "text": "pagar la factura"}


def test_missing_params_explain_the_usage():
    with pytest.raises(ValueError, match="usage: omadot … PATH"):
        SOCKET_COMMANDS["drop"]([])


def test_run_does_not_shadow_the_desktop_context_module():
    # `run()` builds an LLMContext named `context`; the enrichment must come from omadot.context, not from it.
    import inspect

    import omadot

    src = inspect.getsource(omadot.run)
    assert "from omadot import context as desktop" in src and "desktop.enrich" in src
    assert "context.enrich" not in src


def test_a_required_param_keeps_an_empty_value_so_the_handler_can_reject_it():
    assert SOCKET_COMMANDS["progress"](["taskid", "", "Label"]) == {"id": "taskid", "pct": "", "label": "Label"}
    assert SOCKET_COMMANDS["progress"](["taskid", "40", "", ""]) == {"id": "taskid", "pct": "40"}


def test_unmuting_never_opens_the_mic():
    # A tester ran `omadot mute off` and the room was listened to: unmute must only clear the flag.
    import inspect

    import omadot

    src = inspect.getsource(omadot.run)
    i = src.index("def on_mute(")
    body = src[i:i + 400]
    assert "gate.open = True" not in body and "gate.open = not muted" not in body
