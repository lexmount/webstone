"""The shared same-task explorer renders every registered participant and refuses an unknown baseline."""
import pytest
from bs4 import BeautifulSoup

from harness.reporting import explorer

LABELS = explorer.Labels(title="Same task", description="Rows are tasks.", task="Task", baseline="Baseline",
                         compare="Compare", relation="Relation", any="All",
                         relations={name: name for name in explorer.RELATIONS})
PARTICIPANTS = [explorer.Participant(key, key.upper()) for key in ("a", "b", "c")]


def render_fixture():
    """Three participants over four tasks: a baseline win, a baseline loss, all pass and all fail."""
    outcomes = {"t1": "ppf", "t2": "fpp", "t3": "ppp", "t4": "fff"}
    tasks = [explorer.Task(cell=task, search=task, filters={"tier": "easy" if task in ("t1", "t3") else "hard"},
                           outcomes={key: explorer.Outcome({"p": "pass", "f": "fail"}[state], state,
                                                           f"results/{key}-{task}.html")
                                     for key, state in zip("abc", states, strict=True)})
             for task, states in outcomes.items()]
    return explorer.same_task_explorer("matrix", PARTICIPANTS, tasks, baseline="a", labels=LABELS,
                                       filters=[explorer.Filter("tier", "Tier", [("easy", "Easy"), ("hard", "Hard")])])


def test_explorer_renders_registry_columns_links_and_controls():
    soup = BeautifulSoup(render_fixture(), "html.parser")
    assert [th.get("data-participant") for th in soup.select("thead th")] == [None, "a", "b", "c"]
    assert soup.select_one("[data-explorer-baseline] option[selected]")["value"] == "a"
    assert [box["value"] for box in soup.select("[data-explorer-compare] input[checked]")] == ["a", "b", "c"]
    assert soup.select_one("[data-explorer-relation] option[selected]")["value"] == "differs"
    cell = soup.select_one('tr[data-explorer-row] td[data-participant="c"]')
    assert cell["data-outcome"] == "fail" and cell.a["href"] == "results/c-t1.html"


def test_explorer_refuses_an_unregistered_baseline_or_incomplete_relations():
    with pytest.raises(ValueError, match="include the baseline"):
        explorer.same_task_explorer("m", PARTICIPANTS, [], baseline="z", labels=LABELS)
    with pytest.raises(ValueError, match="every outcome relation"):
        explorer.same_task_explorer("m", PARTICIPANTS, [], baseline="a",
                                    labels=LABELS.__class__(**{**LABELS.__dict__, "relations": {}}))
    with pytest.raises(ValueError, match="Unknown same-task outcome"):
        explorer.Outcome("skipped", "")


def inventory_fixture(selected="fail", notice=True, dialog=True):
    options = "".join(f'<option value="{v}"' + (" selected" if v == selected else "") + f">{v or 'All'}</option>"
                      for v in ("fail", "pass", ""))
    button = explorer.diagnosis_button("Diagnose", title="T", stage="request", error="Boom: exact",
                                       links=[("r/1.html", "Open")], stage_label="Stage", error_label="Error")
    return ('<div' + explorer.inventory_attributes(result_control='select[data-filter="result"]') + '>'
            f'<select data-filter="result">{options}</select><table><tbody>'
            f'<tr data-result="fail"><td><a data-inventory-detail href="r/1.html">1</a> {button}</td></tr></tbody></table>'
            + (explorer.inventory_notice("No failures; showing all.", "Show more") if notice else "") + '</div>'
            + (explorer.diagnosis_dialog(label="Diagnosis", close="Close") if dialog else ""))


def test_inventory_opens_on_failures_with_notice_pager_and_dialog():
    soup = explorer.check_inventory(inventory_fixture())
    content = soup.select_one("[data-diagnosis-content][hidden]")
    assert content.select_one("pre").get_text() == "Boom: exact" and content.select_one('a[href="r/1.html"]')
    assert soup.select_one("[data-inventory]")["data-inventory-page-size"] == str(explorer.PAGE_SIZE)
    nav = BeautifulSoup(explorer.detail_navigation("../results.html", back="Back", previous="Prev", next_label="Next"),
                        "html.parser")
    assert nav.select_one("[data-detail-back]")["href"] == "../results.html"
    assert nav.select_one("[data-detail-previous]").has_attr("hidden") and nav.select_one("[data-detail-next]").has_attr("hidden")


@pytest.mark.parametrize("kwargs, message", [
    ({"selected": ""}, "open on failures"),
    ({"selected": "pass"}, "open on failures"),
    ({"notice": False}, "fallback notice or pager"),
    ({"dialog": False}, "shared closable dialog"),
])
def test_inventory_check_rejects_all_default_missing_notice_or_dialog(kwargs, message):
    with pytest.raises(ValueError, match=message):
        explorer.check_inventory(inventory_fixture(**kwargs))
