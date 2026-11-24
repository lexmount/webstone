"""Derive a saved-matrix build input from run directories written by `webstone run`."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from harness.web_fetch.report_saved_matrix import _load_saved_matrix

RUN_FILES = ("manifest.json", "dataset.json", "results.jsonl")



def unique_suite_id(base: str, digest: str, used: set[str]) -> str:
    """Return ``base``, or ``base`` with a longer dataset digest prefix until unused."""
    candidate, width = base, 8
    while candidate in used:
        if width > len(digest):
            raise ValueError("Cannot derive a unique suite id for " + base)
        candidate, width = base + "-" + digest[:width], width + 8
    return candidate

def _binding(path: Path, base: Path) -> dict[str, str]:
    return {"path": os.path.relpath(path, base), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _read_run(run_dir: Path) -> dict:
    for name in RUN_FILES:
        if not (run_dir / name).is_file():
            raise ValueError(f"{run_dir}: missing {name}; expected a directory written by `webstone run`")
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    dataset = json.loads((run_dir / "dataset.json").read_text(encoding="utf-8"))
    records = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    if not manifest.get("dataset_sha256") or not records:
        raise ValueError(f"{run_dir}: run has no frozen dataset hash or no results")
    cells = [(record["provider"], record["case_id"]) for record in records]
    if len(cells) != len(set(cells)):
        raise ValueError(f"{run_dir}: several attempts for one method and case cannot form one comparison")
    providers = list(dict.fromkeys(provider for provider, _ in cells))
    case_ids = [case["id"] for case in dataset["cases"]]
    if any({cid for provider, cid in cells if provider == name} != set(case_ids) for name in providers):
        raise ValueError(f"{run_dir}: a method lacks results for some selected cases")
    meta = manifest.get("dataset") or {}
    return {"dir": run_dir, "dataset_sha256": manifest["dataset_sha256"], "providers": providers,
            "case_ids": case_ids, "suite_id": str(meta.get("id") or "dataset"),
            "suite_label": str(meta.get("name") or meta.get("id") or "dataset")}


def compose_saved_matrix(run_dirs, output, *, title=None, by_suite=False) -> Path:
    """Write the minimal saved-matrix specification for complete, separately produced runs."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"{output} exists; write the specification to a new path")
    runs = [_read_run(Path(path).resolve()) for path in run_dirs]
    if len({run["dir"] for run in runs}) != len(runs):
        raise ValueError("A run directory is listed more than once")
    groups: dict[str, list[dict]] = {}
    for run in runs:
        groups.setdefault(run["dataset_sha256"], []).append(run)
    if len(groups) > 1 and not by_suite:
        raise ValueError("Runs use different frozen datasets (" + ", ".join(sorted(groups))
                         + "); pass --by-suite to compare each dataset as its own suite")
    base = output.parent
    suites, sources, providers = [], [], None
    used_ids = set()
    for digest, members in groups.items():
        first = members[0]
        suite_id = unique_suite_id(first["suite_id"], digest, used_ids)
        used_ids.add(suite_id)
        if any(set(run["case_ids"]) != set(first["case_ids"]) for run in members):
            raise ValueError(f"Runs of suite {suite_id} selected different cases; run each method on the same cases")
        suite_providers = [name for run in members for name in run["providers"]]
        if len(suite_providers) != len(set(suite_providers)):
            raise ValueError(f"A method appears in several runs of suite {suite_id}")
        if providers is None:
            providers = suite_providers
        elif set(providers) != set(suite_providers):
            raise ValueError("Every suite needs results from the same methods")
        suites.append({"id": suite_id, "label": first["suite_label"],
                       "dataset": _binding(first["dir"] / "dataset.json", base)})
        sources.extend({"suite": suite_id, "root": os.path.relpath(run["dir"], base),
                        "results": _binding(run["dir"] / "results.jsonl", base),
                        "case_ids": run["case_ids"]} for run in members)
    spec = {"schema_version": 1,
            "title": title or " / ".join(suite["label"] for suite in suites),
            "providers": [{"id": name, "label": name} for name in providers],
            "suites": suites, "sources": sources}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        _load_saved_matrix(output)
    except Exception:
        output.unlink()
        raise
    return output
