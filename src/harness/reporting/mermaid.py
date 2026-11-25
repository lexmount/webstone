"""The one Mermaid runtime: Mermaid 11.4.1 ``dist/mermaid.min.js``.

The file ships as package data. Web Fetch and WCXB reports copy it beside their own scripts.
Web Search and the public WebArena replay inline their page scripts into hash-bound packages
and still load the same release from a CDN with its upstream SRI integrity.
"""

import hashlib
import re
from pathlib import Path

MERMAID_VERSION = "11.4.1"
MERMAID_RUNTIME = "mermaid.min.js"
MERMAID_LICENSE = "mermaid-LICENSE.txt"
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"
MERMAID_INTEGRITY = "sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I"
MERMAID_SHA256 = "a43bc1afd446f9c4cc66ac5dd45d02e8d65e26fc5344ec0ef787f88d6ddb6f9e"
MERMAID_LICENSE_SHA256 = "ec9fb67dcb25eccc416ed56e1aab819222c805a2a4bfe4cb19e7556bf2ffde80"

_PACKAGE = Path(__file__).resolve().parents[1]
_SOURCES = {
    MERMAID_RUNTIME: (_PACKAGE / "reporting/vendor/mermaid.min.js", MERMAID_SHA256),
    MERMAID_LICENSE: (_PACKAGE / "licenses/Mermaid-MIT.txt", MERMAID_LICENSE_SHA256),
}
_MERMAID_URL_PATTERN = re.compile(r"""(?:https?:)?//[^\s'"`<>()/]+/[^\s'"`<>()]*mermaid[^\s'"`<>()]*""", re.I)


def mermaid_file(name: str) -> bytes:
    """Return the vendored runtime or its license after checking the pinned digest."""
    path, digest = _SOURCES[name]
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"Vendored Mermaid file differs from its pinned digest: {name}")
    return data


def install_mermaid(directory: Path) -> None:
    """Copy the runtime and its license into a report's script directory."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in _SOURCES:
        (directory / name).write_bytes(mermaid_file(name))


def mermaid_script_source(element: str, script_url: str) -> str:
    """Return JavaScript pointing script ``element`` at the runtime installed beside ``script_url``.

    ``script_url`` is a JavaScript expression for the absolute URL of the report script, so the
    runtime resolves from nested routes, language routes and packaged shells alike.
    """
    return f"{element}.src=new URL('{MERMAID_RUNTIME}',{script_url}).href"


def mermaid_script_attributes(element: str) -> str:
    """Return JavaScript pointing script ``element`` at the same release on the CDN."""
    return (
        f"{element}.src='{MERMAID_URL}';"
        f"{element}.integrity='{MERMAID_INTEGRITY}';"
        f"{element}.crossOrigin='anonymous'"
    )


def mermaid_urls(text: str) -> list[str]:
    """Return every remote URL in ``text`` whose path names Mermaid."""
    return _MERMAID_URL_PATTERN.findall(text)


def unpinned_mermaid_urls(text: str) -> list[str]:
    """Return every Mermaid URL in ``text`` other than the pinned CDN release."""
    return [url for url in mermaid_urls(text) if url != MERMAID_URL]
