"""Tests that one hung callback cannot take every later hotkey with it.

Measured twice on a real machine, both times inside CoreAudio: the single
dispatch worker blocked forever in HALB_Mutex::Lock, every later press piled
up in the queue unprocessed, and the app looked completely healthy — menu
bar responsive, event tap delivering — while no hotkey did anything.
"""

import threading
import time

from src.hotkey_dispatch import HotkeyDispatcher


def dispatcher(callbacks, **kwargs):
    kwargs.setdefault("stuck_seconds", 0.05)
    kwargs.setdefault("check_interval", 0.02)
    return HotkeyDispatcher(resolve=callbacks.get, **kwargs)


def test_a_callback_runs_off_the_calling_thread():
    seen = []
    done = threading.Event()
    d = dispatcher({"go": lambda name: (seen.append(
        threading.current_thread() is threading.main_thread()), done.set())})
    d.start()
    try:
        d.submit("go")
        assert done.wait(2)
        assert seen == [False]
    finally:
        d.stop()


def test_an_unregistered_name_is_ignored():
    d = dispatcher({})
    d.start()
    try:
        d.submit("nothing")
        time.sleep(0.1)
        assert d.worker_count() == 1
    finally:
        d.stop()


def test_a_hung_callback_does_not_stop_later_hotkeys():
    """The whole point. The stop hotkey has to keep working even when the
    press before it wedged the worker inside CoreAudio."""
    wedged = threading.Event()
    later_ran = threading.Event()
    d = dispatcher({
        "wedge": lambda name: wedged.wait(10),   # never returns in time
        "later": lambda name: later_ran.set(),
    })
    d.start()
    try:
        d.submit("wedge")
        time.sleep(0.1)          # let it get stuck
        d.submit("later")

        assert later_ran.wait(2), "a hung callback swallowed every later hotkey"
    finally:
        wedged.set()
        d.stop()


def test_a_replacement_worker_is_started_for_a_stuck_callback():
    wedged = threading.Event()
    d = dispatcher({"wedge": lambda name: wedged.wait(10)})
    d.start()
    try:
        assert d.worker_count() == 1
        d.submit("wedge")

        deadline = time.monotonic() + 2
        while d.worker_count() < 2 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert d.worker_count() == 2
    finally:
        wedged.set()
        d.stop()


def test_replacements_are_capped():
    """Each replacement leaks the thread it replaces — they are blocked in C
    and cannot be killed — so a permanently broken callback must not be able
    to spawn threads without end."""
    wedged = threading.Event()
    d = dispatcher({"wedge": lambda name: wedged.wait(30)}, max_workers=3)
    d.start()
    try:
        for _ in range(10):
            d.submit("wedge")
        time.sleep(0.5)

        assert d.worker_count() == 3
    finally:
        wedged.set()
        d.stop()


def test_a_healthy_slow_callback_does_not_spawn_workers():
    """Only a callback past the stuck threshold counts; ordinary work must
    not quietly grow the pool."""
    done = threading.Event()
    d = dispatcher({"slow": lambda name: (time.sleep(0.01), done.set())},
                   stuck_seconds=5.0)
    d.start()
    try:
        d.submit("slow")
        assert done.wait(2)
        time.sleep(0.1)

        assert d.worker_count() == 1
    finally:
        d.stop()


def test_a_raising_callback_does_not_kill_the_worker():
    calls = []
    done = threading.Event()

    def callback(name):
        calls.append(name)
        if len(calls) == 1:
            raise RuntimeError("boom")
        done.set()

    d = dispatcher({"go": callback})
    d.start()
    try:
        d.submit("go")
        d.submit("go")
        assert done.wait(2), "the worker died on the first exception"
        assert calls == ["go", "go"]
    finally:
        d.stop()


def test_nothing_is_reported_stuck_when_idle():
    d = dispatcher({})
    d.start()
    try:
        assert d.stuck_for() == 0.0
    finally:
        d.stop()
