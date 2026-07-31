"""End-to-end tests for adopt-and-reclaim, against real processes.

These deliberately cross the process boundary. Every in-process part of
WhisperServer was already correct when 40GB of orphaned servers froze the
machine — what failed was the boundary itself, so a suite that only ever
tests in-process logic reproduces the original blind spot.

A stub stands in for the real binary: it serves /health exactly as
whisper-server does, without the 2.9GB of weights. It is named
`whisper-server` because discovery finds servers by command line.
"""

import os
import socket
import subprocess
import sys
import time

import pytest

from src.process_util import process_exists
from src.server_discovery import running_servers
from src.server_ownership import OwnershipRegistry
from src.whisper_server import WhisperServer

STUB = '''#!{python}
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

port = int(sys.argv[sys.argv.index("--port") + 1])


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{"status": "ok"}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


@pytest.fixture
def stub_binary(tmp_path):
    path = tmp_path / "whisper-server"
    path.write_text(STUB.format(python=sys.executable))
    path.chmod(0o755)
    return path


@pytest.fixture
def model(tmp_path):
    """WhisperServer refuses to start when the model file is missing."""
    path = tmp_path / "ggml-test-model.bin"
    path.write_bytes(b"not really a model")
    return path


@pytest.fixture
def registry(tmp_path):
    return OwnershipRegistry(tmp_path / "whisper-owned.json")


@pytest.fixture
def make_server(stub_binary, model, registry):
    created = []

    def build():
        server = WhisperServer(
            server_path=stub_binary, model_path=model,
            startup_timeout=15, idle_unload_seconds=600,
            ownership=registry,
        )
        created.append(server)
        return server

    yield build

    for server in created:
        try:
            server.stop()
        except Exception:
            pass
    # Nothing may survive the test, whatever it asserted.
    for found in running_servers(model):
        try:
            os.kill(found.pid, 9)
        except OSError:
            pass


def wait_gone(pid, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_exists(pid):
            return True
        time.sleep(0.1)
    return False


def wait_for_port(port, timeout=15):
    """Block until something is listening.

    A freshly spawned stub has not bound its port yet, and a server that
    isn't answering /health reads as wedged rather than adoptable.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def test_a_started_server_is_discoverable_and_claimed(make_server, model,
                                                      registry):
    server = make_server()

    assert server.ensure_started()

    pid = server._process.pid
    assert [s.pid for s in running_servers(model)] == [pid]
    assert registry.owns(pid), "an unclaimed server could never be reclaimed"


def test_a_restart_adopts_the_running_server_instead_of_loading_a_second(
        make_server, model):
    """The whole fix in one test.

    The original bug: each launch spawned its own server on its own port, so
    restarts stacked up 2.9GB copies until the machine died. A second
    instance must now find the first and reuse it.
    """
    first = make_server()
    assert first.ensure_started()
    original_pid = first._process.pid

    second = make_server()
    assert second.ensure_started()

    assert second._adopted_pid == original_pid
    assert second._process is None, "adoption must not spawn anything"
    assert len(running_servers(model)) == 1, "a second copy was loaded"


def test_an_adopted_server_serves_on_the_port_it_was_found_on(make_server):
    first = make_server()
    assert first.ensure_started()

    second = make_server()
    assert second.ensure_started()

    assert second._port == first._port


def test_stopping_kills_a_server_we_own(make_server, model):
    server = make_server()
    assert server.ensure_started()
    pid = server._process.pid

    server.stop()

    assert wait_gone(pid), "the model memory was never released"
    assert running_servers(model) == []


def test_stopping_an_adopted_server_of_ours_also_kills_it(make_server):
    """A crash-restart adopts its predecessor, and Quit must still free it."""
    first = make_server()
    assert first.ensure_started()
    pid = first._process.pid

    second = make_server()
    assert second.ensure_started()
    second.stop()

    # wait() rather than wait_gone(): here the server is pytest's own child,
    # so after it dies it stays a zombie — visible to os.kill(pid, 0) —
    # until this process reaps it. In production an adopted server belongs
    # to an earlier run and is reaped by launchd, so this never arises.
    assert first._process.wait(timeout=10) is not None
    assert not process_exists(pid)


def test_a_server_we_do_not_own_is_used_but_never_killed(
        stub_binary, model, tmp_path):
    """A whisper-server started by hand must survive us."""
    stranger = subprocess.Popen(
        [str(stub_binary), "-m", str(model), "--port", "58231"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_for_port(58231), "stub never came up"
        empty = OwnershipRegistry(tmp_path / "empty.json")
        server = WhisperServer(
            server_path=stub_binary, model_path=model,
            startup_timeout=15, ownership=empty,
        )

        assert server.ensure_started()
        assert server._adopted_pid == stranger.pid

        server.stop()

        assert process_exists(stranger.pid), "killed a server that wasn't ours"
    finally:
        stranger.kill()
        stranger.wait(timeout=5)


def test_refuses_to_spawn_beside_a_wedged_server_that_is_not_ours(
        stub_binary, model, tmp_path):
    """Failing one transcription beats adding another copy of the weights.

    Transcriber's third attempt falls back to whisper-cli, so a refusal
    still produces a transcript.
    """
    # Bound but never answering /health: the wedged case.
    wedged = subprocess.Popen(
        ["/bin/sh", "-c",
         f"exec -a 'whisper-server -m {model} --port 58232' sleep 60"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        server = WhisperServer(
            server_path=stub_binary, model_path=model, startup_timeout=5,
            ownership=OwnershipRegistry(tmp_path / "empty.json"),
        )

        assert server.ensure_started() is False
        assert process_exists(wedged.pid), "killed a process that wasn't ours"
    finally:
        wedged.kill()
        wedged.wait(timeout=5)
