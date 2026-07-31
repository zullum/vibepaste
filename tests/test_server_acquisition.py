"""Tests for the rule that stops a second copy of a model being loaded.

The incident these guard against: 19 whisper-servers started, 4 stopped, and
the survivors — ~2.9GB each for large-v3 — filled a 24GB machine until it
had to be power-cycled. Every branch below is a decision that was previously
implicit in the spawn path, and therefore untestable.
"""

from src.server_acquisition import ADOPT, REFUSE, SPAWN, decide
from src.server_discovery import RunningServer


def server(pid=100, port=5000, model="ggml-large-v3.bin"):
    return RunningServer(pid=pid, model_path=model, port=port)


def healthy(*ports):
    return lambda port: port in ports


def owned(*pids):
    return lambda pid: pid in pids


def test_spawns_when_nothing_is_running():
    decision = decide([], healthy(), owned())

    assert decision.action == SPAWN
    assert decision.reclaim == ()


def test_adopts_a_healthy_server_instead_of_loading_the_model_again():
    """The whole point: a restart must not add a second 2.9GB copy."""
    existing = server(pid=42, port=5000)

    decision = decide([existing], healthy(5000), owned(42))

    assert decision.action == ADOPT
    assert decision.server is existing


def test_adopts_a_healthy_server_that_is_not_ours():
    """Using someone else's server is a read — same binary, same model."""
    existing = server(pid=42, port=5000)

    decision = decide([existing], healthy(5000), owned())

    assert decision.action == ADOPT
    assert decision.server is existing


def test_reclaims_our_own_wedged_server_and_then_spawns():
    """A wedged server still holds the RAM, so it must be killed, not left."""
    decision = decide([server(pid=42, port=5000)], healthy(), owned(42))

    assert decision.action == SPAWN
    assert decision.reclaim == (42,)


def test_refuses_when_a_wedged_server_is_not_ours_to_kill():
    """Spawning alongside it is precisely how one orphan became fifteen."""
    stranger = server(pid=42, port=5000)

    decision = decide([stranger], healthy(), owned())

    assert decision.action == REFUSE
    assert decision.blocker is stranger


def test_a_healthy_server_wins_even_when_a_wedged_one_is_also_present():
    wedged = server(pid=1, port=5000)
    working = server(pid=2, port=5001)

    decision = decide([wedged, working], healthy(5001), owned(1, 2))

    assert decision.action == ADOPT
    assert decision.server is working


def test_reclaims_every_server_of_ours_before_refusing_for_a_stranger():
    """A refusal must not leave our own dead weight resident."""
    ours = server(pid=1, port=5000)
    stranger = server(pid=2, port=5001)

    decision = decide([ours, stranger], healthy(), owned(1))

    assert decision.action == REFUSE
    assert decision.reclaim == (1,)
