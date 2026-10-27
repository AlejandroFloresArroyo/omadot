import signal

from omadot import CONTROL_SIGNALS, unblock_control_signals


def test_control_signals_are_unblocked_even_if_inherited_blocked():
    # Some terminals start children with SIGUSR1 blocked; `omadot toggle` (and retry/reload) would never arrive.
    signal.pthread_sigmask(signal.SIG_BLOCK, CONTROL_SIGNALS)
    try:
        unblock_control_signals()
        assert not CONTROL_SIGNALS & signal.pthread_sigmask(signal.SIG_BLOCK, set())
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, CONTROL_SIGNALS)
