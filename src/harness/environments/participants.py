"""The participants a browser report profile declares, shared by both browser tracks.

A profile names its browsers either as ``engines`` (Browser Use: every engine runs
with every registered model) or as ``methods`` (Trajectory Replay: one participant
ID per measured configuration, each bound to an engine). Both forms are read here,
so runners and reports select the same run set from the same declaration.
"""

from __future__ import annotations

import re
from typing import Any

SAFE_ENGINE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


def profile_engines(profile: dict[str, Any]) -> tuple[str, ...]:
    """Return the browser engines a profile registers, in declaration order."""
    if profile.get("methods"):
        methods = participant_registry(profile["methods"]).values()
        engines = tuple(dict.fromkeys(str(method["engine"]) for method in methods))
    else:
        engines = tuple(str(engine) for engine in profile.get("engines") or [])
        if not engines or len(engines) != len(set(engines)):
            raise ValueError("current profile must register unique browser engines")
    if not all(SAFE_ENGINE.fullmatch(engine) for engine in engines):
        raise ValueError("profile engine IDs must be safe stable identifiers")
    return engines


def participant_registry(methods: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    registry = {}
    for method in methods:
        identity = method.get("id")
        if (not isinstance(identity, str)
                or not SAFE_ENGINE.fullmatch(identity)
                or identity in registry or not method.get("engine")):
            raise ValueError("participant registry requires unique safe method ids and engines")
        registry[identity] = method
    if not registry:
        raise ValueError("participant registry is required")
    return registry


def resolve_participant_id(
    row: dict[str, Any], registry: dict[str, dict[str, Any]]
) -> str:
    identity = row.get("participant_id")
    if identity is not None:
        if identity not in registry or row.get("engine") != registry[identity]["engine"]:
            raise ValueError("participant identity is unknown or disagrees with its engine")
        return identity
    matches = [key for key, method in registry.items() if method["engine"] == row.get("engine")]
    if len(matches) != 1:
        raise ValueError("legacy engine identity is ambiguous or absent from the registry")
    return matches[0]


def bind_participant(
    row: dict[str, Any], registry: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Return a normalized copy; never rewrite retained evidence."""
    return {**row, "participant_id": resolve_participant_id(row, registry)}
