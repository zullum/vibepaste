"""Unloads an idle whisper-server so its model memory comes back.

Runs as a daemon thread inside the app process, which is the important
limitation: it dies with its parent. An orphaned server therefore never
unloads itself, which is why reclaiming one is the *next* run's job (see
whisper_server's adopt-and-reclaim path) rather than something this thread
can ever be relied on to do.
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

REAP_INTERVAL_SECONDS = 30


def start_idle_reaper(server, interval=REAP_INTERVAL_SECONDS):
    """Poll `server.reap_if_idle()` until it reports it is finished."""

    def reap():
        while True:
            time.sleep(interval)
            try:
                if server.reap_if_idle():
                    return
            except Exception as e:  # never let the thread die silently
                logger.error(f"Idle reaper failed: {e}", exc_info=True)
                return

    thread = threading.Thread(target=reap, name="whisper-reaper", daemon=True)
    thread.start()
    return thread
