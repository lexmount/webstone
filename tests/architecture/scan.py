"""Source scans behind the architecture tests.

Run ``uv run --frozen python tests/architecture/scan.py`` to regenerate both
allowlists from the current tree. The tests fail when an allowlist misses an
occurrence or keeps one that is gone, so a regenerated list may only shrink.
"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src"
LAYERING_ALLOWLIST = Path(__file__).with_name("layering-allowlist.json")
INSTANCE_LITERAL_ALLOWLIST = Path(__file__).with_name("instance-literal-allowlist.json")

TRACKS = frozenset({"web_fetch", "web_search", "web_extract", "trajectory_replay", "browser_use", "lexbench"})
SHARED_PACKAGES = frozenset({"reporting", "results", "benchmarks", "environments"})
# The root command registry names tracks by design; it imports them only when selected.
ROOT_DISPATCHER = "harness.cli"
# Registered names that are also ordinary words in shared code.
GENERIC_NAMES = frozenset({
    "fixture",  # the synthetic Web Fetch provider and the general test-fixture term
    "http",  # the plain HTTP provider type and the URL scheme
})


def modules() -> dict[str, Path]:
    found = {}
    for path in sorted((SOURCE / "harness").rglob("*.py")):
        parts = list(path.relative_to(SOURCE).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        found[".".join(parts)] = path
    return found


def track_of(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in TRACKS else None


def is_shared(module: str, path: Path) -> bool:
    parts = module.split(".")
    if module == ROOT_DISPATCHER or len(parts) < 2:
        return False
    return parts[1] in SHARED_PACKAGES or (len(parts) == 2 and path.name != "__init__.py")


def imported_modules(path: Path, known: set[str]) -> set[str]:
    imported = set()
    for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names if alias.name.startswith("harness."))
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and (
                node.module == "harness" or node.module.startswith("harness.")):
            for alias in node.names:
                child = f"{node.module}.{alias.name}"
                imported.add(child if child in known else node.module)
    return imported


def layering_violations() -> dict[str, list[str]]:
    found = modules()
    violations = {}
    for module, path in found.items():
        own_track, shared = track_of(module), is_shared(module, path)
        if not (own_track or shared):
            continue
        bad = sorted(name for name in imported_modules(path, set(found))
                     if (target := track_of(name)) and target != own_track)
        if bad:
            violations[module] = bad
    return violations


def registered_names() -> frozenset[str]:
    """Method, provider and browser names from the registries that own them."""
    from harness.trajectory_replay.participants import BROWSER_ADAPTERS
    from harness.web_extract.pipeline import METHODS
    from harness.web_fetch.providers.base import PROVIDERS
    from harness.web_search.adapters import ADAPTERS

    providers = json.loads((ROOT / "configs/providers.example.json").read_text(encoding="utf-8"))["providers"]
    names = {*PROVIDERS.names(), *providers, *ADAPTERS, *METHODS, *BROWSER_ADAPTERS}
    return frozenset(names - GENERIC_NAMES)


def instance_literals() -> dict[str, list[str]]:
    names = registered_names()
    occurrences = {}
    for module, path in modules().items():
        if not is_shared(module, path):
            continue
        literals = sorted({node.value for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path)))
                           if isinstance(node, ast.Constant) and isinstance(node.value, str)
                           and node.value in names})
        if literals:
            occurrences[module] = literals
    return occurrences


def read_allowlist(path: Path) -> dict[str, list[str]]:
    return json.loads(path.read_text(encoding="utf-8"))


def allowlist_drift(found: dict[str, list[str]], allowed: dict[str, list[str]]) -> tuple[list[str], list[str]]:
    """Return (new occurrences, stale allowlist entries) as ``module -> name`` strings."""
    def pairs(mapping):
        return {f"{module} -> {name}" for module, names in mapping.items() for name in names}

    current, listed = pairs(found), pairs(allowed)
    return sorted(current - listed), sorted(listed - current)


def write_allowlist(path: Path, found: dict[str, list[str]]) -> None:
    path.write_text(json.dumps(dict(sorted(found.items())), indent=2) + "\n", encoding="utf-8")


def shrink_allowlist(path: Path, found: dict[str, list[str]]) -> list[str]:
    """Write ``found`` only when it adds nothing; return the additions otherwise."""
    added, _ = allowlist_drift(found, read_allowlist(path)) if path.exists() else ([], [])
    if not added:
        write_allowlist(path, found)
    return added


if __name__ == "__main__":
    sys.path.insert(0, str(SOURCE))
    growth = shrink_allowlist(LAYERING_ALLOWLIST, layering_violations())
    growth += shrink_allowlist(INSTANCE_LITERAL_ALLOWLIST, instance_literals())
    if growth:
        sys.exit("Allowlists only shrink; fix these instead of listing them:\n  " + "\n  ".join(growth))
