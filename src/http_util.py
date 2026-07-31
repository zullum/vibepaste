"""Small HTTP/socket helpers for talking to whisper-server.

Kept dependency-free (stdlib only) so VibePaste doesn't need `requests`.
"""

import socket
import uuid
from pathlib import Path

BOUNDARY_PREFIX = "----VibePasteBoundary"


def find_free_port():
    """Ask the OS for an unused localhost port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_is_open(port, timeout=0.5):
    """True once something accepts connections on the port.

    whisper-server loads its model before it starts listening, so an open
    port means the model is resident and ready to serve.
    """
    if port is None:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def build_multipart(file_path, fields, field_name="file",
                    content_type="audio/wav"):
    """Encode a file plus form fields as multipart/form-data.

    Returns:
        (body_bytes, content_type_header)
    """
    boundary = f"{BOUNDARY_PREFIX}{uuid.uuid4().hex}"
    parts = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    path = Path(file_path)
    parts.append(
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field_name}"; '
        f'filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode("utf-8")
    )
    parts.append(path.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"
