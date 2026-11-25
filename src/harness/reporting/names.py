"""Reader-facing names: registered display labels instead of raw ids, and Chinese interface text in Chinese pages."""

import re
from collections.abc import Mapping

from bs4 import BeautifulSoup


def display_name(labels: Mapping[str, object], name: str, language: str = "en") -> str:
    """The registered display label of ``name``; an unregistered name stays as it is.

    A label may be one string or a per-language mapping such as ``{"zh": ..., "en": ...}``.
    """
    label = labels.get(name)
    if isinstance(label, Mapping):
        label = label.get(language) or label.get("en")
    return str(label) if label else name


# Separators that join several names or a name and a scalar in one cell.
_CELL_PARTS = re.compile(r"\s*(?:·|/|×|,|、|;)\s*")


def check_display_names(html, labels: Mapping[str, object]) -> None:
    """Body cells show registered display names, never a raw registered id.

    Ids whose display label equals the id itself are names already; links, data
    attributes and verbatim evidence keep ids and are not read.
    """
    soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
    raw = {name for name in labels if display_name(labels, name) != name}
    if not raw:
        return
    for cell in soup.select("main td, main tbody th"):
        if cell.find_parent(["pre", "code"]) is not None or cell.find_parent(attrs={"data-verbatim": True}):
            continue
        for text in cell.find_all(string=True):
            if text.find_parent(["pre", "code"]) is not None or text.find_parent(attrs={"data-verbatim": True}):
                continue
            for part in _CELL_PARTS.split(str(text).strip()):
                if part in raw:
                    raise ValueError(f"Table cell shows the raw id {part!r} instead of its display name")


_CJK = re.compile(r"[㐀-鿿]")
# Interface words a Chinese page translates; names, units and metric acronyms (P50, F1) stay as they are.
INTERFACE_WORDS = ("Mean", "Median", "Average", "Total", "Status", "Result", "Results", "Pass", "Passed",
                   "Fail", "Failed", "Task", "Tasks", "Latency", "Score", "Rank", "Back", "Next",
                   "Previous", "Close", "Search", "Filter", "All", "Details", "Error", "Errors", "Source",
                   "Value", "Count", "Owner", "Revision")
_INTERFACE_WORD = re.compile(r"\b(?:" + "|".join(INTERFACE_WORDS) + r")\b")
_INTERFACE = "main h1, main h2, main h3, main h4, th, summary, button, label, caption, legend, dt, option"


def check_interface_language(html) -> None:
    """A Chinese page names its interface in Chinese.

    Headings, table headers, controls and labels without any Chinese text must not
    carry an untranslated interface word. Text in another declared language, verbatim
    evidence and ``translate="no"`` content keep their wording.
    """
    soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
    if not (soup.html and soup.html.get("lang", "").lower().startswith("zh")):
        return
    for node in soup.select(_INTERFACE):
        declared = node if node.has_attr("lang") else node.find_parent(attrs={"lang": True})
        if ((declared is not None and not declared["lang"].lower().startswith("zh"))
                or node.find_parent(attrs={"data-verbatim": True}) is not None
                or node.has_attr("data-verbatim")
                or node.find_parent(attrs={"translate": "no"}) is not None
                or node.find_parent(["pre", "code", "template"]) is not None):
            continue
        text = " ".join(str(part).strip() for part in node.find_all(string=True)
                        if part.find_parent(attrs={"data-verbatim": True}) is None).strip()
        if text and not _CJK.search(text) and (match := _INTERFACE_WORD.search(text)):
            raise ValueError(f"Untranslated interface text in a Chinese page: {match.group(0)!r} in <{node.name}> {text[:80]!r}")
