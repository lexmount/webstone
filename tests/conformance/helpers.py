"""Drive a track's public stages with synthetic instances registered only through adapters.

Each conformance test registers a synthetic method and a synthetic benchmark in the
track's registries, then runs ``webstone <track> <stage>`` in this process for every
stage the track declares. A stage that would need a shared-code change for the new
instance fails the test (docs/spec.md, "Enforcement").
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from harness import cli
from harness.adapters import Registry


@contextmanager
def registered(registry: Registry, name: str, item: Any) -> Iterator[Any]:
    """Register ``item`` under ``name`` for the duration of the block."""
    registry.register(name, item)
    try:
        yield item
    finally:
        registry.unregister(name)


def webstone(track: str, stage: str, *arguments: str) -> None:
    """Run ``webstone <track> <stage>`` in this process and require success."""
    try:
        cli.main([track, stage, *map(str, arguments)])
    except SystemExit as exit_:
        assert exit_.code in (0, None), f"webstone {track} {stage} exited with {exit_.code}"


def declared_stages(track: str) -> tuple[str, ...]:
    """The fixed stages the track registers, which its conformance test must drive."""
    import importlib

    return importlib.import_module(cli.TRACKS[track].cli).STAGES.supported()
