import json

import pytest

from harness.reporting.release_check import ReleaseRules, VisualReceipt, validate_release

WINNER_TABLE = (
    '<table><tbody><tr><td data-metric="pass" data-metric-direction="max" data-sort="2">'
    '{first}</td><td data-metric="pass" data-metric-direction="max" data-sort="1">1</td>'
    "</tr></tbody></table>"
)


def report(tmp_path, *, languages=("zh",), table=None):
    root = tmp_path / "report"
    root.mkdir(parents=True)
    (root / "index.html").write_text('<a href="results/a.html">A</a>', encoding="utf-8")
    (root / "results").mkdir()
    (root / "results/a.html").write_text(table or "<p>detail</p>", encoding="utf-8")
    (root / "report-languages.json").write_text(
        json.dumps({"languages": list(languages)}), encoding="utf-8"
    )
    return root


def recording_rules(calls, receipt, *, current):
    return ReleaseRules(
        identity=lambda root: calls.append("identity") or {"pages": 2},
        languages=lambda root: calls.append("languages"),
        links=lambda root: calls.append("links"),
        evidence=lambda root: calls.append("evidence"),
        visual=VisualReceipt(
            receipt,
            is_current=lambda root, path: calls.append("receipt") or current,
            check=lambda root, path: calls.append("render"),
        ),
    )


@pytest.mark.parametrize("current", [True, False])
def test_gate_runs_track_rules_in_fixed_order(tmp_path, current):
    root = report(tmp_path)
    calls = []
    results = validate_release(root, recording_rules(calls, tmp_path / "visual.json", current=current))
    assert calls == ["identity", "languages", "links", "evidence", "receipt"] + (
        [] if current else ["render"]
    )
    assert results["identity"] == {"pages": 2}
    assert results["presentation"] == 0


def test_shared_presentation_checks_every_track(tmp_path):
    winner = '<span class="comparison-winner">2</span>'
    root = report(tmp_path, table=WINNER_TABLE.format(first=winner))
    assert validate_release(root, ReleaseRules(identity=lambda root: None))["presentation"] == 1

    missing = report(tmp_path / "missing", table=WINNER_TABLE.format(first="2"))
    with pytest.raises(ValueError, match="highlight"):
        validate_release(missing, ReleaseRules(identity=lambda root: None, links=None))


def test_shared_stages_reject_missing_edition_and_broken_link(tmp_path):
    bilingual = report(tmp_path, languages=("zh", "en"))
    with pytest.raises(ValueError, match="en edition"):
        validate_release(bilingual, ReleaseRules(identity=lambda root: None))

    broken = report(tmp_path / "broken")
    (broken / "results/a.html").unlink()
    (broken / "results/b.html").write_text("<p>other</p>", encoding="utf-8")
    with pytest.raises(ValueError, match="Broken local links"):
        validate_release(broken, ReleaseRules(identity=lambda root: None))


def test_gate_refuses_a_linked_report_before_any_rule(tmp_path):
    root = report(tmp_path)
    (root / "linked.html").symlink_to(root / "index.html")
    calls = []
    with pytest.raises(ValueError):
        validate_release(root, recording_rules(calls, tmp_path / "visual.json", current=True))
    assert calls == []
