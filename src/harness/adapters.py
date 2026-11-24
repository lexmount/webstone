"""Shared extension points: one registry type and the adapter contracts.

Every track keeps its own call signature, because a fetch, a search, a
structured extraction and a browser episode return different things. What all
of them share is defined here, so that shared code obtains adapters by name and
reads declared capabilities instead of branching on an instance name.

See docs/spec.md, "Extension points" and "One pipeline per track".
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")


class Registry(Generic[T]):
    """Name -> adapter mapping for one extension point.

    Shared code resolves adapters only through a registry, and command choices,
    validation sets and contract tests derive from ``names()``. A hardcoded list
    of adapter names elsewhere is a second registry and is not allowed.
    """

    def __init__(self, kind: str):
        self.kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T | None = None):
        """Register ``item`` under ``name``; usable as a decorator."""

        def add(value: T) -> T:
            if not isinstance(name, str) or not name:
                raise ValueError(f"{self.kind} name must be a non-empty string")
            if name in self._items and self._items[name] is not value:
                raise ValueError(f"{self.kind} already registered: {name}")
            self._items[name] = value
            return value

        return add if item is None else add(item)

    def get(self, name: str) -> T:
        try:
            return self._items[name]
        except KeyError:
            known = ", ".join(sorted(self._items)) or "none"
            raise ValueError(f"Unknown {self.kind}: {name} (registered: {known})") from None

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))

    def items(self) -> Iterator[tuple[str, T]]:
        return iter(sorted(self._items.items()))

    def __contains__(self, name: object) -> bool:
        return name in self._items

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True)
class Capabilities:
    """Declared behaviour that shared code may branch on.

    Shared code reads flags, never names. A track adds a flag here, with a
    default that preserves existing behaviour, when its pipeline needs one.
    """

    flags: frozenset[str] = frozenset()
    values: dict[str, Any] = field(default_factory=dict)

    def has(self, flag: str) -> bool:
        return flag in self.flags

    def value(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


@runtime_checkable
class MethodAdapter(Protocol):
    """What every measured method declares, whatever its track.

    A track's own adapter class adds the call itself (for example
    ``fetch(case, timeout)``) and the projection of native output into the
    record its scorer reads.
    """

    name: str

    def capabilities(self) -> Capabilities: ...

    def implementation_identity(self) -> dict[str, Any]:
        """Version, binary or endpoint identity and public settings."""
        ...

    def validate(self) -> list[str]:
        """Missing requirements; empty when the method can run."""
        ...


@runtime_checkable
class BenchmarkAdapter(Protocol):
    """The steps a benchmark supplies to its track's pipeline skeleton."""

    name: str

    def cases(self, profile: dict[str, Any]) -> Iterable[Any]:
        """Frozen cases selected by the profile."""
        ...

    def inputs(self, cases: list[Any]) -> AbstractContextManager[Any]:
        """Input environment for the calls: live site, served fixture or deployment."""
        ...

    def score(self, case: Any, scored_input: Any) -> Any:
        """Score one call; task rules come from case data."""
        ...

    def populations(self) -> list[dict[str, Any]]:
        """Denominators, families, metric sets and the expected matrix."""
        ...

    def views(self) -> list[Any]:
        """Report sections (``harness.reporting.templates.SectionHook``) for track hook points; often none."""
        ...


STAGES = ("run", "score", "report", "check", "package", "publish", "export")


@dataclass(frozen=True)
class TrackStages:
    """A track's implementation of the fixed stage verbs.

    Each value is a ``main(argv)`` callable or ``None`` when the track does not
    have that stage. Tracks may add preparation commands of their own under
    ``extra``; they may not add a second command for a fixed stage.
    """

    run: Callable[[list[str]], Any] | None = None
    score: Callable[[list[str]], Any] | None = None
    report: Callable[[list[str]], Any] | None = None
    check: Callable[[list[str]], Any] | None = None
    package: Callable[[list[str]], Any] | None = None
    publish: Callable[[list[str]], Any] | None = None
    export: Callable[[list[str]], Any] | None = None
    extra: dict[str, Callable[[list[str]], Any]] = field(default_factory=dict)

    def __post_init__(self):
        clash = set(self.extra) & set(STAGES)
        if clash:
            names = ", ".join(sorted(clash))
            raise ValueError("Extra commands cannot replace fixed stages: " + names)

    def supported(self) -> tuple[str, ...]:
        return tuple(stage for stage in STAGES if getattr(self, stage) is not None)

    def names(self) -> tuple[str, ...]:
        """Every command the track offers: its fixed stages first, then its extras."""
        return (*self.supported(), *self.extra)

    def command(self, name: str) -> Callable[[list[str]], Any]:
        handler = getattr(self, name) if name in STAGES else self.extra.get(name)
        if handler is None:
            raise ValueError(f"Unknown stage: {name} (offered: {', '.join(self.names())})")
        return handler


def owner_stage(module: str, function: str, summary: str, *,
                takes_argv: bool = False) -> Callable[[list[str]], Any]:
    """A stage implemented by ``module.function``, imported only when selected.

    Most owners read ``sys.argv``; several record their own source hash, so
    their ``main()`` stays unchanged. ``takes_argv`` owners receive the list.
    ``run_stage`` names the command in ``sys.argv[0]`` for the owner's usage line.
    """

    def stage(argv: list[str]) -> Any:
        handler = getattr(importlib.import_module(module), function)
        if takes_argv:
            return handler(list(argv))
        invoked = sys.argv
        sys.argv = [invoked[0], *argv]
        try:
            return handler()
        finally:
            sys.argv = invoked

    stage.__doc__ = summary
    return stage


def run_stage(stages: TrackStages, name: str, argv: Sequence[str], prog: str) -> None:
    """Run one stage under the public name ``<prog> <name>``."""
    handler = stages.command(name)
    invoked = sys.argv
    sys.argv = [f"{prog} {name}", *invoked[1:]]
    try:
        handler(list(argv))
    finally:
        sys.argv = invoked


def stage_main(stages: TrackStages, prog: str, description: str | None,
               argv: Sequence[str] | None = None) -> None:
    """Select a stage and pass it the remaining arguments, including its own ``--help``."""
    parser = argparse.ArgumentParser(prog=prog, description=description)
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for name in stages.names():
        sub.add_parser(name, help=stages.command(name).__doc__, add_help=False)
    args, remaining = parser.parse_known_args(argv)
    run_stage(stages, args.command, remaining, parser.prog)
