from pipecat.pipeline.pipeline import Pipeline

from omadot import make_task


def test_daemon_task_never_idles_out():
    # OmaDot waits to be summoned indefinitely; Pipecat's default would cancel it after 5 idle minutes.
    task = make_task(Pipeline([]), observers=[])
    assert task._idle_timeout_secs is None
