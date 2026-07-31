"""Decides which whisper-server should serve a model.

Kept free of processes, sockets and state so the rule that matters can be
tested directly: **never load a second copy of the same model.** Fifteen
concurrent copies of large-v3 once filled a 24GB machine, and the reason it
went unnoticed is that the decision was implicit in the spawn path. Here it
is an explicit value.
"""

from dataclasses import dataclass, field

ADOPT = "adopt"
SPAWN = "spawn"
REFUSE = "refuse"


@dataclass(frozen=True)
class Decision:
    """What to do about the servers currently holding a model."""

    action: str
    server: object = None          # the RunningServer to adopt
    reclaim: tuple = field(default_factory=tuple)  # pids to kill first
    blocker: object = None         # the server that forced a refusal


def decide(existing, is_healthy, owns):
    """Choose between adopting, spawning and refusing.

    Args:
        existing: RunningServers already holding this model.
        is_healthy: port -> bool, whether a server can serve requests.
        owns: pid -> bool, whether we are allowed to kill it.

    A healthy server is always preferred, whoever started it: reusing one is
    a read, and it is the same binary serving the same model. An unhealthy
    one is killed only if it is ours. If any unhealthy server survives that,
    we refuse — spawning alongside it is exactly what turned one orphan into
    fifteen, and Transcriber can still fall back to whisper-cli.
    """
    for server in existing:
        if is_healthy(server.port):
            return Decision(ADOPT, server=server)

    reclaim = tuple(s.pid for s in existing if owns(s.pid))
    blocked = [s for s in existing if not owns(s.pid)]
    if blocked:
        return Decision(REFUSE, blocker=blocked[0], reclaim=reclaim)
    return Decision(SPAWN, reclaim=reclaim)
