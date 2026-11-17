"""Result edition versions, independent of the Webstone software release."""

import copy
import re
from datetime import UTC, date, datetime

_PATTERN = re.compile(r"v([1-9]\d*)(?:\.(0|[1-9]\d*)\.(0|[1-9]\d*))?")
VERSION_PATTERN = r"v[1-9]\d*(?:\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))?"
KINDS = ("protocol", "results", "metadata")


def parts(version: str) -> tuple[int, int, int]:
    match = _PATTERN.fullmatch(version)
    if not match:
        raise ValueError("Invalid result version; expected vX.Y.Z or historical vN.")
    return tuple(int(value or 0) for value in match.groups())


def canonical(version: str) -> str:
    return "v" + ".".join(str(value) for value in parts(version))


def next_version(previous: str | None, kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("Choose release.kind: protocol, results or metadata.")
    if previous is None:
        if kind != "results":
            raise ValueError("An initial release must contain results.")
        return "v1.0.0"
    major, minor, patch = parts(previous)
    if kind == "protocol":
        return f"v{major + 1}.0.0"
    if kind == "results":
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def release_info(report: dict, previous: str | None) -> dict:
    release = report.get("release")
    if not isinstance(release, dict) or release.get("kind") not in KINDS:
        raise ValueError("Provide release.kind: protocol, results or metadata.")
    summary = release.get("summary")
    if (
        not isinstance(summary, str)
        or not re.search("[A-Za-z]", summary)
        or re.search(r"[\u3400-\u9fff]", summary)
    ):
        raise ValueError("Provide an English release.summary describing the change.")
    value = release.get("date", "")
    if value is None or value == "":
        value = datetime.now(UTC).date().isoformat()
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("release.date must be a UTC calendar date: YYYY-MM-DD.")
    parsed = date.fromisoformat(value)
    if parsed > datetime.now(UTC).date():
        raise ValueError("release.date cannot be in the future.")
    return {
        "kind": release["kind"], "date": value,
        "previous": canonical(previous) if previous else None, "summary": summary,
    }


def identity(report: dict) -> dict:
    """Ignore allocated identity/date when retrying the same result payload."""
    result = copy.deepcopy(report)
    result.pop("id", None)
    result.pop("version", None)
    if isinstance(result.get("release"), dict):
        result["release"].pop("previous", None)
        result["release"].pop("date", None)
    return result
