"""The same-task explorer: one row per task, one column per registered participant.

A track passes its participants in registry order, a baseline and each task's
outcomes; the explorer adds no participant of its own. Readers pick the
baseline, any set of comparison participants and an explicit outcome relation
between them. Every outcome links to the participant's full result when one
exists.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from harness.reporting.presentation import esc

OUTCOMES = frozenset({"pass", "fail", "missing"})
# Relations between the baseline and the selected comparisons, in menu order. "differs" is
# the default because a comparison reads from disagreements first.
RELATIONS = ("differs", "baseline_passes", "comparison_passes", "all_pass", "all_fail")


@dataclass(frozen=True)
class Participant:
    """One column. ``label`` and ``detail`` are already-rendered HTML."""

    key: str
    label: str
    detail: str = ""


@dataclass(frozen=True)
class Outcome:
    state: str  # pass, fail or missing
    label: str  # rendered HTML
    href: str | None = None  # the participant's full result for this task

    def __post_init__(self):
        if self.state not in OUTCOMES:
            raise ValueError(f"Unknown same-task outcome: {self.state}")


@dataclass(frozen=True)
class Task:
    """One row: its rendered cell, filter values and each participant's outcome."""

    cell: str
    outcomes: Mapping[str, Outcome]
    filters: Mapping[str, str] = field(default_factory=dict)
    search: str = ""


@dataclass(frozen=True)
class Filter:
    """A row filter: ``options`` are (value, rendered label) pairs; an empty value shows all."""

    key: str
    label: str
    options: Sequence[tuple[str, str]]


@dataclass(frozen=True)
class Labels:
    """Catalog-translated interface text; ``relations`` names every entry of ``RELATIONS``."""

    title: str
    description: str
    task: str
    baseline: str
    compare: str
    relation: str
    any: str
    relations: Mapping[str, str]


def same_task_explorer(element_id, participants, tasks, *, baseline, labels, filters=()):
    """Render the explorer; at least two participants are required for a comparison."""
    keys = [participant.key for participant in participants]
    if len(keys) != len(set(keys)) or baseline not in keys:
        raise ValueError("Same-task explorer needs unique participants that include the baseline")
    if set(labels.relations) != set(RELATIONS):
        raise ValueError("Same-task explorer labels must name every outcome relation")
    rows = []
    for task in tasks:
        cells = []
        for key in keys:
            outcome = task.outcomes.get(key)
            state = outcome.state if outcome else "missing"
            pill = (f'<a class="outcome-pill {state}" href="{esc(outcome.href)}">{outcome.label}</a>'
                    if outcome and outcome.href else
                    f'<span class="outcome-pill {state}">{outcome.label if outcome else ""}</span>')
            cells.append(f'<td data-participant="{esc(key)}" data-outcome="{state}">{pill}</td>')
        attributes = "".join(f' data-{esc(name)}="{esc(value)}"' for name, value in task.filters.items())
        rows.append(f'<tr data-explorer-row{attributes} data-search="{esc(task.search)}">'
                    f'<td>{task.cell}</td>{"".join(cells)}</tr>')
    headers = "".join(
        f'<th data-participant="{esc(p.key)}">{p.label}'
        + (f'<br><span class="table-kicker">{p.detail}</span>' if p.detail else "") + "</th>"
        for p in participants)
    choice = "".join(
        f'<option value="{esc(p.key)}"' + (" selected" if p.key == baseline else "") + f">{_plain(p)}</option>"
        for p in participants)
    compared = "".join(
        f'<label><input type="checkbox" value="{esc(p.key)}" checked> {_plain(p)}</label>' for p in participants)
    relations = (f'<option value="">{labels.any}</option>' + "".join(
        f'<option value="{name}"' + (" selected" if name == RELATIONS[0] else "") + f">{labels.relations[name]}</option>"
        for name in RELATIONS))
    selects = "".join(
        f'<label>{item.label} <select data-explorer-filter="{esc(item.key)}"><option value="">{labels.any}</option>'
        + "".join(f'<option value="{esc(value)}">{text}</option>' for value, text in item.options)
        + "</select></label>" for item in filters)
    return (
        f'<article class="browser-comparison same-task-explorer" id="{esc(element_id)}" data-explorer>'
        f'<header class="comparison-heading"><div><h3>{labels.title}</h3><p>{labels.description}</p></div>'
        '<b class="comparison-count" data-explorer-count></b></header>'
        f'<div class="filters matrix-filters"><label>{labels.baseline} <select data-explorer-baseline>{choice}</select></label>'
        f'<fieldset data-explorer-compare><legend>{labels.compare}</legend>{compared}</fieldset>'
        f'<label>{labels.relation} <select data-explorer-relation>{relations}</select></label>{selects}</div>'
        '<div class="comparison-result"><div class="scroll" tabindex="0"><table class="comparison-table configuration-matrix">'
        f'<thead><tr><th>{labels.task}</th>{headers}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></div></article>'
    )


def _plain(participant):
    return participant.label + (" · " + participant.detail if participant.detail else "")


# Shows the baseline and the selected comparisons, then keeps rows whose outcomes hold the
# selected relation. Missing outcomes take part in no relation. A page may load the script
# after DOMContentLoaded (for example from an inserted script element), so it initializes
# at once when the document is already parsed.
SCRIPT = """
function filterExplorer(root){const baseline=root.querySelector('[data-explorer-baseline]')?.value,boxes=[...root.querySelectorAll('[data-explorer-compare] input')],compared=boxes.filter(b=>b.checked&&b.value!==baseline).map(b=>b.value),relation=root.querySelector('[data-explorer-relation]')?.value||'',filters=[...root.querySelectorAll('[data-explorer-filter]')];boxes.forEach(b=>{b.disabled=b.value===baseline});root.querySelectorAll('[data-participant]').forEach(c=>{const k=c.dataset.participant;c.hidden=k!==baseline&&!compared.includes(k);c.toggleAttribute('data-baseline',k===baseline)});let visible=0,total=0;root.querySelectorAll('tbody tr[data-explorer-row]').forEach(r=>{total++;const state=k=>r.querySelector('[data-participant="'+k+'"]')?.dataset.outcome||'missing',base=state(baseline),others=compared.map(state).filter(s=>s!=='missing'),states=(base==='missing'?[]:[base]).concat(others),holds={differs:new Set(states).size>1,baseline_passes:base==='pass'&&others.includes('fail'),comparison_passes:base==='fail'&&others.includes('pass'),all_pass:states.length>0&&states.every(s=>s==='pass'),all_fail:states.length>0&&states.every(s=>s==='fail')},hidden=Boolean(relation&&!holds[relation])||filters.some(c=>c.value&&r.dataset[c.dataset.explorerFilter]!==c.value);r.hidden=hidden;if(!hidden)visible++});const count=root.querySelector('[data-explorer-count]');if(count)count.textContent=visible+' / '+total}
function initExplorers(){document.querySelectorAll('[data-explorer]').forEach(root=>{root.querySelectorAll('select,input').forEach(c=>c.addEventListener('change',()=>filterExplorer(root)));filterExplorer(root)})}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initExplorers);else initExplorers();
"""


# --- Result inventories and detail navigation -------------------------------------------------
#
# A track keeps its own filter controls and filter script; marking the inventory with
# ``inventory_attributes`` adds the shared reader path on top. Rows are ``[data-inventory-row]``
# (or the table body rows) and a track filter hides a row with the ``hidden`` attribute.
# - The result control opens on failures; when the first view is empty it switches to the
#   fallback value once and shows the notice, so a reader never lands on an empty table.
# - Only ``page_size`` matching rows are shown at a time, with a button for the next page.
# - Filter values live in the page URL (``?f.<control>=value``), so a link back restores them.
# - Opening a row's detail link remembers the visible order; the detail page's
#   ``detail_navigation`` then offers back (with filters), previous and next within it.
# - ``diagnosis_button`` rows open one shared dialog with the stage, exact error and links.

PAGE_SIZE = 100


def inventory_attributes(*, result_control, fallback="", failure="fail", page_size=PAGE_SIZE):
    """Attributes for the element that holds an inventory's controls, rows and notice.

    ``failure`` is the result value the inventory must open on; ``fallback`` shows everything.
    """
    return (f' data-inventory data-inventory-result="{esc(result_control)}"'
            f' data-inventory-failure="{esc(failure)}" data-inventory-fallback="{esc(fallback)}"'
            f' data-inventory-page-size="{int(page_size)}"')


def inventory_notice(text, more):
    """The fallback notice and the next-page button; both stay hidden until needed."""
    return (f'<p class="inventory-notice" data-inventory-notice role="status" hidden>{text}</p>'
            f'<button type="button" class="inventory-more" data-inventory-more hidden>{more}</button>')


def diagnosis_button(label, *, title, stage, error, links=(), stage_label, error_label):
    """A row's diagnosis: the button and, beside it, the dialog content in a hidden element.

    The content is ordinary hidden DOM rather than a ``<template>`` so a packaged report's link
    rewriter and a localization pass both reach it.

    Labels and ``stage`` are rendered HTML so a track's localization pass sees each label as its
    own text node; ``error`` is evidence and stays verbatim. ``links`` are (href, rendered label) pairs.
    """
    links_html = "".join(f'<a href="{esc(href)}">{text}</a>' for href, text in links)
    return (f'<button type="button" class="inventory-diagnosis" data-inventory-diagnosis>{label}</button>'
            f'<div hidden data-diagnosis-content><p class="inventory-dialog-title"><strong>{title}</strong></p><dl><dt>{stage_label}</dt><dd>{stage}</dd>'
            f'<dt>{error_label}</dt><dd><pre>{esc(error)}</pre></dd></dl>'
            f'<p class="inventory-dialog-links">{links_html}</p></div>')


def diagnosis_dialog(*, label, close):
    """The one diagnosis dialog a page's ``diagnosis_button`` rows share."""
    return (f'<dialog class="inventory-dialog" data-inventory-dialog aria-label="{esc(label)}">'
            f'<div data-dialog-body></div><button type="button" data-dialog-close>{close}</button></dialog>')


def detail_navigation(back_href, *, back, previous, next_label, back_attributes=""):
    """Back to the inventory (with the reader's filters once known), then previous/next within it.

    ``back_attributes`` adds pre-rendered attributes a track's own release check looks for.
    """
    return (f'<div class="detail-navigation" data-detail-nav role="navigation" aria-label="{esc(back)}">'
            f'<a href="{esc(back_href)}" data-detail-back{back_attributes}><span aria-hidden="true">←</span> <span>{back}</span></a>'
            f'<a data-detail-previous hidden><span aria-hidden="true">←</span> <span>{previous}</span></a>'
            f'<a data-detail-next hidden><span>{next_label}</span> <span aria-hidden="true">→</span></a></div>')


INVENTORY_STYLE = (
    ".inventory-notice{margin:8px 0;padding:8px 12px;border-radius:8px;"
    "background:color-mix(in srgb,currentColor 8%,transparent)}"
    ".inventory-more{margin:8px 0}"
    "[data-inventory-paged]{display:none!important}"
    "[data-inventory] .scroll,[data-inventory] .table-wrap,[data-inventory] .inventory{max-height:70vh;overflow:auto}"
    ".inventory-dialog{max-width:min(720px,calc(100vw - 32px));padding:20px 24px;border-radius:12px;"
    "border:1px solid color-mix(in srgb,currentColor 20%,transparent);"
    "background:var(--panel,var(--surface,var(--bg,Canvas)));color:var(--ink,var(--fg,CanvasText))}"
    ".inventory-dialog::backdrop{background:rgb(0 0 0/.45)}"
    ".inventory-dialog-title{font-size:1.15rem;margin:0 0 12px}.inventory-dialog dt{font-weight:600}"
    ".inventory-dialog dd{margin:2px 0 12px}"
    ".inventory-dialog pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:40vh;overflow:auto}"
    ".inventory-dialog-links{display:flex;flex-wrap:wrap;gap:12px}"
    ".detail-navigation{display:flex;flex-wrap:wrap;gap:8px 16px;margin:8px 0 16px}"
    # Track filter rules (e.g. `.filters label{flex-direction:column}`, `.filters input{width:100%}`)
    # also match these checkbox pairs, so every property they set is restated here.
    ".same-task-explorer fieldset label{display:inline-flex;flex:0 0 auto;flex-direction:row;"
    "align-items:center;gap:6px;margin:4px 12px 4px 0}"
    ".same-task-explorer fieldset input[type=checkbox]{width:auto;min-width:0;margin:0;padding:0}"
)

INVENTORY_SCRIPT = r"""
;(()=>{if(typeof document==='undefined')return;const TRAIL='report-inventory-trail';
const key=(c,i)=>c.name||c.id||c.dataset.filter||c.dataset.explorerFilter||('c'+i);
const rowsOf=root=>{const r=[...root.querySelectorAll('[data-inventory-row]')];return r.length?r:[...root.querySelectorAll('tbody tr')]};
const linkOf=row=>row.querySelector('a[data-inventory-detail]')||row.querySelector('a[href]');
// A track filters a row out with the hidden attribute or an inline display:none; paging only
// marks rows with its own attribute, so the two never overwrite each other.
const filtered=r=>r.hidden||r.style.display==='none';
// mode: 'reset' to the first page after a filter change, 'more' for the next page, 'keep' after a re-sort.
function paginate(root,mode){const size=+root.dataset.inventoryPageSize||100,more=root.querySelector('[data-inventory-more]');root._limit=mode==='more'?root._limit+size:mode==='keep'&&root._limit?root._limit:size;let shown=0,left=0;rowsOf(root).forEach(r=>{const paged=!filtered(r)&&shown>=root._limit;if(!filtered(r)&&!paged)shown++;if(paged)left++;if(r.hasAttribute('data-inventory-paged')!==paged)r.toggleAttribute('data-inventory-paged',paged)});if(more){if(more.hidden!==!left)more.hidden=!left;if(more.dataset.remaining!==String(left))more.dataset.remaining=left}return shown}
const values=c=>c.type==='checkbox'?[c.checked?'1':'0']:c.multiple?[...c.selectedOptions].map(o=>o.value):[c.value];
function save(root,controls){const url=new URL(location.href);[...url.searchParams.keys()].filter(k=>k.startsWith('f.')).forEach(k=>url.searchParams.delete(k));controls.forEach((c,i)=>{const k='f.'+key(c,i),v=values(c);if(c.multiple)url.searchParams.set(k+'.n',String(v.length));v.forEach(x=>url.searchParams.append(k,x))});history.replaceState(history.state,'',url)}
// Events this script dispatches itself are marked so they are not taken as the reader's choice;
// while restoring, the root element carries data-inventory-restoring so a track can skip resets.
let firing=false;function fire(c){firing=true;try{c.dispatchEvent(new Event('input',{bubbles:true}));c.dispatchEvent(new Event('change',{bubbles:true}))}finally{firing=false}}
// Until the reader picks a filter, an empty failure view switches once to the fallback. A track
// may filter late (after fetching data), so this runs on every repagination, not only at load.
function settle(root){if(root._settled)return;const rows=rowsOf(root),result=root.querySelector(root.dataset.inventoryResult||'[data-inventory-result-control]'),fallback=root.dataset.inventoryFallback||'';if(!rows.length||!result||result.value===fallback||rows.some(r=>!filtered(r)))return;root._settled=true;result.value=fallback;fire(result);const notice=root.querySelector('[data-inventory-notice]');if(notice)notice.hidden=false}
function refresh(root,mode){paginate(root,mode);settle(root)}
function record(root){const items=rowsOf(root).filter(r=>!filtered(r)).map(linkOf).filter(Boolean).map(x=>x.href);try{sessionStorage.setItem(TRAIL,JSON.stringify({back:location.href,items:[...new Set(items)]}))}catch(_){}}
function restore(root,controls){const params=new URL(location.href).searchParams,saved=[];controls.forEach((c,i)=>{const k='f.'+key(c,i),v=params.getAll(k);if(!v.length&&!(c.multiple&&params.get(k+'.n')==='0'))return;if(c.type==='checkbox')c.checked=v[0]==='1';else if(c.multiple)[...c.options].forEach(o=>{o.selected=v.includes(o.value)});else c.value=v[0];saved.push(c)});if(!saved.length)return;root._settled=true;
const again=()=>saved.forEach(c=>{const v=params.getAll('f.'+key(c,controls.indexOf(c)));if(c.type==='checkbox')c.checked=v[0]==='1';else if(c.multiple)[...c.options].forEach(o=>{o.selected=v.includes(o.value)});else c.value=v[0]});
document.documentElement.dataset.inventoryRestoring='';try{saved.forEach(c=>{fire(c);again()});fire(saved[saved.length-1])}finally{delete document.documentElement.dataset.inventoryRestoring}}
function init(root){if(root._inventory)return;root._inventory=true;const controls=[...root.querySelectorAll('select,input:not([type=button])')].filter(c=>!c.closest('dialog'));restore(root,controls);
// Repaginate when a track filters (hidden or inline display) or reorders rows by sorting.
let queued=false;const observer=new MutationObserver(()=>{if(queued)return;queued=true;requestAnimationFrame(()=>{queued=false;refresh(root,'keep')})});observer.observe(root,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden','style']});
const chose=()=>{root._settled=true;save(root,controls)};
// Only the reader's own choices go into the URL; the load-time fallback and restores do not.
controls.forEach(c=>c.addEventListener('change',()=>{if(!firing)chose();refresh(root,'reset')}));controls.forEach(c=>c.addEventListener('input',()=>{if(!firing)chose()}));
// Track buttons such as "Show all" or "Reset" set controls without events; save after them.
root.addEventListener('click',e=>{const b=e.target.closest('button');if(b&&!b.closest('[data-inventory-row],tbody tr')&&!b.matches('[data-inventory-more]'))setTimeout(chose)});
root.querySelector('[data-inventory-more]')?.addEventListener('click',()=>paginate(root,'more'));
refresh(root,'reset');
// Any interaction with a row (its links, or a button opening its diagnosis) remembers the
// visible order, so every path to that row's detail page gets previous/next and back.
root.addEventListener('click',e=>{const row=e.target.closest('[data-inventory-row],tbody tr');if(row&&root.contains(row))record(root)})}
function dialogFor(){const d=document.querySelector('[data-inventory-dialog]');if(!d||d._ready)return d;d._ready=true;d.querySelector('[data-dialog-close]')?.addEventListener('click',()=>d.close());d.addEventListener('close',()=>d._opener?.focus());return d}
document.addEventListener('click',e=>{const b=e.target.closest('[data-inventory-diagnosis]'),d=b&&dialogFor();if(!d)return;const t=b.nextElementSibling;if(!t||!t.matches('[data-diagnosis-content]'))return;d._opener=b;d.querySelector('[data-dialog-body]').replaceChildren(...t.cloneNode(true).childNodes);d.showModal()});
// A page address without its in-page fragment; a packaged report's hash route (#page=…) is the page.
const route=h=>{const [b,f='']=h.split('#');return f.startsWith('page=')?b+'#'+f.split('&')[0]:b};
function detail(){const nav=document.querySelector('[data-detail-nav]');if(!nav)return;let trail;try{trail=JSON.parse(sessionStorage.getItem(TRAIL)||'null')}catch(_){}if(!trail)return;const here=route(location.href);let i=trail.items.indexOf(location.href);if(i<0)i=trail.items.findIndex(h=>route(h)===here);if(i<0)return;nav.querySelector('[data-detail-back]').href=trail.back;[['[data-detail-previous]',i-1],['[data-detail-next]',i+1]].forEach(([s,j])=>{const a=nav.querySelector(s);if(!a)return;a.hidden=!trail.items[j];if(trail.items[j])a.href=trail.items[j]})}
if(typeof addEventListener==='function')addEventListener('hashchange',detail);
// Paging must hide rows even on a page that did not include INVENTORY_STYLE.
function start(){if(document.querySelector('[data-inventory]')&&!document.getElementById('inventory-paging-style')){const st=document.createElement('style');st.id='inventory-paging-style';st.textContent='[data-inventory-paged]{display:none!important}';document.head.append(st)}document.querySelectorAll('[data-inventory]').forEach(init);detail()}
// Run after the track's own scripts, which are deferred or wait for DOMContentLoaded.
if(document.readyState==='complete'||typeof addEventListener!=='function')setTimeout(start);else addEventListener('load',()=>setTimeout(start));
// A packaged report replaces the body for each route and then announces it.
if(typeof addEventListener==='function')addEventListener('report-ready',()=>setTimeout(start));
})();
"""


def check_inventory(document):
    """Inventories keep the shared controls: failure default, fallback notice, pager and dialog."""
    soup = BeautifulSoup(document, "html.parser") if isinstance(document, str) else document
    for root in soup.select("[data-inventory]"):
        result = root.select_one(root.get("data-inventory-result", ""))
        if result is None:
            raise ValueError("Inventory lacks its result control")
        selected = result.select_one("option[selected]") or result.select_one("option")
        if selected is None or selected.get("value") != root.get("data-inventory-failure", "fail"):
            raise ValueError("Inventory must open on failures")
        if not root.select_one("[data-inventory-notice]") or not root.select_one("[data-inventory-more]"):
            raise ValueError("Inventory lacks its fallback notice or pager")
        if root.select("[data-inventory-diagnosis]") and not soup.select_one("dialog[data-inventory-dialog] [data-dialog-close]"):
            raise ValueError("Diagnosis buttons need the shared closable dialog")
    return soup
