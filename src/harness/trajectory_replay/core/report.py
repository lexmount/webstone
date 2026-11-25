"""Report helpers shared by the Trajectory Replay reports."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from harness.record_io import write_json
from harness.reporting import charts, explorer, shell
from harness.reporting.presentation import unit_number
from harness.reporting.templates import compose

ASSETS = Path(__file__).parents[1] / "assets"
SUITE_FILTER_JS = (ASSETS / "suite-filter.js").read_text(encoding="utf-8")
EVIDENCE_DIALOG_JS = (ASSETS / "evidence-dialog.js").read_text(encoding="utf-8")


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _trace_text(value: Any) -> str:
    """Remove machine-identifying IPs and diff-breaking line-end whitespace."""
    text = str(value if value is not None else "")
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP 已脱敏]", text)
    return "\n".join(line.rstrip() for line in text.splitlines())


def _show(value: Any, divisor: float = 1.0, suffix: str = "", *, unit: str | None = None) -> str:
    """A metric at its unit's shared precision; ``suffix`` names a unit shown after the number."""
    if not isinstance(value, (int, float)):
        return "无数据"
    return unit_number(value / divisor, unit or suffix.strip()) + suffix


def _stat(metric: dict[str, Any], key: str, divisor: float = 1.0) -> str:
    value = metric.get(key, metric.get("median") if key == "p50" else None)
    return _show(value, divisor)


@dataclass(frozen=True)
class ComparedValue:
    text: str
    css_class: str


def _comparison_class(value: Any, other: Any, *, higher: bool = False) -> str:
    if not isinstance(value, (int, float)) or not isinstance(other, (int, float)):
        return ""
    if value == other:
        return ""
    return "better" if (value > other if higher else value < other) else "worse"


def _numeric_stat(metric: dict[str, Any], key: str) -> float | int | None:
    value = metric.get(key, metric.get("median") if key == "p50" else None)
    return value if isinstance(value, (int, float)) else None


def _display_number(
    value: float | int | None, divisor: float = 1.0, unit: str = ""
) -> float | None:
    """Return the exact number shown in an absolute metric cell at its unit's precision."""
    if not isinstance(value, (int, float)):
        return None
    return float(unit_number(value / divisor, unit).replace(",", ""))


def _difficulty(value: str) -> str:
    named = {"simple": "简单", "medium": "中等", "complex": "复杂"}
    if value in named:
        return named[value]
    match = re.fullmatch(r"step_(\d+)_(\d+)", value)
    if match:
        return f"{int(match.group(1))}–{int(match.group(2))} 步"
    return value or "未标注"


def _action_text(call: dict[str, Any]) -> str:
    action = str(call.get("action") or "")
    return {
        "navigate": f"打开 {call.get('url', '')} 并返回页面观察快照",
        "get_text": f"读取 {call.get('selector', '')} 文本",
        "get_attribute": f"读取 {call.get('selector', '')} 的 {call.get('name', '')} 属性",
        "get_property": f"读取 {call.get('selector', '')} 的 {call.get('name', '')} DOM 属性",
        "click": f"点击 {call.get('selector', '')}",
        "fill": f"向 {call.get('selector', '')} 填入 {call.get('text', '')}",
        "select": f"在 {call.get('selector', '')} 选择 {call.get('value', '')}",
        "snapshot": f"读取 {call.get('selector') or 'body'} 当前快照",
        "scroll": f"向 {call.get('direction', 'down')} 滚动 {call.get('amount', 700)} px",
        "back": "浏览器后退",
        "forward": "浏览器前进",
        "press": f"按键 {call.get('key', '')}",
    }.get(action, action)


def _source_references(source: dict[str, Any]) -> str:
    links = []
    for url in source.get("design_basis") or []:
        parsed = urlparse(str(url))
        if parsed.scheme in {"https", "http"}:
            label = parsed.path.rstrip("/").rsplit("/", 1)[-1] or parsed.hostname
            links.append(f'<a href="{_e(url)}" target="_blank" rel="noopener">{_e(label)}</a>')
    return '<p class="muted">设计依据：' + " · ".join(links) + "</p>" if links else ""


def _annotate_source_marks(escaped_text: str) -> str:
    """Annotate the literal heading permalink character without changing the oracle."""
    return escaped_text.replace(
        "¶", '<span class="source-mark" title="原网页标题锚点字符 U+00B6">¶</span>'
    )


def _source_mark_note(values: Any) -> str:
    return (
        '<p class="source-mark-note">¶ 为原网页标题的锚点字符，参与逐字判定。</p>'
        if "¶" in str(values)
        else ""
    )


def replay_table(
    headers: list[str], rows: list[list[Any]], caption: str, *, source_marks: bool = False,
    stackable: bool = False,
) -> str:
    """A captioned table; ``stackable`` cells stack on narrow screens under their header label."""
    head = "".join(f"<th>{_e(item)}</th>" for item in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td data-label="{_e(headers[i])}"'
            + (f' class="{v.css_class}"' if isinstance(v, ComparedValue) and v.css_class else "")
            + ">"
            + (
                _annotate_source_marks(_e(v.text if isinstance(v, ComparedValue) else v))
                if source_marks
                else _e(v.text if isinstance(v, ComparedValue) else v)
            )
            + "</td>"
            for i, v in enumerate(row)
        )
        + "</tr>"
        for row in rows
    )
    wrap = (
        f'<div class="table-wrap stackable" role="region" aria-label="{_e(caption)}" tabindex="0">'
        if stackable
        else '<div class="table-wrap">'
    )
    return (
        f"{wrap}<table><caption>{_e(caption)}</caption><thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


def flat_page(
    variant, key: str, fills: dict[str, str], *, title: str, heading: str, eyebrow: str,
    style: str, labels: dict[str, str], footer: str, brand: str, detail_nav: str = "",
) -> str:
    """One page of a flat (single-directory) replay report in the shared shell.

    ``labels`` translate the variant's page titles by key; a report page ends with a
    previous/next pager, while a detail page leads with its own ``detail_nav`` links
    above its heading.
    """
    pages = variant.pages
    owner = variant.page(key)
    pager = ""
    links = [(page.route, f"{n}. {labels[page.key]}") for n, page in enumerate(pages, 1)]
    header = shell.header(language="zh", brand=brand, home=pages[0].route, links=links,
                          current=None if detail_nav else owner.route)
    if not detail_nav:
        index = pages.index(owner)
        previous = (f'<a href="{pages[index - 1].route}">← {labels[pages[index - 1].key]}</a>'
                    if index else "<span></span>")
        following = (f'<a href="{pages[index + 1].route}">{labels[pages[index + 1].key]} →</a>'
                     if index + 1 < len(pages) else "<span></span>")
        pager = f'<div class="pager">{previous}{following}</div>'
    return shell.page(
        lang="zh-CN", base=None, title=title, heading=None, heading_html=heading,
        style=style + explorer.INVENTORY_STYLE, script=explorer.INVENTORY_SCRIPT, header=header,
        eyebrow=eyebrow, before_heading=detail_nav, notice="", role=owner.role,
        body=compose(owner.role, fills) + pager, dialog="",
        footer_html=f"<footer>{footer}</footer>",
    )


def write_chinese_only(root: Path) -> None:
    """Record that a flat report has only its Chinese edition; its pages say so in the footer.

    A flat report has no language switch, so it carries no English not-generated route.
    """
    contract = {"languages": ["zh"], "english_status": "not_generated"}
    write_json(root / "report-languages.json", contract)


def correctness_chart(summary: dict[str, Any], engines, t) -> str:
    """Result-correct rate per engine, then result against result-and-process correct."""
    functional = summary.get("functional") or {}
    bars, pairs = [], []
    for engine in engines:
        values = functional.get(engine) or {}
        tasks = int(values.get("tasks", 0) or 0)
        result = int(values.get("result_final_passes", 0) or 0)
        combined = int(values.get("combined_final_passes", 0) or 0)
        label = html.escape(str(engine))
        bars.append(charts.Bar.rate(label, result, tasks))
        pairs.append(charts.Pair(label, result / tasks if tasks else None,
                                 combined / tasks if tasks else None))
    ranked = sorted(bars, key=lambda bar: -(bar.value if bar.value is not None else -1))
    order = [bar.label for bar in ranked]
    return charts.charts(
        charts.ranked_bars(
            bars, title=t("chart.result_correct", "Result correct"),
            caption=t("chart.result_correct_caption",
                      "Ordered by rate; n/N is tasks with a correct result / tasks.")),
        charts.dumbbell(
            sorted(pairs, key=lambda pair: order.index(pair.label)),
            title=t("chart.result_and_process",
                    "Correct result versus correct result and process"),
            caption=t("chart.result_and_process_caption",
                      "The gap is tasks whose answer was right but whose reference process "
                      "did not complete."),
            first_label=t("chart.result_only", "Result correct"),
            second_label=t("chart.both", "Result and process correct")))


def explorer_labels(
    t, title: str, description: str, task: str, baseline: str, compare: str
) -> explorer.Labels:
    """Same-task explorer labels in one edition; ``t`` is that edition's catalog lookup."""
    return explorer.Labels(
        title=title, description=description, task=task, baseline=baseline, compare=compare,
        relation=t("comparison.relation", "Outcome relation"),
        any=t("comparison.relation.all_tasks", "All tasks"),
        relations={
            "differs": t("explorer.relation.differs", "Outcomes differ"),
            "baseline_passes": t(
                "explorer.relation.baseline_passes", "Baseline passes; a comparison fails"
            ),
            "comparison_passes": t(
                "explorer.relation.comparison_passes", "Baseline fails; a comparison passes"
            ),
            "all_pass": t("explorer.relation.all_pass", "All selected pass"),
            "all_fail": t("explorer.relation.all_fail", "All selected fail"),
        },
    )
