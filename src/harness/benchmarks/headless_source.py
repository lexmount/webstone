"""LexBench-Headless-Browser definitions read from a checkout of its upstream repository.

Card metadata names ``definitions`` as the track's ``upstream_source`` hook. Every
original input keeps its bytes and is listed with its hash in the generated registry.
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

from harness.benchmarks.publication import _git_blobs, encoded
from harness.record_io import bytes_sha256

REPOSITORY = "https://github.com/lexmount/Lexbench-Headless-Browser"
INPUTS = ("tasks", "fixtures", "config", "manifest.json", "harness_pins.json",
          "LICENSE", "THIRD_PARTY_NOTICES.md")


def definitions(checkout: Path, revision: str) -> dict[str, bytes]:
    originals = _git_blobs(checkout, revision, INPUTS)
    files, source_files = {}, {}
    for name, content in originals.items():
        if set(Path(name).parts) & {"runs", "reports", "generated", "private_artifacts"}:
            raise ValueError("Unexpected headless input: " + name)
        files["definitions/" + name] = content
        source_files[name] = {"sha256": bytes_sha256(content), "size": len(content)}
        if name in {"LICENSE", "THIRD_PARTY_NOTICES.md"}:
            files[name] = content
    required = ("manifest.json", "harness_pins.json", "LICENSE", "THIRD_PARTY_NOTICES.md",
                "config/browser_driver_bindings.json")
    if any("definitions/" + name not in files for name in required):
        raise ValueError("Incomplete upstream headless data contract")
    manifest = json.loads(files["definitions/manifest.json"])
    bindings = json.loads(files["definitions/config/browser_driver_bindings.json"])
    datasets, used = {}, set()
    for layer in manifest["layers"]:
        for subset in layer["subsets"]:
            identity, pattern = subset["subset_id"], subset["task_glob"]
            if (not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", identity)
                    or identity in datasets or identity == "scenario-specs"
                    or not pattern.startswith("tasks/") or ".." in Path(pattern).parts
                    or "**" in pattern):
                raise ValueError("Unsafe or duplicate headless subset")
            names = sorted(name for name in files if name.startswith("definitions/tasks/")
                           and len(Path(name).parts[1:]) == len(Path(pattern).parts)
                           and all(fnmatchcase(value, glob) for value, glob in
                                   zip(Path(name).parts[1:], Path(pattern).parts, strict=True)))
            if not names or used.intersection(names):
                raise ValueError("Missing or overlapping headless task population: " + identity)
            used.update(names)
            cases = [json.loads(files[name]) for name in names]
            path = "viewer/" + identity + ".json"
            payload = encoded({"cases": cases})
            files["definitions/" + path] = payload
            datasets[identity] = {"path": path, "sha256": bytes_sha256(payload), "count": len(cases),
                                  "id_field": "task_id", "driver": subset["driver"],
                                  "layer": layer["layer_id"], "protocol": subset,
                                  "source_paths": {case["task_id"]: name.removeprefix("definitions/")
                                                   for case, name in zip(cases, names, strict=True)}}
    scenarios = sorted(name for name in files if name.startswith("definitions/tasks/scenarios/")
                       and name.endswith(".json"))
    cases = [json.loads(files[name]) for name in scenarios]
    if not cases:
        raise ValueError("Missing headless scenario specifications")
    path, payload = "viewer/scenario-specs.json", encoded({"cases": cases})
    files["definitions/" + path] = payload
    datasets["scenario-specs"] = {"path": path, "sha256": bytes_sha256(payload), "count": len(cases),
                                 "id_field": "scenario_id", "population": "scenario-specifications",
                                 "source_paths": {case["scenario_id"]: name.removeprefix("definitions/")
                                                  for case, name in zip(cases, scenarios, strict=True)}}
    task_files = {name for name in files if name.startswith("definitions/tasks/") and name.endswith(".json")}
    if task_files != used | set(scenarios):
        raise ValueError("Unregistered headless task definitions")
    source = {"repository": REPOSITORY, "revision": revision,
              "bench_id": manifest["bench_id"], "bench_version": manifest["bench_version"],
              "task_count": len(used), "scenario_count": len(scenarios), "subset_count": len(datasets) - 1,
              "fixture_count": sum(name.startswith("definitions/fixtures/") for name in files),
              "config_count": sum(name.startswith("definitions/config/") for name in files),
              "driver_count": len(bindings["driver_ids"]), "driver_ids": bindings["driver_ids"],
              "file_count": len(originals)}
    files["definitions/registry.json"] = encoded({"datasets": datasets, "source": source, "source_files": source_files})
    return files
