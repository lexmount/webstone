"""Track-independent HTML escaping and semantic checks for comparisons and evidence UI."""
import html as html_text
import math
import re
from collections import defaultdict
from html.parser import HTMLParser

from bs4 import BeautifulSoup, NavigableString


def esc(value, *, none=""):
    """Escape one value for HTML text or a quoted attribute, rendering ``None`` as ``none``."""
    return html_text.escape(str(none if value is None else value), quote=True)


# One display precision per unit, so the same unit reads the same in every table and track.
UNIT_DIGITS = {"ms": 0, "s": 2, "秒": 2, "%": 1, "×": 2}


def unit_number(value, unit, *, missing="—"):
    """The number for ``unit`` at that unit's precision, with thousands separators; no unit text."""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        return missing
    return f"{value:,.{UNIT_DIGITS.get(unit, 2)}f}"


def with_unit(value, unit, *, missing="—"):
    """``unit_number`` followed by its unit; a missing value stays bare."""
    number = unit_number(value, unit, missing=missing)
    if number == missing:
        return number
    return number + unit if unit in ("%", "×") else f"{number} {unit}"


def milliseconds(value, *, missing="—"):
    return with_unit(value, "ms", missing=missing)


def percent(ratio, *, missing="—"):
    """A 0–1 ratio as a percentage at the shared precision."""
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        return missing
    return with_unit(ratio * 100, "%", missing=missing)


# Counted nouns that reports print after a number; "1 tasks" or a Chinese page's "3 calls" is a label bug.
COUNTED_NOUNS = ("calls", "tasks", "methods", "queries", "failures", "steps", "results", "cases", "pages",
                 "configurations", "browsers", "rankings", "family rankings", "task families", "characters",
                 "attempts", "records", "rounds", "errors", "providers", "outputs")
_SINGULAR_PLURAL = re.compile(r"(?<![\d.,/])\b1 (" + "|".join(COUNTED_NOUNS) + r")\b")
# Singular forms too: a Chinese page should carry neither "1 task" nor "3 tasks".
_SINGULAR = {"queries": "query", "task families": "task family", "family rankings": "family ranking"}
_COUNT_NOUNS = sorted({*COUNTED_NOUNS, *(_SINGULAR.get(n, n[:-1]) for n in COUNTED_NOUNS)}, key=len, reverse=True)
_ENGLISH_COUNT = re.compile(r"(?<![\d.,])\b\d[\d,]* (" + "|".join(_COUNT_NOUNS) + r")\b")
_ESCAPED_MARKUP = re.compile(r"</?(?:span|a|strong|b|div|p|br)\b[^<>]*>")
_MISSING_SPACE = re.compile(r"\b[a-z]{2,}:[A-Z]")


_VERBATIM = ('script', 'style', 'pre', 'code', 'template', 'textarea', 'head', 'blockquote', 'q')
_CHROME = 'header.shell-top, h1, h2, h3, th, summary, button, label, caption, legend, dt'


def check_shell(soup):
    """Rules for a page under the shared header: header shape, accessible names, no empty
    headings and no escaped markup shown as text."""
    headers = soup.select('header.shell-top')
    if len(headers) != 1:
        raise ValueError('Report page needs exactly one shared header')
    header = headers[0]
    if (not header.select_one('nav[aria-label]') or len(header.select('.shell-links > a')) > 6
            or not header.select_one('button[data-shell-theme][aria-label][aria-pressed]')):
        raise ValueError('Shared header needs labelled navigation, at most six inline pages and a named theme control')
    for button in soup.select('button'):
        named_by = [soup.find(id=ref) for ref in button.get('aria-labelledby', '').split()]
        if not (button.get_text(strip=True) or button.get('aria-label', '').strip()
                or button.get('title', '').strip()
                or any(node is not None and node.get_text(strip=True) for node in named_by)):
            raise ValueError('Button has no accessible name')
    for node in soup.select('h1, h2, h3, summary'):
        # A dialog fills its heading when it opens.
        if (not node.get_text(strip=True) and not node.select_one('img[alt]')
                and node.find_parent(['dialog', 'template']) is None):
            raise ValueError(f'Empty {node.name} element')
    # Report-authored chrome only: quoted source text may legitimately contain escaped markup.
    for node in soup.select(_CHROME):
        if match := _ESCAPED_MARKUP.search(node.get_text()):
            raise ValueError(f'Escaped markup shown as text: {match.group(0)!r}')


def check_labels(soup):
    """Reader-label rules: counted nouns agree with one, Chinese pages carry no English counts,
    and a label never runs into its value. Each text node is checked on its own."""
    soup = BeautifulSoup(soup, 'html.parser') if isinstance(soup, str) else soup
    language = (soup.html.get('lang', '') if soup.html else '').lower()
    rules = [(_SINGULAR_PLURAL, 'Plural noun after one'), (_MISSING_SPACE, 'Label and value run together')]
    if language.startswith('zh'):
        rules.append((_ENGLISH_COUNT, 'Untranslated English count in a Chinese page'))
    page_language = language.split('-')[0]
    for string in soup.find_all(string=True):
        if type(string) is not NavigableString:
            continue  # comments, doctypes and other nodes a reader never sees
        # Evidence and quoted source text keep their own wording: verbatim elements,
        # anything marked data-verbatim, and text declared in another language.
        if string.find_parent(_VERBATIM) is not None or string.find_parent(attrs={'data-verbatim': True}):
            continue
        declared = string.find_parent(attrs={'lang': True})
        if declared is not None and declared.name != 'html' and declared['lang'].lower().split('-')[0] != page_language:
            continue
        text = str(string)
        for pattern, message in rules:
            if match := pattern.search(text):
                raise ValueError(f'{message}: {match.group(0)!r}')


def check_presentation(html):
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    if soup.select_one('header.shell-top'):
        check_shell(soup)
    for table in soup.select('table'):
        if table.get('data-significance') == 'paired-query-bootstrap/1':
            count = table.get('data-query-count', '')
            if not count.isdecimal():
                raise ValueError('Statistical comparison requires its query population')
            if int(count) < 30 and table.select_one('.comparison-winner'):
                raise ValueError('Small query groups cannot carry significance stars')
        if table.get('data-sort-controls') == 'buttons':
            headers = table.select('thead th')
            for index, header in enumerate(headers):
                if header.has_attr('data-unsortable'):
                    if header.select_one('[data-sort-column]'):
                        raise ValueError('Action column cannot carry a sort control')
                    continue
                control = header.select_one('button[type="button"][data-sort-column]')
                if (control is None or control.get('data-sort-column') != str(index)
                        or not control.get_text(strip=True) or header.get('aria-sort') not in
                        ('none', 'ascending', 'descending')):
                    raise ValueError('Sortable comparison needs an accessible control for every column')
        groups = defaultdict(list)
        for cell in table.select('td[data-metric-direction]'):
            direction = cell['data-metric-direction']
            if direction not in ('min', 'max', 'neutral') or not cell.get('data-metric'):
                raise ValueError('Numeric comparison lacks metric identity or direction')
            if direction == 'neutral':
                if cell.select_one('.comparison-winner, .comparison-extremum'):
                    raise ValueError('Out-of-cohort value cannot be highlighted')
                continue
            value = cell.get('data-sort')
            if value is None:
                if cell.select_one('.comparison-winner, .comparison-extremum'):
                    raise ValueError('Unavailable value cannot be highlighted')
                continue
            number = float(value)
            if not math.isfinite(number):
                raise ValueError('Comparison contains a non-finite value')
            groups[(cell['data-metric'], direction)].append((number, cell))
        for (metric, direction), values in groups.items():
            extreme = (min if direction == 'min' else max)(v for v, _ in values)
            unique = sum(v == extreme for v, _ in values) == 1
            # A winner needs a contest: one available value is never highlighted.
            contested = len(values) >= 2
            for value, cell in values:
                statistical = table.get('data-significance') == 'paired-query-bootstrap/1'
                marked = cell.select_one('.comparison-extremum' if statistical else '.comparison-winner') is not None
                if statistical:
                    significant = cell.get('data-significant') == 'true'
                    star = cell.select_one('.comparison-winner') is not None
                    if star != significant or significant and not marked:
                        raise ValueError('Significance marker differs from its paired comparison')
                if marked != (
                    contested and (unique or table.get("data-highlight-ties") == "all") and value == extreme
                ):
                    raise ValueError(f'Column extremum highlight missing, incorrect, or tied: {metric}'
                                     + ('' if contested else ' (a single available value is never highlighted)'))
    # Shared charts and inventories carry their own checks; imported here to avoid a cycle.
    from harness.reporting.charts import check_charts
    from harness.reporting.explorer import check_inventory
    check_charts(soup)
    check_inventory(soup)
    for inventory in soup.select('[data-dataset-inventory]'):
        if not soup.select_one('form[data-dataset-controls] input[type="search"]') or not inventory.has_attr('data-sortable'):
            raise ValueError('Dataset inventory needs independent search and sorting controls')
    for detail in soup.select('[data-report-detail]'):
        if len(detail.select('[data-result-summary]')) != 1:
            raise ValueError('Result detail requires exactly one summary')
        if any(
            pre.find_parent('dialog') is None and pre.find_parent('details') is None
            for pre in detail.select('pre')
        ):
            raise ValueError('Raw evidence must not be flattened into the result summary')
        ids = [node['id'] for node in detail.select('[id]')]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate evidence target')
        for button in detail.select('[data-call-log-open]'):
            dialog = detail.find('dialog', id=button['data-call-log-open'])
            if (dialog is None or not dialog.select_one('[data-call-log-close]')
                    or not dialog.get('aria-labelledby')):
                raise ValueError('Evidence trigger lacks an accessible closable dialog')
        if detail['data-report-detail'] == 'failed':
            error_target = (
                '#result-logs pre[data-kind="error"], #result-logs [data-log-unavailable], '
                '[data-result-logs] pre[data-kind="error"], '
                '[data-result-logs] [data-log-unavailable]'
            )
            if (detail.select_one('[data-failure-diagnosis]') is None
                    or detail.select_one(error_target) is None):
                raise ValueError('Failed result lacks diagnosis or exact error evidence')
    return soup


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "pre", "code"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "pre", "code"} and self.hidden_depth:
            self.hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def visible_text(html: str) -> str:
    """Extract authored prose, excluding scripts, styles and verbatim evidence blocks."""
    parser = _VisibleTextParser()
    parser.feed(html)
    return " ".join(parser.parts)


def metric_guide(definitions, *, title, headings):
    """One reusable reader guide, driven by the same metric identities as comparisons."""
    required = ("id", "label", "meaning", "unit", "population", "direction")
    if not definitions or len({d.get("id") for d in definitions}) != len(definitions):
        raise ValueError("Metric definitions must be nonempty and unique")
    rows = []
    for definition in definitions:
        if any(
            not isinstance(definition.get(k), str) or not definition[k].strip() for k in required
        ):
            raise ValueError("Every metric needs its meaning, unit, population and direction")
        rows.append(
            '<tr id="metric-'
            + esc(definition["id"])
            + '">'
            + "".join(
                "<td>" + esc(definition[k]) + "</td>"
                for k in ("label", "meaning", "unit", "population", "direction")
            )
            + "</tr>"
        )
    return (
        "<details data-metric-guide><summary>"
        + esc(title)
        + '</summary><div class="table-wrap"><table><thead><tr>'
        + "".join("<th>" + esc(h) + "</th>" for h in headings)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div></details>"
    )


def check_metric_guide(document, definitions, *, title, headings):
    soup = BeautifulSoup(document, "html.parser") if isinstance(document, str) else document
    actual = soup.select("[data-metric-guide]")
    expected = BeautifulSoup(
        metric_guide(definitions, title=title, headings=headings), "html.parser"
    )
    if len(actual) != 1 or actual[0].get_text(" ", strip=True) != expected.get_text(
        " ", strip=True
    ):
        raise ValueError("Metric guide is missing or differs from the metric definitions")

    def cells(guide):
        return [
            [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"], recursive=False)]
            for row in guide.select("thead tr, tbody tr")
        ]

    if cells(actual[0]) != cells(expected):
        raise ValueError("Metric guide columns differ from the metric definitions")
    if [r.get("id") for r in actual[0].select("tbody tr")] != [
        "metric-" + d["id"] for d in definitions
    ]:
        raise ValueError("Metric explanation targets differ from their definitions")
