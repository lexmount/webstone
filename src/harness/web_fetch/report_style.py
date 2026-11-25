"""Web Fetch report assets and ranking-table styling; shared styling lives in harness.reporting.style."""
from pathlib import Path


def asset_text(name: str) -> str:
    return (Path(__file__).parent / "assets" / name).read_text(encoding="utf-8")


RANKING_STICKY = """
#ranking-overall { --rank-column: 3.5rem; border-collapse: separate; border-spacing: 0; }
#ranking-overall tbody td { height: 4.25rem; box-sizing: border-box; vertical-align: middle; }
#ranking-overall tr > :first-child,
#ranking-overall tr > :nth-child(2) { position: sticky; left: 0; z-index: 2; }
#ranking-overall tr > :first-child { min-width: var(--rank-column); width: var(--rank-column); max-width: var(--rank-column); }
#ranking-overall tr > :nth-child(2) { left: var(--rank-column); border-right: 1px solid var(--line); }
#ranking-overall tbody tr > :nth-child(-n+2) { background: var(--panel); }
#ranking-overall tbody tr:hover > :nth-child(-n+2) { background: var(--panel2); }
#ranking-overall .method-unit-price { min-width: 16rem; }
#ranking-overall .method-price-amount,
#ranking-overall .method-price-note { display: block; max-width: none; white-space: nowrap !important; }
@media (max-width: 640px) {
  #ranking-overall .method-unit-price { min-width: 8rem; }
  #ranking-overall tr > :nth-child(2) {
    width: 8rem;
    min-width: 8rem;
    max-width: 8rem;
    overflow-wrap: anywhere;
    white-space: normal;
  }
}
"""
