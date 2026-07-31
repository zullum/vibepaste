"""HTTP client for a running whisper-server's /inference endpoint.

Kept apart from the server's lifecycle: talking to a server and deciding
which server should exist are different jobs, and only the latter is
entangled with process management.
"""

import json
import logging
import urllib.error
import urllib.request

from src.http_util import build_multipart

logger = logging.getLogger(__name__)


def transcribe_via_http(port, wav_path, language, timeout):
    """POST the WAV to /inference.

    Returns:
        Transcribed text, or None if the request failed or came back empty.
    """
    body, content_type = build_multipart(
        wav_path,
        {
            "response_format": "json",
            "language": language,
            "temperature": "0.0",
        },
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/inference",
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        logger.error(f"whisper-server request failed: {e}")
        return None

    return extract_text(payload)


def extract_text(payload):
    """Pull the transcript out of an /inference JSON response."""
    try:
        data = json.loads(payload)
    except ValueError:
        logger.error(f"whisper-server returned non-JSON: {payload[:200]}")
        return None
    return (data.get("text") or "").strip() or None
