"""The Browser Use catalog file, and the link rewrite that places the English edition under `en/`.

English is the source language of the report; `messages.json` holds its Chinese and is read
through the shared catalog API in `harness.reporting.locales`.
"""

from __future__ import annotations

import html
import posixpath
import re
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from harness.reporting.locales import catalog_section

MESSAGES = Path(__file__).with_name("messages.json")


def english_rationale(value: str) -> str:
    """Translate a rationale of the hash-bound selection; those without English stay raw."""
    return catalog_section(MESSAGES, "rationale_en").get(value, value)


class _PageWriter(HTMLParser):
    """Write a page back with every attribute value escaped the same way."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs) -> None:
        self.parts.append(self._tag(tag, attrs))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.parts.append(self._tag(tag, attrs, closed=True))

    @staticmethod
    def _tag(tag: str, attrs, *, closed: bool = False) -> str:
        rendered = [
            key if value is None else f'{key}="{html.escape(value, quote=True)}"'
            for key, value in attrs
        ]
        suffix = " /" if closed else ""
        return f"<{tag}{(' ' + ' '.join(rendered)) if rendered else ''}{suffix}>"

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")


def _relative(source: PurePosixPath, target: PurePosixPath) -> str:
    return posixpath.relpath(str(target), str(source.parent))


def _rewrite_url(value: str) -> str:
    parsed = urlsplit(html.unescape(value))
    if parsed.scheme or parsed.netloc or value.startswith(("mailto:", "data:", "javascript:")):
        return value
    if not parsed.path:
        return value
    # Generated report pages declare a root-relative <base>, so local links are
    # resolved from the report root even when the source page is nested.
    resolved = PurePosixPath(posixpath.normpath(parsed.path.lstrip("/")))
    is_page = resolved.suffix.lower() in {".html", ".htm"} or not resolved.suffix
    target = PurePosixPath("en") / resolved if is_page else resolved
    return html.escape(urlunsplit(("", "", str(target), parsed.query, parsed.fragment)), quote=True)


def _rewrite_links(page: str) -> str:
    pattern = re.compile(r'(?P<prefix>\b(?:href|src)=)(?P<quote>["\'])(?P<url>.*?)(?P=quote)')
    return pattern.sub(
        lambda match: (
            match.group("prefix")
            + match.group("quote")
            + _rewrite_url(match.group("url"))
            + match.group("quote")
        ),
        page,
    )


def localize_html(page: str, source: PurePosixPath) -> str:
    """Place one page of the English pass under `en/`."""
    english = PurePosixPath("en") / source
    page = page.replace('<html lang="zh-CN">', '<html lang="en">', 1)
    had_base = bool(re.search(r'<base\s+href=["\'][^"\']*["\']>', page, flags=re.I))
    page = re.sub(
        r'<base\s+href=["\'][^"\']*["\']>', "<base data-english-base>", page, count=1, flags=re.I
    )
    writer = _PageWriter()
    writer.feed(page)
    writer.close()
    # The English pass names the Chinese route of each page; keep that one link outside `en/`.
    localized = re.sub(r'href="en/([^"]*)" hreflang="zh-CN"', r'href="\1" hreflang="zh-CN"',
                       _rewrite_links("".join(writer.parts)))
    if had_base:
        base = _relative(english, PurePosixPath(".")) + "/"
        localized = localized.replace("<base data-english-base>", f'<base href="{base}">', 1)
    return localized


def write_english_report(pages: Path, out: Path) -> None:
    """Publish every HTML page of the English pass under `out/en`."""
    for page in sorted(pages.rglob("*.html")):
        source = PurePosixPath(page.relative_to(pages).as_posix())
        target = out / "en" / Path(str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(localize_html(page.read_text(encoding="utf-8"), source), encoding="utf-8")
