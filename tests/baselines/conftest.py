"""Pin the builder identity that reports record; it changes with every commit and release."""

import pytest

from harness.reporting import versions
from harness.web_fetch import runner

BUILDER = {"version": "0.0.0", "revision": None, "source_sha256": "0" * 64,
           "modified": False}


@pytest.fixture(autouse=True)
def pinned_builder_identity(monkeypatch):
    monkeypatch.setattr(versions, "software_identity", lambda: dict(BUILDER))
    monkeypatch.setattr(runner, "software_identity", lambda: dict(BUILDER))
