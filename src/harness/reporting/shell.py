"""Shared report page shell and components: navigation, theme, language switch, tables, evidence links.

Tracks pass their own catalog-translated labels, style and script; the shell
fixes the page skeleton every report route uses. The page body comes from
``templates.compose`` so each route carries its template role and slots.
"""

from harness.reporting.presentation import esc


def table(headers, rows, sortable=True, *, bounded=False, primary_column=None, significance=False,
          query_count=None, hidden_columns=(), table_id="", caption="", classes="", header_html=False):
    """A scrollable table; sortable headers carry the accessible controls the presentation check requires.

    ``header_html`` marks headers the caller already rendered; plain headers are escaped.
    """
    label = (lambda h: h) if header_html else esc
    head = "".join(
        '<th' + (' data-extra-column hidden' if i in hidden_columns else '')
        + f' aria-sort="{"descending" if i == primary_column else "none"}"'
        + (' data-primary-metric' if i == primary_column else '')
        + f'><button type="button" aria-label="{esc(h)}" data-sort-column="{i}">{label(h)}</button></th>'
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


def page(*, lang, base, title, heading, style, script, nav, eyebrow, notice, role, body, dialog, footer_html="",
         head="", heading_html=None):
    """One complete report page; ``nav`` already holds navigation, language and theme controls.

    ``head`` adds linked assets or metadata; an empty ``style`` or ``script`` is left out, so a
    track may link shared files instead. ``heading_html`` replaces the escaped ``heading``. A
    ``base`` of None leaves the base URL to the host, for reports whose hosted preview sets it.
    """
    base_tag = "" if base is None else f'<base href="{base}">'
    return (f'<!doctype html><html lang="{lang}"><head>{base_tag}<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)}</title>{head}' + (f'<style>{style}</style>' if style else '')
            + f'</head><body><nav>{nav}</nav>'
            f'<main data-page-role="{role}"><p class="muted">{eyebrow}</p>'
            f'<h1>{esc(heading) if heading_html is None else heading_html}</h1>'
            f'{notice}{body}{footer_html}</main>{dialog}'
            + (f'<script>{script}</script>' if script else '') + '</body></html>')


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
