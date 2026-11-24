"""The export stage: turn a track's scored run into a webstone-site result submission.

Each track registers ``export(run_dir, receipt=None) -> dict`` as its
``TrackStages.export``. The exporter reads only scored results, so every value in
the submission comes from the run, and the publication receipt supplies the
public evidence URL. A run exported without a receipt is marked unpublished: it
has no public evidence, so ``ResultsClient.check`` and ``submit`` refuse it until
the report is published and the run exported again with its receipt.
"""

from __future__ import annotations

import argparse
import importlib
import json
import math
import re
import statistics
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.record_schema import RecordSchema

PUBLISHED = "report-page"
UNPUBLISHED = "unpublished-run"

# The scored-result projection a track's exporter reads when its own records do not
# carry the common roles directly: one logical result per method and case.
SCORED_ROLES = {"method": "method", "case": "case", "elapsed": "elapsed_ms",
                "evidence": "evidence", "verdict": "passed"}


def scored_result_schema(name: str, version: int = 1, *, nullable: Iterable[str] = (),
                         **fields: tuple[type, ...]) -> RecordSchema:
    """A versioned scored-result schema bound to the common roles."""
    return RecordSchema(name, version, SCORED_ROLES, fields, nullable=frozenset(nullable))


def distribution(values: Iterable[float]) -> dict[str, float | int | None]:
    """Mean, median and nearest-rank P95 of one declared population."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"n": 0, "mean": None, "p50": None, "p95": None}
    return {"n": len(ordered), "mean": statistics.fmean(ordered),
            "p50": statistics.median(ordered),
            "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]}


def rounded(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def percent(part: int | float | None, total: int | float | None) -> float | None:
    return None if part is None or not total else round(100 * part / total, 2)


def method_id(name: str) -> str:
    """A submission method ID: a valid run method name as is, else its lowercase hyphen form."""
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(name)):
        return str(name)
    slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")
    if not slug:
        raise ValueError(f"Method name has no submission ID form: {name!r}")
    return slug


def read_receipt(path: Path | str) -> dict[str, Any]:
    """The public URL and version a publication receipt records.

    Publish stages write ``share_url`` (or ``url``), or a site binding with
    ``host``/``base_url`` and ``slug``, and a ``versionId``/``version``.
    """
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError(f"Select a regular publication receipt file: {source}")
    receipt = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError("A publication receipt must be a JSON object")
    url = receipt.get("share_url") or receipt.get("url")
    base = receipt.get("base_url") or receipt.get("host")
    if not url and base and receipt.get("slug"):
        url = str(base).rstrip("/") + "/s/" + str(receipt["slug"])
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("The publication receipt does not record a public HTTPS report URL")
    version = next((receipt[key] for key in ("versionId", "version", "expected_version")
                    if receipt.get(key) is not None), None)
    return {"url": url, "version": version}


def provider(method: str, scores: dict[str, Any], *, mode: str, description: str,
             passed: int | None = None, **extra: Any) -> dict[str, Any]:
    """One measured method; ``scores`` holds every metric of the definition (null when absent)."""
    return {"id": method_id(method), "name": method, "description": description, "mode": mode,
            "scores": scores, "passed": passed, **extra}


def submission(*, benchmark: str, definition: str, task_count: int, providers: list[dict],
               receipt: dict[str, Any] | None, track: str, scope: str,
               notes: Sequence[str] = (), **result: Any) -> dict[str, Any]:
    """The submission ``ResultsClient.check`` validates, from measured values only."""
    ids: dict[str, list[str]] = {}
    for item in providers:
        ids.setdefault(item["id"], []).append(item["name"])
    collisions = [names for names in ids.values() if len(names) > 1]
    if collisions:
        raise ValueError("Methods share one submission ID; rename them in the run: "
                         + "; ".join(", ".join(names) for names in collisions))
    url = receipt["url"] if receipt else None
    published = receipt is not None
    notes = [*notes] if published else [
        *notes, "Unpublished run: publish the report and export again with its receipt."]
    return {
        "release": {"kind": "results", "date": "", "summary": (
            f"{len(providers)} method{'s' * (len(providers) != 1)} on {task_count} "
            f"{benchmark} cases, exported from a scored {track} run.")},
        "datasetDifference": scope,
        "source": {
            "kind": PUBLISHED if published else UNPUBLISHED,
            "url": url,
            "title": f"{track} report" if published else f"Unpublished {track} run",
            **({"version": receipt["version"]} if published and receipt.get("version") else {}),
            "method": f"webstone {track} export from scored results",
            "scope": scope,
            "importedAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "dateNote": "Export time, not evaluation time.",
        },
        "benchmarks": [{"id": benchmark, "definitionId": definition, "taskCount": task_count,
                        **result, "notes": list(notes), "reportUrl": url,
                        "providers": providers}],
        "evidence": [],
    }


def export_stage(module: str, function: str = "export", *,
                 selects_benchmark: bool = False) -> Callable[[list[str]], None]:
    """``<track> export RUN_DIR [--receipt R] --output FILE`` for ``module.function``.

    A track whose runs can hold several benchmarks sets ``selects_benchmark``; the
    command then takes ``--benchmark`` and passes it to the exporter.
    """

    def stage(argv: list[str]) -> None:
        import sys

        parser = argparse.ArgumentParser(
            prog=sys.argv[0], description="Export scored results as a webstone-site submission")
        parser.add_argument("run_dir", type=Path, help="Scored run directory")
        parser.add_argument("--receipt", type=Path,
                            help="Publication receipt of the published report; "
                                 "without one the submission is marked unpublished")
        if selects_benchmark:
            parser.add_argument("--benchmark",
                                help="Submission benchmark to export when the run holds several")
        parser.add_argument("--output", type=Path, required=True,
                            help="New submission JSON for `webstone results submit`")
        args = parser.parse_args(argv)
        exporter = getattr(importlib.import_module(module), function)
        try:
            receipt = read_receipt(args.receipt) if args.receipt else None
            options = {"benchmark": args.benchmark} if selects_benchmark else {}
            result = exporter(args.run_dir, receipt, **options)
            with args.output.open("x", encoding="utf-8") as stream:
                stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        except (OSError, ValueError, KeyError) as error:
            parser.exit(2, f"error: {error}\n")
        print(json.dumps({"file": str(args.output), "benchmark": result["benchmarks"][0]["id"],
                          "published": result["source"]["kind"] == PUBLISHED}, indent=2))

    stage.__doc__ = "Export scored results as a webstone-site submission"
    return stage
