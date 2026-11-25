"""Shared report page shell and components: header, theme, language switch, tables, evidence links.

Tracks pass their own catalog-translated labels, style and script; the shell
fixes the page skeleton every report route uses. The page body comes from
``templates.compose`` so each route carries its template role and slots.
"""

from pathlib import Path

from harness.reporting.locales import catalog_section, catalog_text
from harness.reporting.presentation import esc

_HERE = Path(__file__).parent
CSS = (_HERE / "shell.css").read_text(encoding="utf-8")
"""Design tokens (light and dark), header, table-overflow cue and empty-state styles for every track."""
SCRIPT = (_HERE / "shell.js").read_text(encoding="utf-8")
"""Theme bootstrap (stored choice, else system preference), theme toggle and table-overflow cue."""
HEAD = f"<style>{CSS}</style><script>{SCRIPT}</script>"
"""Shell assets for a page that does not link a stylesheet already containing ``CSS``."""
HEAD_SCRIPT = f"<script>{SCRIPT}</script>"

_MESSAGES = _HERE / "messages.json"


def _words(language):
    """The shell's labels in one edition; each language switch names its language in that language."""
    t = catalog_text(_MESSAGES, _edition(language))
    return {"more": t("shell.more", "More"), "navigation": t("shell.navigation", "Report pages"),
            "table_hint": t("shell.table_hint", "Swipe sideways to see the full table"),
            "theme": t("shell.theme", "Dark theme"),
            **catalog_section(_MESSAGES, "endonyms")}


PRIMARY_LINKS = 6
"""Header items shown inline; later items move under "More"."""


def _edition(language):
    return "en" if str(language).lower().startswith("en") else "zh"


def header(*, language, brand, home, links, current=None, back="", switch=None, primary=PRIMARY_LINKS):
    """The one report header: brand, up to ``primary`` page links plus "More", language and theme.

    ``links`` are (route, label) pairs, or (route, label, primary) triples when a track
    decides which pages stay inline; labels are already-rendered HTML. ``current`` marks the
    open route; ``back`` prefixes every route. ``switch`` is the (href, language) of the
    other edition, or None for a single-language report.
    """
    words = _words(language)
    entries = [(route, label, True if len(rest) == 0 else rest[0]) for route, label, *rest in links]
    flagged = any(len(entry) == 3 for entry in links)
    ordered = [e for e in entries if e[2]] + [e for e in entries if not e[2]] if flagged else entries
    # Declared secondary pages always go under "More"; so do primary pages past the limit.
    inline_count = min(primary, sum(e[2] for e in entries)) if flagged else primary
    inline, more = ordered[:inline_count], ordered[inline_count:]

    def anchor(route, label, _):
        return f'<a href="{back}{route}"' + (' aria-current="page"' if route == current else '') + f'>{label}</a>'

    html = (f'<header class="shell-top"><span data-table-hint hidden>{words["table_hint"]}</span>'
            f'<nav class="shell-nav" aria-label="{esc(words["navigation"])}">'
            f'<a class="shell-brand" href="{home}">{brand}</a>'
            '<div class="shell-links">' + "".join(anchor(*e) for e in inline) + '</div>')
    if more:
        marker = ' data-current' if any(route == current for route, _, _ in more) else ''
        html += (f'<details class="shell-more"{marker}><summary>{words["more"]}</summary><div>'
                 + "".join(anchor(*e) for e in more) + '</div></details>')
    html += '<div class="shell-controls">'
    if switch is not None:
        href, target = switch
        target = _edition(target)
        code = "zh-CN" if target == "zh" else "en"
        html += f'<a href="{href}" hreflang="{code}" lang="{code}">{words[target]}</a>'
    html += (f'<button type="button" class="shell-theme" data-shell-theme aria-pressed="false" aria-label="{esc(words["theme"])}"'
             f' title="{esc(words["theme"])}">◐</button></div></nav></header>')
    return html


def empty_state(message):
    """The one placeholder for a section or table with nothing to show."""
    return f'<p class="shell-empty" data-empty-state>{message}</p>'


def table(headers, rows, sortable=True, *, bounded=False, primary_column=None, significance=False,
          query_count=None, hidden_columns=(), table_id="", caption="", classes="", header_html=False,
          empty=None, action_columns=()):
    """A scrollable table; sortable headers carry the accessible controls the presentation check requires.

    ``header_html`` marks headers the caller already rendered; plain headers are escaped.
    ``empty`` replaces a table without rows by that message; ``action_columns`` hold
    links or buttons, so they get no sort control.
    """
    rows = list(rows)
    if not rows and empty is not None:
        return empty_state(empty)
    label = (lambda h: h) if header_html else esc
    head = "".join(
        '<th' + (' data-extra-column hidden' if i in hidden_columns else '')
        + (' data-unsortable' if i in action_columns else
           f' aria-sort="{"descending" if i == primary_column else "none"}"')
        + (' data-primary-metric' if i == primary_column else '')
        + ('>' + label(h) + '</th>' if i in action_columns else
           f'><button type="button" aria-label="{esc(h)}" data-sort-column="{i}">{label(h)}</button></th>')
        if sortable
        else f"<th>{label(h)}</th>"
        for i, h in enumerate(headers)
    )
    names = " ".join(filter(None, ("compact" if len(headers) == 2 else "", classes)))
    return (
        ('<div class="scroll inventory" tabindex="0"><table' if bounded else '<div class="scroll" tabindex="0"><table')
        + (f' id="{esc(table_id)}"' if table_id else '')
        + (' data-sort-controls="buttons"' if sortable else '')
        + (f' data-significance="paired-query-bootstrap/1" data-query-count="{query_count}"' if significance else '')
        + (f' class="{esc(names)}"' if names else "")
        + ">"
        + (f"<caption>{esc(caption)}</caption>" if caption else "")
        + "<thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def cells(values):
    """One table row of already-rendered cell HTML."""
    return "<tr>" + "".join("<td>" + v + "</td>" for v in values) + "</tr>"


def evidence_link(path, label, *, result=None, viewer=None, raw_label="JSON"):
    """Open saved evidence in the page dialog, or in a shared ``viewer`` page, with the raw file as a fallback link."""
    if viewer is not None:
        return f'<a href="{viewer}?src={path}">{label}</a><a href="{path}" download>{raw_label}</a>'
    if result is not None:
        return f'<button data-evidence="{path}" data-result="{result}">{label}</button>'
    return f'<button data-evidence="{path}">{label}</button> <a href="{path}">{raw_label}</a>'


def evidence_dialog(label, close, full_result):
    """The one in-page evidence viewer that ``evidence_link`` buttons open."""
    return (f'<dialog aria-label="{label}"><button data-close>{close}</button><pre></pre>'
            f'<a hidden>{full_result}</a></dialog>')


def footer(links):
    """Secondary page links, as (href, label, attributes) triples."""
    if not links:
        return ""
    return "<footer>" + "".join(
        f'<a href="{href}"' + "".join(f' {key}="{esc(value)}"' if value != "" else f" {key}"
                                       for key, value in attributes.items())
        + f'>{label}</a>' for href, label, attributes in links) + "</footer>"


def page(*, lang, base, title, heading, style, script, header, eyebrow, notice, role, body, dialog, footer_html="",
         head="", heading_html=None, shell_css=True, before_heading=""):
    """One complete report page under the shared ``header``.

    ``head`` adds linked assets or metadata; an empty ``style`` or ``script`` is left out, so a
    track may link shared files instead. ``heading_html`` replaces the escaped ``heading``. A
    ``base`` of None leaves the base URL to the host, for reports whose hosted preview sets it.
    ``shell_css`` False leaves the shell styles to a linked stylesheet that already holds ``CSS``.
    ``before_heading`` places block content, such as a detail page's way back, above the heading.
    """
    base_tag = "" if base is None else f'<base href="{base}">'
    eyebrow_html = f'<p class="muted">{eyebrow}</p>' if eyebrow else ''
    return (f'<!doctype html><html lang="{lang}"><head>{base_tag}<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title>' + (HEAD if shell_css else HEAD_SCRIPT) + head
            + (f'<style>{style}</style>' if style else '')
            + f'</head><body>{header}'
            f'<main data-page-role="{role}">{before_heading}{eyebrow_html}'
            f'<h1>{esc(heading) if heading_html is None else heading_html}</h1>'
            f'{notice}{body}{footer_html}</main>{dialog}'
            + (f'<script>{script}</script>' if script else '') + '</body></html>')


def numeric_winners(rows, directions):
    """Return unique extrema from available raw values; ties remain neutral."""
    winners = {}
    for column, direction in directions.items():
        available = [row[column] for row in rows if row[column] is not None]
        # A winner needs a contest: at least two available values.
        if direction == "neutral" or len(available) < 2:
            continue
        best = (min if direction == "min" else max)(available)
        if available.count(best) == 1:
            winners[column] = best
    return winners


def numeric_cell(label, value, metric, direction, winner=False, *, significant=None):
    attrs = f' data-metric="{esc(metric)}" data-metric-direction="{direction}"'
    if value is not None:
        attrs += f' data-sort="{value}" data-value="{value}"'
    else:
        attrs += ' data-value=""'
    if significant is not None:
        attrs += ' data-significant="' + str(bool(significant)).lower() + '"'
    if winner:
        attrs += ' class="win"'
        if significant is None:
            label = '<span class="comparison-winner">★ ' + label + '</span>'
        else:
            marker = '<span class="comparison-winner">★ </span>' if significant else ''
            label = '<span class="comparison-extremum">' + marker + label + '</span>'
    return '<td' + attrs + '>' + label + '</td>'
