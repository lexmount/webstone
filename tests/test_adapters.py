import contextlib

import pytest

from harness.adapters import (
    STAGES,
    BenchmarkAdapter,
    Capabilities,
    MethodAdapter,
    Registry,
    TrackStages,
)


def test_registry_resolves_and_lists_names():
    registry = Registry("method")

    @registry.register("b")
    class B:
        pass

    registry.register("a", object)
    assert registry.names() == ("a", "b")
    assert registry.get("b") is B
    assert "a" in registry and len(registry) == 2


def test_registry_rejects_conflicts_and_unknown_names():
    registry = Registry("benchmark")
    registry.register("x", 1)
    registry.register("x", 1)  # idempotent for the same object
    with pytest.raises(ValueError, match="already registered"):
        registry.register("x", 2)
    with pytest.raises(ValueError, match="Unknown benchmark: y"):
        registry.get("y")
    with pytest.raises(ValueError, match="non-empty"):
        registry.register("", 3)
    registry.unregister("x")
    assert "x" not in registry
    with pytest.raises(ValueError, match="Unknown benchmark: x"):
        registry.unregister("x")


def test_capabilities_are_declared_flags_and_values():
    capabilities = Capabilities(frozenset({"loopback_only"}), {"max_bytes": 10})
    assert capabilities.has("loopback_only") and not capabilities.has("other")
    assert capabilities.value("max_bytes") == 10 and capabilities.value("missing", 1) == 1


def test_protocols_accept_minimal_adapters():
    class Method:
        name = "m"

        def capabilities(self):
            return Capabilities()

        def implementation_identity(self):
            return {"version": "1"}

        def validate(self):
            return []

    class Benchmark:
        name = "b"

        def cases(self, profile):
            return []

        def inputs(self, cases):
            return contextlib.nullcontext()

        def score(self, case, scored_input):
            return None

        def populations(self):
            return []

        def views(self):
            return []

    assert isinstance(Method(), MethodAdapter)
    assert isinstance(Benchmark(), BenchmarkAdapter)
    assert not isinstance(object(), MethodAdapter)


def test_track_stages_fix_the_verbs():
    def run(argv):
        return argv

    stages = TrackStages(run=run, report=run, extra={"prepare": run})
    assert stages.supported() == ("run", "report")
    assert set(stages.supported()) <= set(STAGES)
    assert stages.names() == ("run", "report", "prepare")
    assert stages.command("prepare") is run
    with pytest.raises(ValueError, match="Unknown stage: score"):
        stages.command("score")
    with pytest.raises(ValueError, match="cannot replace fixed stages"):
        TrackStages(extra={"run": run})
