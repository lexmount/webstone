"""The same-task explorer: one row per task, one column per registered participant.

A track passes its participants in registry order, a baseline and each task's
outcomes; the explorer adds no participant of its own. Readers pick the
baseline, any set of comparison participants and an explicit outcome relation
between them. Every outcome links to the participant's full result when one
exists.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

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
# selected relation. Missing outcomes take part in no relation.
SCRIPT = """
function filterExplorer(root){const baseline=root.querySelector('[data-explorer-baseline]')?.value,boxes=[...root.querySelectorAll('[data-explorer-compare] input')],compared=boxes.filter(b=>b.checked&&b.value!==baseline).map(b=>b.value),relation=root.querySelector('[data-explorer-relation]')?.value||'',filters=[...root.querySelectorAll('[data-explorer-filter]')];boxes.forEach(b=>{b.disabled=b.value===baseline});root.querySelectorAll('[data-participant]').forEach(c=>{const k=c.dataset.participant;c.hidden=k!==baseline&&!compared.includes(k);c.toggleAttribute('data-baseline',k===baseline)});let visible=0,total=0;root.querySelectorAll('tbody tr[data-explorer-row]').forEach(r=>{total++;const state=k=>r.querySelector('[data-participant="'+k+'"]')?.dataset.outcome||'missing',base=state(baseline),others=compared.map(state).filter(s=>s!=='missing'),states=(base==='missing'?[]:[base]).concat(others),holds={differs:new Set(states).size>1,baseline_passes:base==='pass'&&others.includes('fail'),comparison_passes:base==='fail'&&others.includes('pass'),all_pass:states.length>0&&states.every(s=>s==='pass'),all_fail:states.length>0&&states.every(s=>s==='fail')},hidden=Boolean(relation&&!holds[relation])||filters.some(c=>c.value&&r.dataset[c.dataset.explorerFilter]!==c.value);r.hidden=hidden;if(!hidden)visible++});const count=root.querySelector('[data-explorer-count]');if(count)count.textContent=visible+' / '+total}
document.addEventListener('DOMContentLoaded',()=>document.querySelectorAll('[data-explorer]').forEach(root=>{root.querySelectorAll('select,input').forEach(c=>c.addEventListener('change',()=>filterExplorer(root)));filterExplorer(root)}));
"""
