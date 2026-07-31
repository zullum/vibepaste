"""Tests for the escalating retry policy."""

import pytest

from src.transcriber import Transcriber, TranscriptionError


class FakeServer:
    """Stands in for a resident whisper-server."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self.stops = 0

    def transcribe(self, wav_path, language, timeout):
        self.calls.append((wav_path, language, timeout))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def stop(self):
        self.stops += 1


@pytest.fixture
def transcriber(tmp_path):
    return Transcriber(
        whisper_cli_path=tmp_path / "whisper-cli",
        server_path=tmp_path / "whisper-server",
        attempts=3,
        timeout_base=10,
        timeout_per_second=2.0,
    )


def _use_server(transcriber, server, model_path="model.bin"):
    transcriber._servers[str(model_path)] = server


def test_timeout_scales_with_duration(transcriber):
    assert transcriber.timeout_for(0) == 10
    assert transcriber.timeout_for(30) == 70


def test_first_attempt_uses_the_server(transcriber):
    server = FakeServer(["hello"])
    _use_server(transcriber, server)

    text = transcriber.transcribe("a.wav", "model.bin", "en", 5)

    assert text == "hello"
    assert len(server.calls) == 1
    assert server.stops == 0


def test_second_attempt_restarts_the_server(transcriber):
    server = FakeServer([None, "recovered"])
    _use_server(transcriber, server)

    text = transcriber.transcribe("a.wav", "model.bin", "en", 5)

    assert text == "recovered"
    assert server.stops == 1, "a wedged server must be restarted, not re-polled"


def test_third_attempt_falls_back_to_the_cli(transcriber, monkeypatch):
    server = FakeServer([None, None])
    _use_server(transcriber, server)
    monkeypatch.setattr(
        transcriber, "_transcribe_via_cli",
        lambda *args, **kwargs: "from cli",
    )

    assert transcriber.transcribe("a.wav", "model.bin", "en", 5) == "from cli"


def test_raises_with_every_reason_when_all_attempts_fail(transcriber, monkeypatch):
    server = FakeServer([None, RuntimeError("boom")])
    _use_server(transcriber, server)
    monkeypatch.setattr(
        transcriber, "_transcribe_via_cli",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(TranscriptionError) as excinfo:
        transcriber.transcribe("a.wav", "model.bin", "en", 5)

    assert len(excinfo.value.reasons) == 3
    assert "boom" in str(excinfo.value)


def test_an_exception_does_not_abort_the_remaining_attempts(transcriber, monkeypatch):
    server = FakeServer([RuntimeError("server died"), "second try"])
    _use_server(transcriber, server)

    assert transcriber.transcribe("a.wav", "model.bin", "en", 5) == "second try"


def test_empty_result_counts_as_failure(transcriber, monkeypatch):
    """An empty transcript used to be returned as success, pasting nothing."""
    server = FakeServer(["   ", "actual text"])
    _use_server(transcriber, server)

    assert transcriber.transcribe("a.wav", "model.bin", "en", 5) == "actual text"
