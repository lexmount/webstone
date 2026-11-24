"""WebMainBench frozen inputs: the pinned corpus, sample manifests and case identity."""
from __future__ import annotations

import json
from pathlib import Path

from harness.record_io import bytes_sha256, parse_jsonl

DATASET_SHA256 = "0efaa4b49a45e320a27fe6e5a0b6aad5b57259fc3321ac3448519cacc74c537e"
SCORER_COMMIT = "9d991bdc00c57b57521499494d96be85c31317ba"
CASE_COUNT = 545


def selected_input(dataset: Path | None = None, expected_sha256: str | None = None,
                   scorer_commit: str | None = None) -> tuple[Path, str, str]:
    """Use the pinned corrected edition by default; explicit files retain historical controls."""
    if dataset is not None:
        return dataset, expected_sha256 or DATASET_SHA256, scorer_commit or SCORER_COMMIT
    from harness.benchmarks.data import binding, definition_path
    selected = binding("webmainbench")
    task = selected["datasets"][selected["default_dataset"]]
    return (definition_path("webmainbench", task["path"]),
            expected_sha256 or task["sha256"], scorer_commit or selected["scorer"]["revision"])



def load_source(
    path: Path,
    selected_ids: set[str] | None = None,
    expected_sha256: str = DATASET_SHA256,
    *, expected_count: int = CASE_COUNT,
) -> list[dict]:
    payload = path.read_bytes()
    actual = bytes_sha256(payload)
    if actual != expected_sha256:
        raise ValueError(f"Unexpected WebMainBench-545 SHA-256: {actual}")
    records = parse_jsonl(payload, skip_blank=True)
    if type(expected_count) is not int or expected_count <= 0 or len(records) != expected_count:
        raise ValueError(f"Expected {expected_count} records, found {len(records)}")
    if len({record["track_id"] for record in records}) != expected_count:
        raise ValueError("WebMainBench track_id values are not unique")
    if selected_ids is not None:
        records = [record for record in records if record["track_id"] in selected_ids]
        missing = selected_ids - {record["track_id"] for record in records}
        if missing:
            raise ValueError("Unknown track IDs: " + ", ".join(sorted(missing)))
    return records


def load_selected_ids(path: Path, expected_dataset_sha256: str = DATASET_SHA256) -> tuple[set[str], dict]:
    """Load either the frozen JSON sample manifest or a plain one-ID-per-line file."""
    payload = path.read_bytes()
    binding = {"path": str(path), "sha256": bytes_sha256(payload)}
    if path.suffix.lower() != ".json":
        selected = {line.strip() for line in payload.decode("utf-8").splitlines() if line.strip()}
        return selected, {**binding, "format": "track_id_lines", "case_count": len(selected)}
    manifest = json.loads(payload)
    parent = manifest.get("parent", {})
    if parent.get("sha256") != expected_dataset_sha256:
        raise ValueError("WebMainBench sample manifest parent does not match the selected corpus; "
                         "select matching --dataset and --case-ids-file inputs")
    cases = manifest.get("cases", [])
    selected = {row.get("track_id") for row in cases}
    if (manifest.get("schema_version") != 1
            or len(cases) != manifest.get("sampling", {}).get("size")
            or None in selected or len(selected) != len(cases)):
        raise ValueError("Invalid WebMainBench sample manifest")
    return selected, {
        **binding,
        "format": "webmainbench_sample_manifest_v1",
        "dataset_id": manifest.get("dataset_id"),
        "selection_seed": manifest.get("sampling", {}).get("seed"),
        "parent_sha256": parent["sha256"],
        "case_count": len(selected),
    }

