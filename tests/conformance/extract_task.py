"""Task adapter of the synthetic Web Extract conformance benchmark.

It reuses the portable record-task rules and differs only in the benchmark name
its prepared homes record, as a new record benchmark would.
"""

from __future__ import annotations

from pathlib import Path

from harness.web_extract.pipeline import read_json, save_json
from harness.web_extract.records import *  # noqa: F403  (the task adapter interface)
from harness.web_extract.records import prepare as prepare_records

NAME = "conformance-benchmark"


def prepare(home: Path, source: Path, *, distribution=None):
    result = prepare_records(home, source, distribution=distribution)
    manifest = Path(home) / "dataset/manifest.json"
    save_json(manifest, {**read_json(manifest), "benchmark": NAME})
    return result
