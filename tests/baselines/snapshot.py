"""Characterization baselines: readable snapshots of each track's stage outputs.

Each track test drives its runnable synthetic example through the stages it can
run offline (run, score, report, check) and compares a normalized snapshot with
``tests/baselines/<track>.json``. The snapshot keeps scored record fields, the
report's routes with a SHA-256 prefix of each normalized file and, per page, the
ordered h1-h3 headings and table cells. Absolute paths, timestamps and SHA-256
digests are replaced with tokens, so the snapshot depends only on behaviour. The
route digests catch any byte change; the outlines show *what* changed in review.
Further report scenarios of a track keep only their route digests.

A refactor that changes a baseline must say why in its description. Refresh
every baseline after an intended change with::

    WEBSTONE_UPDATE_BASELINES=1 uv run --frozen --all-extras python -m pytest tests/baselines
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from collections.abc import Callable, Iterable
from html.parser import HTMLParser
from pathlib import Path

import pytest

from harness.reporting.mermaid import unpinned_mermaid_urls

BASELINE_DIR = Path(__file__).parent
CHECKOUT = BASELINE_DIR.parents[1]
UPDATE_VARIABLE = "WEBSTONE_UPDATE_BASELINES"
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
_COMPACT_STAMP = re.compile(r"\b\d{8}T\d{6}Z\b")
_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
_HEADINGS = {"h1", "h2", "h3"}
# Longer tables keep their first rows and the count of the rest, so a changed
# row count still shows while frozen dataset listings stay readable.
MAX_TABLE_ROWS = 20
_IGNORED = {"script", "style", "template"}


class Normalizer:
    """Replace machine-, clock- and digest-specific text with stable tokens."""

    def __init__(self, roots: Iterable[Path]):
        tokens = {str(path): "<root>" for root in roots for path in (root, root.resolve())}
        # Packaged inputs, such as the fixture dataset, are recorded by checkout path.
        tokens.update(dict.fromkeys(
            {str(CHECKOUT), str(CHECKOUT.resolve())} - tokens.keys(), "<checkout>"))
        # Longest first, so a nested root is not split by its parent's replacement.
        self.roots = sorted(tokens.items(), key=lambda item: len(item[0]), reverse=True)

    def text(self, value: str) -> str:
        for root, token in self.roots:
            value = value.replace(root, token)
        value = _TIMESTAMP.sub("<timestamp>", value)
        value = _COMPACT_STAMP.sub("<timestamp>", value)
        return _SHA256.sub("<sha256>", value)

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {self.text(str(key)): self.value(item) for key, item in value.items()}
        if isinstance(value, list | tuple):
            return [self.value(item) for item in value]
        return value


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def pick(row: dict, keys: Iterable[str]) -> dict:
    """Return the named fields of a record, keeping absent ones visible as ``None``."""
    return {key: row.get(key) for key in keys}


def omit(value, keys: set[str]):
    """Drop the named keys at every depth, such as evidence bindings the routes already list."""
    if isinstance(value, dict):
        return {key: omit(item, keys) for key, item in value.items() if key not in keys}
    if isinstance(value, list):
        return [omit(item, keys) for item in value]
    return value


def file_inventory(root: Path, suffixes: tuple[str, ...] = ()) -> list[str]:
    """List files under ``root`` relative to it, optionally only the given suffixes."""
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and (not suffixes or path.suffix in suffixes)
    )


class _PageOutline(HTMLParser):
    """Collect the ordered h1-h3 headings and the cells of every table on a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.headings: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._ignored = 0
        self._heading: list[str] | None = None
        self._open_tables: list[list[list[str]]] = []
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag in _IGNORED:
            self._ignored += 1
        elif tag in _HEADINGS:
            self._heading = []
        elif tag == "table":
            self._open_tables.append([])
        elif tag == "tr" and self._open_tables:
            self._open_tables[-1].append([])
        elif tag in {"td", "th"} and self._open_tables:
            if not self._open_tables[-1]:
                self._open_tables[-1].append([])
            self._cell = []

    def handle_endtag(self, tag):
        if tag in _IGNORED:
            self._ignored = max(0, self._ignored - 1)
        elif tag in _HEADINGS and self._heading is not None:
            self.headings.append(f"{tag}: {_collapse(self._heading)}")
            self._heading = None
        elif tag in {"td", "th"} and self._cell is not None and self._open_tables:
            self._open_tables[-1][-1].append(_collapse(self._cell))
            self._cell = None
        elif tag == "table" and self._open_tables:
            rows = [row for row in self._open_tables.pop() if row]
            if rows:
                self.tables.append(rows)

    def handle_data(self, data):
        if self._ignored:
            return
        if self._heading is not None:
            self._heading.append(data)
        if self._cell is not None:
            self._cell.append(data)


def _collapse(parts: list[str]) -> str:
    return " ".join("".join(parts).split())


def page_outline(path: Path, normalizer: Normalizer) -> dict:
    outline = _PageOutline()
    outline.feed(path.read_text(encoding="utf-8"))
    page = {"headings": [normalizer.text(text) for text in outline.headings]}
    tables = []
    for table in outline.tables:
        rows = [" | ".join(normalizer.text(cell) for cell in row) for row in table]
        if len(rows) > MAX_TABLE_ROWS:
            rows = rows[:MAX_TABLE_ROWS] + [f"... {len(rows) - MAX_TABLE_ROWS} more rows"]
        tables.append(rows)
    if tables:
        page["tables"] = tables
    return page


def route_digests(report: Path, normalizer: Normalizer) -> list[str]:
    """List every file under ``report`` as ``<route> <SHA-256 prefix of its normalized bytes>``.

    Text files are normalized like the rest of the snapshot; other files are hashed
    as written. Routes are normalized too, so the list is sorted after that and
    keeps routes that differ only by a digest. A 20-digit prefix is ample for change
    detection and, unlike a full digest, survives ``Normalizer.value``. A rendered
    page must not load an unpinned Mermaid runtime.
    """
    lines = []
    for route in file_inventory(report):
        content = (report / route).read_bytes()
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            if route.endswith((".html", ".js")) and (unpinned := unpinned_mermaid_urls(text)):
                pytest.fail(f"{route} loads an unpinned Mermaid runtime: {unpinned}")
            content = normalizer.text(text).encode("utf-8")
        lines.append(f"{normalizer.text(route)} {hashlib.sha256(content).hexdigest()[:20]}")
    if not lines:
        pytest.fail(f"{report} holds no routes")
    return sorted(lines)


def report_snapshot(
    report: Path, normalizer: Normalizer, outline: Callable[[str], bool] = lambda route: True
) -> dict:
    """Return the route digests and the outline of each HTML page selected by ``outline``.

    Every route keeps its digest; ``outline`` only skips repetitive pages, such as
    one page per frozen dataset task, whose structure a representative page shows.
    """
    pages = [route for route in file_inventory(report, (".html",)) if outline(route)]
    return {
        "routes": route_digests(report, normalizer),
        "pages": {route: page_outline(report / route, normalizer) for route in pages},
    }


def check_baseline(name: str, snapshot: dict) -> None:
    """Compare ``snapshot`` with ``tests/baselines/<name>.json``, or rewrite it on request."""
    actual = json.dumps(snapshot, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    path = BASELINE_DIR / f"{name}.json"
    if os.environ.get(UPDATE_VARIABLE) == "1":
        path.write_text(actual, encoding="utf-8")
        return
    if not path.is_file():
        pytest.fail(f"{name}: no baseline; run with {UPDATE_VARIABLE}=1 to create {path.name}")
    expected = path.read_text(encoding="utf-8")
    if actual == expected:
        return
    diff = list(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        f"tests/baselines/{name}.json", "current output", lineterm="", n=2,
    ))
    shown = "\n".join(diff[:120]) + ("\n..." if len(diff) > 120 else "")
    pytest.fail(
        f"{name}: stage output differs from its baseline.\n{shown}\n"
        f"If the change is intended, rerun with {UPDATE_VARIABLE}=1, commit the baseline "
        "and explain the change in the PR description."
    )
