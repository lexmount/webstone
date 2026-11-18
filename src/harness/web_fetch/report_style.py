"""Shared visual styling for the current report and standalone editorial export."""
from pathlib import Path


def asset_text(name: str) -> str:
    return (Path(__file__).parent / "assets" / name).read_text(encoding="utf-8")


RANKING_STICKY = """
#ranking-overall { --rank-column: 3.5rem; border-collapse: separate; border-spacing: 0; }
#ranking-overall tr > :first-child,
#ranking-overall tr > :nth-child(2) { position: sticky; left: 0; z-index: 2; }
#ranking-overall tr > :first-child { min-width: var(--rank-column); width: var(--rank-column); max-width: var(--rank-column); }
#ranking-overall tr > :nth-child(2) { left: var(--rank-column); border-right: 1px solid var(--line); }
#ranking-overall tbody tr > :nth-child(-n+2) { background: var(--panel); }
#ranking-overall tbody tr:hover > :nth-child(-n+2) { background: var(--panel2); }
@media (max-width: 640px) {
  #ranking-overall tr > :nth-child(2) {
    width: 8rem;
    min-width: 8rem;
    max-width: 8rem;
    overflow-wrap: anywhere;
    white-space: normal;
  }
}
"""

STYLE = asset_text("report-style.css")
