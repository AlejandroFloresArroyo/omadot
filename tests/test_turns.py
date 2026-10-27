from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy

from omadot import turn_strategies


def test_smart_turn_gives_up_after_one_second_of_silence():
    # Smart Turn marked "Hola, ¿cómo estás?" incomplete and the default 3 s fallback ate the turn.
    (stop,) = turn_strategies().stop
    assert isinstance(stop, TurnAnalyzerUserTurnStopStrategy)
    analyzer = stop._turn_analyzer
    assert isinstance(analyzer, LocalSmartTurnAnalyzerV3)
    assert analyzer.params.stop_secs == 1.0
