"""English is the Search report source language; `messages.json` holds its Chinese translation."""
from pathlib import Path

from harness.reporting.locales import source_text

CATALOG = Path(__file__).with_suffix(".json")


def zh_text(source, /, *, context=None, **values):
    """Translate English source text; `context` separates one English label with several Chinese meanings."""
    return source_text(CATALOG, "zh", source, context=context, **values)


def report_text(source, /, zh, *, context=None, **values):
    """Render English source text in the requested edition; `values` fill its named placeholders."""
    return source_text(CATALOG, "zh" if zh else "en", source, context=context, **values)
