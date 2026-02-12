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
