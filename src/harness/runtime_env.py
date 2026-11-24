"""Apply the repository .env to a run; ``harness.env_file`` only parses dotenv files."""

import os
import re
from pathlib import Path
from typing import Any

from harness.distribution import repository_root
from harness.env_file import parse_env_file

ROOT = repository_root()


def _evaluation_credential(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9_]+_(?:API_KEY|API_TOKEN|ACCOUNT_ID)", name))


def load_runtime_env(path: Path | None = None) -> None:
    """Use the repository .env by default; explicit missing files are errors."""
    selected = path if path is not None else (ROOT / ".env")
    if path is not None and not selected.exists():
        raise FileNotFoundError(selected)
    # Credentials may never leak into a run from the parent shell.
    for key in list(os.environ):
        if _evaluation_credential(key):
            os.environ.pop(key)
    if path is not None or selected.exists():
        load_env_file(selected, override_credentials=True,
                      credentials_allowed=selected.resolve() == ((ROOT / ".env")).resolve())
    if path is not None:
        os.environ.setdefault("WEBSTONE_REPORT_LANGUAGE", "en")


def repository_env() -> dict[str, str]:
    """Read the repository .env alone, leaving the process environment untouched."""
    selected = ROOT / ".env"
    if not selected.exists():
        return {}
    return {key: value for key, value in parse_env_file(selected).items() if value}


def load_env_file(path: Path, *, override_credentials: bool = False, credentials_allowed: bool = True) -> None:
    """Apply dotenv assignments to the process environment."""
    for key, value in parse_env_file(path).items():
        if _evaluation_credential(key) and not credentials_allowed:
            raise ValueError("Evaluation credentials must come from the repository .env")
        if override_credentials and _evaluation_credential(key):
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?}", value)
        if match:
            return os.getenv(match.group(1)) or match.group(2) or ""
        return value
    if isinstance(value, list):
        return [expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: expand_env(item) for key, item in value.items()}
    return value
