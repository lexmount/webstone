"""Reader-facing names: registered display labels instead of raw ids, and Chinese interface text in Chinese pages."""

import re
from collections.abc import Mapping

from bs4 import BeautifulSoup, NavigableString


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


def _edition(soup) -> str:
    """The catalog edition of a page: ``zh`` for any Chinese ``lang``, else ``en``."""
    language = (soup.html.get("lang", "") if soup.html else "").lower()
    return "zh" if language.startswith("zh") else "en"


def check_display_names(html, labels: Mapping[str, object]) -> None:
    """Body cells show registered display names, never a raw registered id.

    Labels resolve in the page's own language. Ids whose display label in that
    language equals the id itself are names already; links, data attributes and
    verbatim evidence keep ids and are not read.
    """
    soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
    language = _edition(soup)
    raw = {name for name in labels if display_name(labels, name, language) != name}
    if not raw:
        return
    for cell in soup.select("main td, main tbody th"):
        # Visible text only: comments and other non-rendered nodes are not names.
        texts = [str(text) for text in cell.find_all(string=True)
                 if type(text) is NavigableString
                 and text.find_parent(["pre", "code", "script", "style", "template"]) is None
                 and text.find_parent(attrs={"data-verbatim": True}) is None]
        # Each text node, and the cell read across inline elements, so a split id is still seen.
        for text in (*texts, "".join(texts)):
            # The whole text first: an id may itself contain a separator.
            for part in (text.strip(), *_CELL_PARTS.split(text.strip())):
                if part in raw:
                    table = cell.find_parent("table")
                    where = (table.get("id") or "") if table is not None else ""
                    raise ValueError(f"Table cell shows the raw id {part!r} instead of its display name"
                                     + (f" in table {where!r}" if where else "")
                                     + f": {cell.get_text(' ', strip=True)[:80]!r}")

_CJK = re.compile(r"[\u3400-\u9fff]")
# Interface words a Chinese page translates; names, units and metric acronyms (P50, F1) stay as they are.
INTERFACE_WORDS = ("Mean", "Median", "Average", "Total", "Status", "Result", "Results", "Pass", "Passed",
                   "Fail", "Failed", "Task", "Tasks", "Latency", "Score", "Rank", "Back", "Next",
                   "Previous", "Close", "Details", "Owner", "Revision")
_INTERFACE_WORD = re.compile(r"\b(?:" + "|".join(INTERFACE_WORDS) + r")\b")
_INTERFACE = "main h1, main h2, main h3, main h4, th, summary, button, label, caption, legend, dt, option"


def _interface_text(string) -> bool:
    """Whether a text node is report-authored Chinese interface text rather than kept wording."""
    if type(string) is not NavigableString:
        return False  # comments and other nodes a reader never sees
    declared = string.find_parent(attrs={"lang": True})
    return not ((declared is not None and not declared["lang"].lower().startswith("zh"))
                or string.find_parent(attrs={"data-verbatim": True}) is not None
                or string.find_parent(attrs={"translate": "no"}) is not None
                or string.find_parent(["pre", "code", "template", "script", "style"]) is not None)


def check_interface_language(html) -> None:
    """A Chinese page names its interface in Chinese.

    Headings, table headers, controls and labels without any Chinese text must not
    carry an untranslated interface word. Text in another declared language, verbatim
    evidence and ``translate="no"`` content keep their wording, on the element itself
    or anywhere inside it.
    """
    soup = BeautifulSoup(html, "html.parser") if isinstance(html, str) else html
    if _edition(soup) != "zh":
        return
    for node in soup.select(_INTERFACE):
        text = " ".join(str(part).strip() for part in node.find_all(string=True) if _interface_text(part)).strip()
        if text and not _CJK.search(text) and (match := _INTERFACE_WORD.search(text)):
            raise ValueError(f"Untranslated interface text in a Chinese page: {match.group(0)!r} in <{node.name}> {text[:80]!r}")
