"""The one report message catalog API: a JSON file per track, keyed by language.

A message chooses a counted noun with ``{name|singular|plural}``: the singular form
when the value ``name`` is 1, else the plural form. Languages without plural nouns
write the noun once. ``plural`` applies the same rule to text built in code.
"""
import re
import string
from functools import cache
from pathlib import Path

from harness.record_io import read_json


@cache
def _catalog(path: Path) -> dict:
    return read_json(path)


def catalog_section(path: Path, name: str) -> dict:
    """A named non-language section of a catalog, such as frozen-source translations."""
    return _catalog(path).get(name) or {}


# A doubled brace escapes the form, as it does a placeholder.
_PLURAL = re.compile(r"(?<!\{)\{(\w+)\|([^{}|]*)\|([^{}|]*)\}(?!\})")


def plural(count, singular: str, plural_form: str) -> str:
    """The noun form for ``count``: singular only for exactly one, also when written as text."""
    if isinstance(count, str):
        digits = count.replace(",", "").strip()
        count = int(digits) if digits.isdigit() else None
    return singular if count == 1 else plural_form


def placeholders(message: str) -> set[str]:
    """The value names a message reads, including those that only choose a noun form."""
    names = {match.group(1) for match in _PLURAL.finditer(message)}
    return names | {field for _, field, _, _ in string.Formatter().parse(_PLURAL.sub("", message)) if field}


def format_message(message: str, values: dict) -> str:
    message = _PLURAL.sub(lambda m: plural(values[m.group(1)], m.group(2), m.group(3)), message)
    return message.format_map(values)


def translate(path: Path, key: str, language: str, **values):
    """Select one declared language; preserve values supplied by the report model."""
    try:
        message = _catalog(path)[language][key]
    except KeyError as exc:
        raise ValueError(f"Missing report translation: {language}/{key}") from exc
    if isinstance(message, list):
        return [format_message(item, values) for item in message]
    return format_message(message, values)


def catalog_text(path: Path, language: str):
    """Bind `t(id, english, **values)`: English is inline source text, other languages read the catalog."""
    def t(key: str, source: str, /, **values) -> str:
        if language == "en":
            return format_message(source, values)
        return translate(path, key, language, **values)
    return t


def source_text(path: Path, language: str, source: str, /, *, context=None, **values):
    """Render source-language (English) text that is also its own catalog key.

    ``context`` separates one English label with several translations; the
    catalog stores it as ``context|source``. Values fill named placeholders.
    """
    message = source
    if language != "en":
        key = f"{context}|{source}" if context else source
        try:
            message = _catalog(path)[language][key]
        except KeyError as exc:
            raise ValueError(f"Missing report translation: {language}/{key}") from exc
    return format_message(message, values) if values else message
