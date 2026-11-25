"""Home-page charts every track shares: a ranked bar with n/N and a two-rate dumbbell.

Charts are HTML and CSS rather than scaled SVG, so their text keeps the page's
font size at any width: at 390 px a label wraps above its bar instead of
shrinking. Every value stays in the text beside its mark, and each mark carries
its raw value so ``check_charts`` can prove the drawing matches the numbers.
"""

from dataclasses import dataclass

from bs4 import BeautifulSoup

from harness.reporting.presentation import esc


@dataclass(frozen=True)
class Bar:
    """One participant: ``label`` is rendered HTML, ``value`` is 0..1 or None, ``count`` is e.g. "476/530"."""

    label: str
    value: float | None
    count: str = ""
    href: str | None = None

    @classmethod
    def rate(cls, label, passed, total, href=None):
        """A pass rate whose n/N stays beside the bar."""
        return cls(label, passed / total if total else None, f"{passed}/{total}", href)


@dataclass(frozen=True)
class Pair:
    """Two rates for one participant, each 0..1 or None when unavailable."""

    label: str
    first: float | None
    second: float | None
    href: str | None = None


def _percent(value):
    return f"{100 * value:.1f}%"


def _label(label, href):
    return f'<a href="{esc(href)}">{label}</a>' if href else label


def ranked_bars(bars, *, title, caption):
    """Participants ordered by value (a rate or a 0..1 score); ties keep the caller's order.

    ``title`` and ``caption`` are rendered HTML; the caption names what n/N counts.
    """
    ordered = sorted(bars, key=lambda bar: -(bar.value if bar.value is not None else -1))
    items = []
    for bar in ordered:
        rate = bar.value
        width = 0 if rate is None else round(100 * rate, 1)
        text = (_percent(rate) if rate is not None else "—") + (f" · {bar.count}" if bar.count else "")
        items.append(
            f'<li class="chart-row" data-chart-value="{"" if rate is None else repr(float(rate))}">'
            f'<span class="chart-label">{_label(bar.label, bar.href)}</span>'
            f'<span class="chart-track" aria-hidden="true"><span class="chart-fill" style="width:{width}%"></span></span>'
            f'<span class="chart-value">{esc(text)}</span></li>'
        )
    return (f'<figure class="report-chart" data-chart="ranked-bar"><figcaption><b>{title}</b> '
            f'<span>{caption}</span></figcaption><ol class="chart-rows">{"".join(items)}</ol></figure>')


def dumbbell(pairs, *, title, caption, first_label, second_label):
    """Two rates per participant on one 0–100% axis, joined by the gap between them.

    Titles, captions and series labels are rendered HTML, so a localization pass sees each
    label as its own text node.
    """
    items = []
    for pair in pairs:
        marks = []
        for name, value in (("first", pair.first), ("second", pair.second)):
            if value is not None:
                marks.append(f'<span class="chart-dot {name}" data-chart-mark="{name}" data-chart-value="{float(value)!r}" '
                             f'style="left:{round(100 * value, 1)}%"></span>')
        gap = ""
        if pair.first is not None and pair.second is not None:
            low, high = sorted((pair.first, pair.second))
            gap = (f'<span class="chart-gap" style="left:{round(100 * low, 1)}%;'
                   f'width:{round(100 * (high - low), 1)}%"></span>')
        # Each series keeps its own value and text, so the check can pair them.
        text = " → ".join(
            f'<span data-chart-series="{name}" data-chart-value="{"" if value is None else repr(float(value))}">'
            f"<span>{label}</span> {_percent(value) if value is not None else '—'}</span>"
            for name, label, value in (("first", first_label, pair.first), ("second", second_label, pair.second)))
        items.append(
            f'<li class="chart-row"><span class="chart-label">{_label(pair.label, pair.href)}</span>'
            f'<span class="chart-track dumbbell" aria-hidden="true">{gap}{"".join(marks)}</span>'
            f'<span class="chart-value">{text}</span></li>'
        )
    legend = (f'<span class="chart-legend"><span class="chart-dot first"></span><span>{first_label}</span>'
              f'<span class="chart-dot second"></span><span>{second_label}</span></span>')
    return (f'<figure class="report-chart" data-chart="dumbbell"><figcaption><b>{title}</b> '
            f'<span>{caption}</span>{legend}</figcaption><ol class="chart-rows">{"".join(items)}</ol></figure>')


def charts(*figures):
    """The home-page chart block: its own style once, then the figures in order."""
    rendered = "".join(figure for figure in figures if figure)
    return f'<div class="report-charts" data-report-charts><style>{STYLE}</style>{rendered}</div>' if rendered else ""


STYLE = (
    ".report-charts{display:grid;gap:16px;margin:16px 0 24px}"
    ".report-chart{margin:0;padding:16px;border:1px solid color-mix(in srgb,currentColor 16%,transparent);"
    "border-radius:12px;min-width:0}"
    ".report-chart figcaption{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;margin-bottom:12px;"
    "font-size:.95rem;line-height:1.45}"
    ".report-chart figcaption>span:not(.chart-legend){opacity:.78}"
    ".chart-rows{list-style:none;margin:0;padding:0;display:grid;gap:10px}"
    ".chart-row{display:grid;grid-template-columns:minmax(8rem,14rem) minmax(0,1fr) minmax(9rem,auto);"
    "gap:4px 12px;align-items:center;font-size:.9rem;line-height:1.4}"
    ".chart-label{overflow-wrap:anywhere;font-weight:600}"
    ".chart-value{font-variant-numeric:tabular-nums;white-space:nowrap}"
    ".chart-track{position:relative;display:block;height:12px;border-radius:6px;"
    "background:color-mix(in srgb,currentColor 12%,transparent)}"
    ".chart-fill{position:absolute;inset:0 auto 0 0;border-radius:6px;background:var(--chart-first,#2f6fde)}"
    ".chart-track.dumbbell{background:none;border-top:1px dashed color-mix(in srgb,currentColor 25%,transparent);"
    "height:14px;border-radius:0;margin-top:7px}"
    ".chart-gap{position:absolute;top:-3px;height:4px;border-radius:2px;"
    "background:color-mix(in srgb,currentColor 35%,transparent)}"
    ".chart-dot{display:inline-block;width:12px;height:12px;border-radius:50%;"
    "background:var(--chart-first,#2f6fde);border:2px solid var(--bg,Canvas)}"
    ".chart-dot.second{background:var(--chart-second,#d9822b)}"
    ".chart-track .chart-dot{position:absolute;top:-8px;transform:translateX(-50%)}"
    ".chart-legend{display:inline-flex;gap:6px;align-items:center}"
    ".chart-legend .chart-dot+*{margin-right:8px}"
    "@media (max-width:640px){.chart-row{grid-template-columns:minmax(0,1fr) auto}"
    ".chart-label{grid-column:1/-1}.chart-track{grid-column:1}.chart-value{grid-column:2;white-space:normal}"
    "[data-chart=dumbbell] .chart-track,[data-chart=dumbbell] .chart-value{grid-column:1/-1}}"
)


def check_charts(document, *, required=False):
    """Every mark must match its stated value; a home page that ``required`` charts must have one."""
    soup = BeautifulSoup(document, "html.parser") if isinstance(document, str) else document
    figures = soup.select("[data-report-charts] figure[data-chart]")
    if required and not figures:
        raise ValueError("Home page lacks its comparison chart")
    for figure in figures:
        rows = figure.select("li.chart-row")
        if not rows:
            raise ValueError("Chart has no participants")
        for row in rows:
            if not row.select_one(".chart-label").get_text(strip=True) or not row.select_one(".chart-value").get_text(strip=True):
                raise ValueError("Chart row must keep its label and exact value as text")
            if figure["data-chart"] == "ranked-bar":
                _check_mark(row.select_one(".chart-fill"), row.get("data-chart-value", ""), "width",
                            row.select_one(".chart-value"))
                continue
            series = row.select("[data-chart-series]")
            if [item["data-chart-series"] for item in series] != ["first", "second"]:
                raise ValueError("Dumbbell row must keep both series as text")
            for item in series:
                value = item.get("data-chart-value", "")
                mark = row.select_one(f'[data-chart-mark="{item["data-chart-series"]}"]')
                if not value:
                    if mark is not None or "—" not in item.get_text():
                        raise ValueError("Unavailable series must show no mark and a dash")
                    continue
                if mark is None or mark.get("data-chart-value") != value:
                    raise ValueError("Chart series lacks its mark")
                _check_mark(mark, value, "left", item)
        if figure["data-chart"] == "ranked-bar":
            values = [float(row["data-chart-value"]) for row in rows if row.get("data-chart-value")]
            if values != sorted(values, reverse=True):
                raise ValueError("Ranked chart is not in rank order")
    return soup


def _check_mark(mark, value, prop, text):
    expected = 0.0 if value == "" else round(100 * float(value), 1)
    if _style_percent(mark, prop) != expected:
        raise ValueError("Chart mark differs from its value")
    if value and f"{expected:.1f}%" not in text.get_text():
        raise ValueError("Chart value text differs from its mark")


def _style_percent(node, prop):
    for part in (node.get("style") or "").split(";"):
        key, _, value = part.partition(":")
        if key.strip() == prop:
            return float(value.strip().rstrip("%"))
    raise ValueError("Chart mark lacks its position")
