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
