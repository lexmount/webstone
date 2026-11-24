"""Shared report page shell and components: navigation, theme, language switch, tables, evidence links.

Tracks pass their own catalog-translated labels, style and script; the shell
fixes the page skeleton every report route uses. The page body comes from
``templates.compose`` so each route carries its template role and slots.
"""

from harness.reporting.presentation import esc


def table(headers, rows, sortable=True, *, bounded=False, primary_column=None, significance=False,
          query_count=None, hidden_columns=()):
    """A scrollable table; sortable headers carry the accessible controls the presentation check requires."""
    head = "".join(
        '<th' + (' data-extra-column hidden' if i in hidden_columns else '')
        + f' aria-sort="{"descending" if i == primary_column else "none"}"'
        + (' data-primary-metric' if i == primary_column else '')
        + f'><button type="button" aria-label="{esc(h)}" data-sort-column="{i}">{esc(h)}</button></th>'
        if sortable
        else f"<th>{esc(h)}</th>"
        for i, h in enumerate(headers)
    )
    return (
        ('<div class="scroll inventory" tabindex="0"><table' if bounded else '<div class="scroll" tabindex="0"><table')
        + (' data-sort-controls="buttons"' if sortable else '')
        + (f' data-significance="paired-query-bootstrap/1" data-query-count="{query_count}"' if significance else '')
        + (' class="compact"' if len(headers) == 2 else "")
        + "><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def cells(values):
    """One table row of already-rendered cell HTML."""
    return "<tr>" + "".join("<td>" + v + "</td>" for v in values) + "</tr>"


def evidence_link(path, label, *, result=None):
    """Open saved evidence in the page dialog, with the raw file as a fallback link."""
    if result is not None:
        return f'<button data-evidence="{path}" data-result="{result}">{label}</button>'
    return f'<button data-evidence="{path}">{label}</button> <a href="{path}">JSON</a>'


def evidence_dialog(label, close, full_result):
    """The one in-page evidence viewer that ``evidence_link`` buttons open."""
    return (f'<dialog aria-label="{label}"><button data-close>{close}</button><pre></pre>'
            f'<a hidden>{full_result}</a></dialog>')


def navigation(links, current, *, back=""):
    """Top navigation: ``links`` are (route, label) pairs; ``current`` marks the open route."""
    return "".join(f'<a href="{back}{route}"' + (' aria-current="page"' if route == current else '')
                   + f'>{label}</a>' for route, label in links)


def footer(links):
    """Secondary page links, as (href, label, attributes) triples."""
    if not links:
        return ""
    return "<footer>" + "".join(
        f'<a href="{href}"' + "".join(f' {key}="{esc(value)}"' if value != "" else f" {key}"
                                       for key, value in attributes.items())
        + f'>{label}</a>' for href, label, attributes in links) + "</footer>"


def page(*, lang, base, title, heading, style, script, nav, eyebrow, notice, role, body, dialog, footer_html=""):
    """One complete report page; ``nav`` already holds navigation, language and theme controls."""
    return (f'<!doctype html><html lang="{lang}"><head><base href="{base}"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title><style>{style}</style></head><body><nav>{nav}</nav>'
            f'<main data-page-role="{role}"><p class="muted">{eyebrow}</p><h1>{esc(heading)}</h1>'
            f'{notice}{body}{footer_html}</main>{dialog}<script>{script}</script></body></html>')


def numeric_winners(rows, directions):
    """Return unique extrema from available raw values; ties remain neutral."""
    winners = {}
    for column, direction in directions.items():
        available = [row[column] for row in rows if row[column] is not None]
        if direction == "neutral" or not available:
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
