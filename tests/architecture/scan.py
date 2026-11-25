"""Source scans behind the architecture tests.

Run ``uv run --frozen python tests/architecture/scan.py`` to regenerate both
allowlists from the current tree. The tests fail when an allowlist misses an
occurrence or keeps one that is gone, so a regenerated list may only shrink.
"""

import ast
import json
import re
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
# Packages whose child modules are adapters: each one names only the instance it implements.
ADAPTER_PACKAGES = ("harness.environments.browser",)
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


# Shared Web Extract scoring and report modules: they reach benchmarks only through their adapters.
WEB_EXTRACT_SHARED = tuple(f"harness.web_extract.{name}" for name in (
    "analysis", "report", "report_ui", "report_suite", "model_comparison"))


def benchmark_module_imports() -> dict[str, list[str]]:
    """Registered Web Extract task-adapter modules imported directly by shared modules."""
    from harness.web_extract.dataset import BENCHMARKS

    adapters = {item.module for _, item in BENCHMARKS.items() if item.module}
    found = modules()
    occurrences = {}
    for module in WEB_EXTRACT_SHARED:
        bad = sorted(imported_modules(found[module], set(found)) & adapters)
        if bad:
            occurrences[module] = bad
    return occurrences


def context_variable_modules() -> list[str]:
    """Track modules that pass state through context variables instead of arguments (spec rule 4)."""
    found = []
    for module, path in modules().items():
        tree = ast.parse(path.read_bytes(), filename=str(path))
        if track_of(module) and any(
                (isinstance(node, ast.ImportFrom) and node.module == "contextvars")
                or (isinstance(node, ast.Import) and any(alias.name == "contextvars" for alias in node.names))
                for node in ast.walk(tree)):
            found.append(module)
    return sorted(found)


def registered_names() -> frozenset[str]:
    """Method, provider and browser names from the registries that own them."""
    from harness.environments.browser import BROWSERS
    from harness.web_extract.pipeline import METHODS
    from harness.web_fetch.providers.base import PROVIDERS
    from harness.web_search.adapters import ADAPTERS

    providers = json.loads((ROOT / "configs/providers.example.json").read_text(encoding="utf-8"))["providers"]
    names = {*PROVIDERS.names(), *providers, *ADAPTERS.names(), *METHODS.names(), *BROWSERS.names()}
    return frozenset(names - GENERIC_NAMES)


def instance_literals() -> dict[str, list[str]]:
    names = registered_names()
    occurrences = {}
    for module, path in modules().items():
        if not is_shared(module, path) or any(module.startswith(package + ".") for package in ADAPTER_PACKAGES):
            continue
        literals = sorted({node.value for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path)))
                           if isinstance(node, ast.Constant) and isinstance(node.value, str)
                           and node.value in names})
        if literals:
            occurrences[module] = literals
    return occurrences


# Shared Web Fetch catalogs: interface text the renderer and its English pass use for every report.
WEB_FETCH = SOURCE / "harness/web_fetch"
SHARED_CATALOGS = ("report-translations.json", "messages.json", "report_i18n.py")
# Technical tokens that look like values but are part of interface text.
TECHNICAL_TOKENS = re.compile(r"\d+ [KM]iB|\b404\b|SHA-256|HTTP \d{3}|UTF-8|P\d{2}|F1|\b1,000\b")
MEASURED_VALUE = re.compile(
    r"\d+\.\d*[1-9]\d*\s*(?:%|ms|毫秒|秒|s\b|seconds?)"  # a measured decimal with its unit
    r"|(?<![\d.,])\d{1,3}(?:,\d{3})+(?![\d.])"   # a grouped count
    r"|(?<![\d.,/])\d{3,}(?![\d.,%])"              # a count or size of three or more digits
    r"|\d{4}-\d{2}-\d{2}"                         # a date
    r"|(?<![\w.])\d+\.\d+\.\d+")                  # a version
# Registered names that are also ordinary words in report prose.
GENERIC_NAME_TOKENS = frozenset({"browser", "headless", "http", "subprocess"})


def method_name_tokens() -> frozenset[str]:
    """Registered method names as they appear in prose: product tokens and display labels."""
    tokens = {re.split(r"[-_]", name)[0] for name in registered_names()} - GENERIC_NAME_TOKENS
    methods = json.loads((WEB_FETCH / "data/report-methods.json").read_text(encoding="utf-8"))["methods"]
    for row in methods.values():
        label = row.get("label")
        tokens.update(label.values() if isinstance(label, dict) else [label] if label else [])
    return frozenset(tokens)


def catalog_strings(name: str) -> list[str]:
    path = WEB_FETCH / name
    if path.suffix == ".py":
        return [node.value for node in ast.walk(ast.parse(path.read_bytes(), filename=str(path)))
                if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    found = []

    def walk(value):
        if isinstance(value, str):
            found.append(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                if name == "report-translations.json":  # its keys are source text, not message ids
                    walk(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(json.loads(path.read_text(encoding="utf-8")))
    return found


def catalog_instance_text() -> dict[str, list[str]]:
    """Entries of the shared catalogs that quote a measured value or name a method in a sentence."""
    tokens = method_name_tokens()
    name = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, sorted(tokens, key=len, reverse=True)))
                      + r")(?![A-Za-z0-9])", re.I)
    occurrences = {}
    for catalog in SHARED_CATALOGS:
        found = []
        for text in catalog_strings(catalog):
            named = name.search(text)
            if (named and named.group().strip() != text.strip()) or MEASURED_VALUE.search(TECHNICAL_TOKENS.sub("", text)):
                found.append(text)
        if found:
            occurrences[catalog] = sorted(set(found))
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
