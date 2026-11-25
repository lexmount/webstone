"""Shared visual styling for report tracks and the standalone editorial export."""
from pathlib import Path

from harness.reporting.shell import CSS

STYLE = CSS + (Path(__file__).parent / "report-style.css").read_text(encoding="utf-8")
