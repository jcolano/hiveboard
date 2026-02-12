<<<<<<< HEAD
"""Test harness — fresh JsonStorageBackend per test with temp directory."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from backend.storage_json import JsonStorageBackend


@pytest.fixture
async def storage(tmp_path: Path) -> JsonStorageBackend:
    """Create a fresh storage backend with a temp data directory."""
    backend = JsonStorageBackend(data_dir=tmp_path)
    await backend.initialize()
    yield backend
    await backend.close()


@pytest.fixture
def sample_batch() -> dict:
    """Load the shared sample batch fixture."""
    import json
    fixture_path = Path(__file__).parent.parent / "shared" / "fixtures" / "sample_batch.json"
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)
=======
"""Shared test fixtures for HiveLoop SDK tests.

Provides a mock HTTP server that captures POST /v1/ingest requests
and can be configured to return error responses for retry testing.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any

import pytest

# Register sdk.hiveloop as 'hiveloop' so `import hiveloop` works in tests
import sdk.hiveloop as _hiveloop_mod
sys.modules["hiveloop"] = _hiveloop_mod
# Also register sub-modules for direct imports
sys.modules["hiveloop._transport"] = _hiveloop_mod._transport  # type: ignore[attr-defined]
sys.modules["hiveloop._agent"] = _hiveloop_mod._agent  # type: ignore[attr-defined]


class _IngestHandler(BaseHTTPRequestHandler):
    """Mock ingest endpoint that captures batches."""

    def do_POST(self) -> None:
        if self.path != "/v1/ingest":
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)
        data = json.loads(body)

        # Record the batch
        self.server.batches.append(data)  # type: ignore[attr-defined]

        # Check for configured error responses
        error_queue = self.server.error_queue  # type: ignore[attr-defined]
        if error_queue:
            status_code, response_body = error_queue.pop(0)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response_body).encode())
            return

        # Default: success
        accepted = len(data.get("events", []))
        response = {"accepted": accepted, "rejected": 0, "warnings": [], "errors": []}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode())

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress request logging during tests."""
        pass


class MockIngestServer:
    """A mock HTTP server for testing the SDK transport."""

    def __init__(self) -> None:
        self.server = HTTPServer(("127.0.0.1", 0), _IngestHandler)
        self.server.batches = []  # type: ignore[attr-defined]
        self.server.error_queue = []  # type: ignore[attr-defined]
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self._thread.join(timeout=5)

    @property
    def batches(self) -> list[dict[str, Any]]:
        return self.server.batches  # type: ignore[attr-defined]

    def enqueue_error(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        """Queue an error response for the next request."""
        if body is None:
            body = {"error": "test_error", "message": "Test error", "status": status_code}
        self.server.error_queue.append((status_code, body))  # type: ignore[attr-defined]

    def clear(self) -> None:
        """Clear captured batches and error queue."""
        self.server.batches.clear()  # type: ignore[attr-defined]
        self.server.error_queue.clear()  # type: ignore[attr-defined]

    def all_events(self) -> list[dict[str, Any]]:
        """Flatten all captured events across all batches."""
        events = []
        for batch in self.batches:
            events.extend(batch.get("events", []))
        return events


@pytest.fixture
def mock_server():
    """Fixture providing a mock ingest server."""
    server = MockIngestServer()
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def reset_hiveloop():
    """Reset the hiveloop singleton between tests."""
    import hiveloop  # noqa: F811
    yield
    hiveloop.reset()
>>>>>>> 7c7da8f16b65b2a17b2d0fed9c920a731e963094
