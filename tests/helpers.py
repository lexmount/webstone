"""Small helpers shared by test modules."""

import json
from functools import cache
from pathlib import Path


def write_json(path: Path, value: object) -> Path:
    """Write UTF-8 JSON, creating the parent directory, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


@cache
def shipped_suite_validations() -> dict:
    """Return ``benchmark.validate`` for every registered Web Fetch suite, computed once per process."""
    from lexbench_webfetch import benchmark

    return {row["id"]: benchmark.validate(row["id"]) for row in benchmark.registry()["suites"]}
