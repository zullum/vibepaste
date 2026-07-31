"""Tests for recognising whisper-servers in the process table."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.server_discovery import (
    parse_servers, running_servers, server_is_healthy,
)

BIN = "/Users/x/whisper.cpp/build/bin/whisper-server"
MODEL = "/Users/x/whisper.cpp/models/ggml-large-v3.bin"


def ps_line(pid, model=MODEL, port=55036):
    return (f"  {pid} {BIN} -m {model} --port {port} "
            f"--host 127.0.0.1 -t 10 -nt")


def test_reads_the_pid_model_and_port_from_a_command_line():
    servers = parse_servers(ps_line(4242))

    assert len(servers) == 1
    assert servers[0].pid == 4242
    assert servers[0].model_path == MODEL
    assert servers[0].port == 55036


def test_ignores_processes_that_are_not_whisper_servers():
    output = "  501 /usr/bin/python3 -m src.menubar\n  502 /bin/zsh"

    assert parse_servers(output) == []


def test_ignores_a_grep_that_merely_mentions_whisper_server():
    """A pgrep for the binary matches its own command line — the reason the
    bundle's PID guard stopped matching on command lines at all."""
    output = f"  777 grep whisper-server\n{ps_line(4242)}"

    servers = parse_servers(output)

    assert [s.pid for s in servers] == [4242]


def test_ignores_a_server_we_could_not_address():
    """No port means we can neither adopt it nor reason about it."""
    output = f"  888 {BIN} -m {MODEL}"

    assert parse_servers(output) == []


def test_ignores_a_line_whose_pid_is_not_a_number():
    assert parse_servers(f"  abc {BIN} -m {MODEL} --port 1") == []


def test_finds_every_running_server():
    output = "\n".join([ps_line(1, port=100), ps_line(2, port=200)])

    assert [s.pid for s in parse_servers(output)] == [1, 2]


def test_filters_to_the_model_that_was_asked_for():
    turbo = "/Users/x/whisper.cpp/models/ggml-large-v3-turbo.bin"
    output = "\n".join([ps_line(1), ps_line(2, model=turbo)])

    found = running_servers(MODEL, runner=lambda: output)

    assert [s.pid for s in found] == [1]


def test_an_unreadable_process_table_costs_adoption_but_never_transcription():
    def broken():
        raise OSError("ps is unavailable")

    assert running_servers(MODEL, runner=broken) == []


# -- /health -------------------------------------------------------------


class _Health(BaseHTTPRequestHandler):
    status = "ok"

    def do_GET(self):
        body = json.dumps({"status": self.status}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def health_server():
    def serve(status):
        _Health.status = status
        httpd = HTTPServer(("127.0.0.1", 0), _Health)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd

    servers = []
    yield lambda status: servers.append(serve(status)) or servers[-1]
    for httpd in servers:
        httpd.shutdown()


def test_a_loaded_server_is_healthy(health_server):
    httpd = health_server("ok")

    assert server_is_healthy(httpd.server_address[1]) is True


def test_a_server_still_reading_its_weights_is_not_yet_adoptable(health_server):
    """/health answers 'loading model' for the ~9s large-v3 takes to load."""
    httpd = health_server("loading model")

    assert server_is_healthy(httpd.server_address[1]) is False


def test_a_port_with_nothing_on_it_is_not_healthy():
    assert server_is_healthy(1) is False
