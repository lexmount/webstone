import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from harness.web_fetch.report_build import require_declared_certification as require_risk_control_certification
from harness.web_fetch.risk_control import main as run_risk_control
from harness.web_fetch.risk_control import (
    measured_risk_control_dataset,
    require_complete_diagnostic_logs,
)
from lexbench_webfetch.composite_tasks import validate_composite_acceptance
from lexbench_webfetch.inputs import definition_path
from lexbench_webfetch.metrics import evaluate_content, normalize_for_match, task_passed
from lexbench_webfetch.risk_control_dataset import (
    EXPECTED_RULE_HASHES,
)

BODY_CONTRACTS = definition_path("evaluation/risk-control/body-contracts.json")
LIST_CONTRACTS = definition_path("evaluation/risk-control/list-contracts.json")
MANIFEST = definition_path("evaluation/risk-control/qualification/cohort/input-manifest.json")
RULE_FILES = tuple(definition_path("evaluation/risk-control/" + name) for name in EXPECTED_RULE_HASHES)
from harness.web_fetch.reference_inputs import benchmark_input_path
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.stable_tasks import evaluate_stable_task, validate_acceptance_contract
from tests.conftest import read_frozen_dataset_bytes

ROOT = Path(__file__).resolve().parents[1]


def test_certificate_validation_import_does_not_require_execution_stack(tmp_path):
    code = '''
import builtins
original = builtins.__import__
def restricted(name, *args, **kwargs):
    if name in {"harness.web_fetch.providers", "harness.web_fetch.runner", "harness.web_fetch.report_build"}:
        raise RuntimeError("Execution stack is unavailable")
    return original(name, *args, **kwargs)
builtins.__import__ = restricted
from harness.web_fetch.risk_control import validate_risk_control_certification
assert callable(validate_risk_control_certification)
'''
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


DATASET = ROOT / "evaluation/risk-control/current/dataset.json"
COHORT = definition_path('evaluation/risk-control/qualification/cohort/cohort-manifest.json')
COHORT_RECEIPT = definition_path('evaluation/risk-control/qualification/cohort/cohort-receipt.json')
RUN_PROFILE = ROOT / "evaluation/risk-control/run-profile.json"
LANGUAGE_REVISION = ROOT / "research/risk-control-language-rule-revision.json"
ZILLOW_REVISION = ROOT / "research/risk-control-zillow-sample-revision.json"
XTOOLS_REVISION = ROOT / "research/risk-control-xtools-field-scope-revision.json"
CONTENT_OWNER_REVISION = ROOT / "research/risk-control-explicit-content-owner-revision.json"


@pytest.fixture(scope="module")
def dataset():
    return json.loads(read_frozen_dataset_bytes(DATASET).decode("utf-8"))




def test_training_page_accepts_the_visible_english_ui_without_dropping_chinese_content(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-153")
    # The live page retains its Chinese training title but renders its UI in
    # English. Both surfaces are part of the requested page, not a translated
    # substitute or a relaxed content requirement.
    visible = (
        "教材《C++版信息学奥赛一本通编程启蒙》题单\n"
        "Login to join training plan\n"
        "Problem Tried AC Difficulty\n"
        "Home Problem Set Training Contest Record Ranking Login Sign Up Language\n"
        "P1 [例2.1]Hello World 454 122 7\n"
    )
    result = evaluate_stable_task(visible, case["acceptance_contract"], case["url"])
    assert result["stable_task_pass"] is True
    assert result["stable_anchor_hits"] == result["stable_anchor_total"] == 15
    without_training_title = evaluate_stable_task(
        visible.replace("教材《C++版信息学奥赛一本通编程启蒙》题单", ""),
        case["acceptance_contract"], case["url"],
    )
    assert without_training_title["stable_task_pass"] is False




def test_language_rule_revision_is_the_predecessor_of_the_current_sample_revision():
    revision = json.loads(LANGUAGE_REVISION.read_text(encoding="utf-8"))
    successor = json.loads(ZILLOW_REVISION.read_text(encoding="utf-8"))
    assert (
        successor["correction"]["predecessor_dataset_sha256"]
        == revision["correction"]["new_dataset_sha256"]
    )
    assert (
        successor["correction"]["predecessor_run_profile_sha256"]
        == revision["correction"]["new_run_profile_sha256"]
    )
    assert revision["independent_browser_observation"]["anchor_total"] == 6
    assert revision["correction"]["provider_calls"] == 0


def test_zillow_sample_revision_binds_current_frozen_inputs():
    revision = json.loads(ZILLOW_REVISION.read_text(encoding="utf-8"))
    successor = json.loads((ROOT/'research/risk-control-training-list-revision.json').read_text(encoding="utf-8"))
    profile = json.loads(RUN_PROFILE.read_text(encoding="utf-8"))
    for key in ('dataset', 'run_profile'):
        assert revision['correction']['new_'+key+'_sha256'] == successor['correction']['predecessor_'+key+'_sha256']
    full_page = json.loads((ROOT/'research/risk-control-full-page-rule-revision.json').read_text(encoding="utf-8"))
    assert successor['correction']['new_dataset_sha256'] == full_page['correction']['predecessor_dataset_sha256']
    assert successor['correction']['new_run_profile_sha256'] == full_page['correction']['predecessor_run_profile_sha256']
    locale=json.loads((ROOT/'research/risk-control-navigation-language-revision.json').read_text(encoding="utf-8"))
    for key in ('dataset','run_profile'):
        assert full_page['correction']['new_'+key+'_sha256'] == locale['correction']['predecessor_'+key+'_sha256']
    responsive=json.loads((ROOT/'research/risk-control-responsive-navigation-revision.json').read_text(encoding="utf-8"))
    xtools=json.loads(XTOOLS_REVISION.read_text(encoding="utf-8"))
    content_owner=json.loads(CONTENT_OWNER_REVISION.read_text(encoding="utf-8"))
    assert responsive['predecessor_revision'] == 'research/risk-control-navigation-language-revision.json'
    assert locale['correction']['new_dataset_sha256'] == responsive['correction']['predecessor_dataset_sha256']
    assert locale['correction']['new_run_profile_sha256'] == responsive['correction']['predecessor_run_profile_sha256']
    assert xtools['predecessor_revision'] == 'research/risk-control-responsive-navigation-revision.json'
    assert responsive['correction']['new_dataset_sha256'] == xtools['correction']['predecessor_dataset_sha256']
    assert responsive['correction']['new_run_profile_sha256'] == xtools['correction']['predecessor_run_profile_sha256']
    assert xtools['correction']['predecessor_dataset_sha256'] == responsive['correction']['new_dataset_sha256']
    assert xtools['correction']['predecessor_run_profile_sha256'] == responsive['correction']['new_run_profile_sha256']
    assert content_owner['predecessor_revision'] == str(
        XTOOLS_REVISION.relative_to(ROOT)
    )
    assert content_owner['correction']['predecessor_dataset_sha256'] == xtools['correction']['new_dataset_sha256']
    assert content_owner['correction']['predecessor_run_profile_sha256'] == xtools['correction']['new_run_profile_sha256']
    assert content_owner['correction']['new_dataset_sha256'] == hashlib.sha256(
        read_frozen_dataset_bytes(DATASET)
    ).hexdigest()
    assert content_owner['correction']['new_run_profile_sha256'] == hashlib.sha256(
        RUN_PROFILE.read_bytes()
    ).hexdigest()
    assert profile["dataset"]["sha256"] == content_owner["correction"]["new_dataset_sha256"]
    assert profile["dataset"]["path"] == "evaluation/risk-control/current/dataset.json"
    assert revision["correction"]["provider_calls"] == 1
    assert revision["correction"]["rerun_case_ids"] == ["risk-diverse-134"]


def test_every_secondary_risk_anchor_is_bound_to_independent_revision(dataset):
    revision = json.loads(LANGUAGE_REVISION.read_text(encoding="utf-8"))
    case_id = revision["case_id"]
    locale=json.loads((ROOT/'research/risk-control-navigation-language-revision.json').read_text(encoding="utf-8"))
    declared = {**revision["correction"]["equivalent_ui_alternatives_added"],
                **locale["correction"]["equivalent_ui_alternatives_added"]}
    observed = {}
    for case in dataset["cases"]:
        for anchor in case["acceptance_contract"]["stable_text_anchors"]:
            for alternative in anchor["alternatives"][1:]:
                observed[(case["id"], anchor["id"])] = alternative
    assert observed == {(case_id, anchor_id): text for anchor_id, text in declared.items()}
    assert len(revision["independent_browser_observation"]["visible_text_sha256"]) == 64










def test_zillow_case_binds_location_and_only_requires_visible_card_fields(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-134")
    assert case["url"] == "https://www.zillow.com/seattle-wa/"
    anchors = [
        item["alternatives"][0]
        for item in case["acceptance_contract"]["stable_text_anchors"]
    ]
    assert "Seattle WA Real Estate & Homes For Sale" in anchors
    fields = case["composite_acceptance"]["groups"][0]["required_fields"]
    assert [field["id"] for field in fields] == [
        "price", "bedrooms", "bathrooms", "area"
    ]

    cards = []
    for index in range(8):
        zpid = 10000000 + index
        cards.append(
            f"[10{index} Example Avenue, Seattle, WA 9810{index}]"
            f"(https://www.zillow.com/homedetails/10{index}-Example-Ave-Seattle-WA-9810{index}/"
            f"{zpid}_zpid/)\n$900,000\n3 bds\n2 ba\n1,800 sqft"
        )
    positive = "\n".join(anchors) + "\n\n" + "\n\n".join(cards)
    accepted = evaluate_content(
        positive,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        composite_acceptance=case["composite_acceptance"],
        base_url=case["url"],
    )
    assert accepted["content_pass"] is True

    wrong_location = positive.replace(
        "Seattle WA Real Estate & Homes For Sale",
        "Liberal MO Real Estate & Homes For Sale",
    )
    assert evaluate_content(
        wrong_location,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        composite_acceptance=case["composite_acceptance"],
        base_url=case["url"],
    )["content_pass"] is False

    missing_visible_area = positive.replace("1,800 sqft", "", 1)
    assert evaluate_content(
        missing_visible_area,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        composite_acceptance=case["composite_acceptance"],
        base_url=case["url"],
    )["content_pass"] is False


@pytest.mark.parametrize('fields,expected', [
    ('3 beds · 2 baths · 1,200 sqft', True),
    # The frozen contract declares bathrooms an integer field.
    ('3 beds · 2.5 baths · 1,200 sqft', False),
    ('5 bds4 ba3,380 sqftActive', True),
    ('**3** beds · __2__ baths · **1,200** sqft', True),
    ('1200 sqft beds\n1200 sqft baths\n1200 sqft', False),
    ('3 beds · 1,200 sqft', False),
])
def test_zillow_full_score_requires_distinct_numeric_card_fields(dataset, fields, expected):
    from lexbench_webfetch.scoring import score_attempt
    case=next(c for c in dataset['cases'] if c['id']=='risk-diverse-134')
    anchors='\n'.join(a['alternatives'][0] for a in case['acceptance_contract']['stable_text_anchors'])
    cards=[f'[10{i} Example Avenue](https://www.zillow.com/homedetails/Example/{10000000+i}_zpid/)\n$900,000\n{fields}' for i in range(8)]
    result=score_attempt(case,(anchors+'\n\n'+'\n\n'.join(cards)).encode(),ok=True,final_url=case['url'])
    assert result['content_pass'] is expected
    assert result['composite_group_results'][0]['count']==(8 if expected else 0)






def test_run_profile_binds_every_rule_and_qualification_authority():
    import hashlib

    profile = json.loads(RUN_PROFILE.read_text(encoding="utf-8"))
    expected = {
        "qualification_manifest",
        "cohort_receipt",
        "cohort_manifest",
        "first_30_rules",
        "remaining_70_rules",
        "list_contracts",
        "body_contracts",
        "rule_independent_audit",
        "rule_completeness_audit",
    }
    assert set(profile["source_bindings"]) == expected
    for binding in profile["source_bindings"].values():
        path = benchmark_input_path(ROOT / binding["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]


def test_direct_formal_execution_is_rejected_before_provider_validation(tmp_path):
    with pytest.raises(SystemExit):
        run_risk_control(["--execute", "--output", str(tmp_path / "run")])


def test_frozen_execution_inputs_do_not_require_historical_source_files(tmp_path):
    from harness.web_fetch.risk_control import frozen_risk_control_input_snapshot
    profile = json.loads(RUN_PROFILE.read_text(encoding="utf-8"))
    profile_path = tmp_path / "evaluation/risk-control/run-profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_bytes(RUN_PROFILE.read_bytes())
    bindings = [profile["dataset"], *profile["source_bindings"].values(),
                {"path": profile["provider"]["config_path"]}]
    for binding in bindings:
        target = tmp_path / binding["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_frozen_dataset_bytes(ROOT / binding["path"]) if binding == profile['dataset'] else benchmark_input_path(ROOT / binding["path"]).read_bytes())
    # There is no qualification data home or browser.txt tree in this fixture.
    snapshot = frozen_risk_control_input_snapshot(profile_path)
    assert snapshot["case_count"] == 100
    (tmp_path / profile["dataset"]["path"]).write_text('{"cases": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="Changed frozen input"):
        frozen_risk_control_input_snapshot(profile_path)


def certified_report_fixture(tmp_path):
    profile_bytes = RUN_PROFILE.read_bytes()
    profile = json.loads(profile_bytes)
    profile_sha = hashlib.sha256(profile_bytes).hexdigest()
    frozen_dataset = json.loads(read_frozen_dataset_bytes(DATASET).decode("utf-8"))
    dataset = measured_risk_control_dataset(profile, frozen_dataset, profile_sha)
    provider = profile["provider"]["name"]
    identity = {
        "reported_version": profile["provider"]["reported_version"],
        "binary_sha256": profile["provider"]["binary_sha256"],
    }
    records = []
    for case in frozen_dataset["cases"]:
        records.append(
            {
                "attempt_id": f"{provider}__{case['id']}__r001",
                "provider": provider,
                "case_id": case["id"],
                "run": 1,
                "provider_metadata": {
                    **identity,
                    "diagnostic_log_path": (
                        f"private_artifacts/{provider}/{case['id']}/run-001.stderr.log"
                    ),
                    "diagnostic_log_sha256": "a" * 64,
                    "diagnostic_log_bytes": 12,
                    "diagnostic_log_truncated": False,
                },
            }
        )
    attempt_ids = [record["attempt_id"] for record in records]
    manifest = {
        "started_at": "2026-09-29T00:00:00+00:00",
        "attempt_count": 100,
        "attempt_ids": attempt_ids,
        "unexecuted": [],
        "dataset_sha256": profile["dataset"]["sha256"],
        "provider_config_sha256": profile["provider"]["config_sha256"],
        "providers": [
            {
                "name": provider,
                "type": "moli_webfetch",
                "min_interval_seconds": 0,
                "implementation_identity": identity,
            }
        ],
        "parameters": {
            "runs": profile["execution"]["runs_per_case"],
            "timeout": profile["execution"]["timeout_seconds"],
            "delay": profile["execution"]["delay_seconds"],
            "provider_concurrency": profile["execution"]["concurrency"],
            "max_failure_retries": profile["execution"]["max_failure_retries"],
        },
    }
    execution = {
        "schema_version": 1,
        "aggregate": {
            "providers": [provider],
            "case_ids": [case["id"] for case in frozen_dataset["cases"]],
            "started_at": manifest["started_at"],
            "completed_at": "2026-09-29T00:10:00+00:00",
            "batch_count": 1,
        },
        "batches": [manifest],
    }
    proof_files = [
        {
            "case_id": case["id"],
            **case["source_benchmark"]["risk_control"]["reference_proof"],
            "bytes": 1,
        }
        for case in frozen_dataset["cases"]
    ]
    proof_inventory = json.dumps(
        proof_files, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    proof_snapshot = {
        "case_count": 100,
        "inventory_sha256": hashlib.sha256(proof_inventory.encode()).hexdigest(),
        "files": proof_files,
    }
    proof_verification = {
        "schema_version": 1,
        "kind": "risk_control_reference_proof_verification",
        "before": proof_snapshot,
        "after": proof_snapshot,
        "unchanged": True,
    }
    (tmp_path / "reference-proof-verification.json").write_text(json.dumps(proof_verification), encoding="utf-8")
    proof_sha = hashlib.sha256(
        (tmp_path / "reference-proof-verification.json").read_bytes()
    ).hexdigest()
    manifest["reference_proof_verification_sha256"] = proof_sha
    execution["batches"] = [manifest]
    (tmp_path / "dataset.json").write_text(json.dumps(dataset), encoding="utf-8")
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8"
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "execution.json").write_text(json.dumps(execution), encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "kind": "certified_risk_control_100_moli_v1",
        "profile_sha256": profile_sha,
        "dataset_sha256": hashlib.sha256((tmp_path / "dataset.json").read_bytes()).hexdigest(),
        "results_sha256": hashlib.sha256((tmp_path / "results.jsonl").read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256((tmp_path / "manifest.json").read_bytes()).hexdigest(),
        "execution_sha256": hashlib.sha256((tmp_path / "execution.json").read_bytes()).hexdigest(),
        "reference_proof_verification_sha256": proof_sha,
        "provider_identity_before": identity,
        "provider_identity_after": identity,
        "logical_results": 100,
        "physical_calls": 100,
        "complete_diagnostic_logs": True,
    }
    (tmp_path / "certification.json").write_text(json.dumps(receipt), encoding="utf-8")
    return dataset, records, receipt, manifest, execution


def rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution):
    manifest["attempt_count"] = len(records)
    manifest["attempt_ids"] = [record["attempt_id"] for record in records]
    execution["aggregate"]["case_ids"] = [record["case_id"] for record in records]
    execution["batches"] = [manifest]
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8"
    )
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "execution.json").write_text(json.dumps(execution), encoding="utf-8")
    receipt.update(
        {
            "results_sha256": hashlib.sha256((tmp_path / "results.jsonl").read_bytes()).hexdigest(),
            "manifest_sha256": hashlib.sha256(
                (tmp_path / "manifest.json").read_bytes()
            ).hexdigest(),
            "execution_sha256": hashlib.sha256(
                (tmp_path / "execution.json").read_bytes()
            ).hexdigest(),
            "logical_results": len(records),
            "physical_calls": len(records),
        }
    )
    (tmp_path / "certification.json").write_text(json.dumps(receipt), encoding="utf-8")


def test_report_requires_complete_profile_bound_risk_control_certification(tmp_path):
    dataset, records, receipt, _, _ = certified_report_fixture(tmp_path)
    require_risk_control_certification(tmp_path, dataset, records)
    receipt["results_sha256"] = "0" * 64
    (tmp_path / "certification.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatched"):
        require_risk_control_certification(tmp_path, dataset, records)



def test_report_rejects_hash_bound_but_incomplete_reference_proof_receipt(tmp_path):
    dataset, records, receipt, manifest, execution = certified_report_fixture(tmp_path)
    proof_path = tmp_path / "reference-proof-verification.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    proof["unchanged"] = False
    proof_path.write_text(json.dumps(proof), encoding="utf-8")
    proof_sha = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    manifest["reference_proof_verification_sha256"] = proof_sha
    receipt["reference_proof_verification_sha256"] = proof_sha
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)

    with pytest.raises(ValueError, match="reference proof verification"):
        require_risk_control_certification(tmp_path, dataset, records)


@pytest.mark.parametrize("defect", ["missing", "duplicate", "provider", "identity"])
def test_report_rejects_self_consistent_but_incomplete_or_wrong_matrix(tmp_path, defect):
    dataset, records, receipt, manifest, execution = certified_report_fixture(tmp_path)
    if defect == "missing":
        records.pop()
    elif defect == "duplicate":
        records[-1] = {**records[0]}
    elif defect == "provider":
        records[-1] = {**records[-1], "provider": "wrong-provider"}
    else:
        wrong = {"reported_version": "moli 9.9.9", "binary_sha256": "b" * 64}
        receipt["provider_identity_before"] = wrong
        receipt["provider_identity_after"] = wrong
        manifest["providers"][0]["implementation_identity"] = wrong
        for record in records:
            record["provider_metadata"].update(wrong)
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)
    with pytest.raises(ValueError):
        require_risk_control_certification(tmp_path, dataset, records)


def test_every_contract_validates_and_body_has_positive_and_adjacent_negative(dataset):
    body_cases = []
    for case in dataset["cases"]:
        validate_acceptance_contract(case["acceptance_contract"])
        if composite := case.get("composite_acceptance"):
            validate_composite_acceptance(composite)
        if case.get("body_requirements"):
            body_cases.append(case)
    assert len(body_cases) == 79
    # The frozen dataset is hash-bound by the run profile. Scoring the smallest
    # bodies covers every region count, composite cases and repeated labels.
    body_cases.sort(key=lambda case: sum(len(row["text"]) for row in case["body_requirements"]["regions"]))
    for case in body_cases[:20]:
        anchors = _anchors(case)
        regions = [row["text"] for row in case["body_requirements"]["regions"]]
        assert _evaluate(case, "\n".join([*anchors, *regions]))["content_pass"], case["id"]
        # If a body boundary deliberately repeats a required stable label,
        # omitting that body region must omit the duplicated label too.  Keeping
        # the label would silently reinsert the supposedly missing region.
        omitted = normalize_for_match(regions[-1])
        negative_anchors = [anchor for anchor in anchors if normalize_for_match(anchor) not in omitted]
        failed = _evaluate(case, "\n".join([*negative_anchors, *regions[:-1]]))
        assert not failed["body_coverage_pass"], case["id"]
        assert not failed["content_pass"], case["id"]


def _evaluate(case, text):
    return evaluate_content(
        text,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        body_requirements=case.get("body_requirements"),
        base_url=case["url"],
    )


def _anchors(case):
    return [row["alternatives"][0] for row in case["acceptance_contract"]["stable_text_anchors"]]


def test_full_page_cases_require_both_page_chrome_and_complete_body(dataset):
    cases = {case["id"]: case for case in dataset["cases"]}
    zhihu = cases["risk-diverse-002"]
    regions = [row["text"] for row in zhihu["body_requirements"]["regions"]]
    partial = _evaluate(zhihu, "\n".join([*_anchors(zhihu), *regions[:3]]))
    assert not partial["body_coverage_pass"] and not partial["content_pass"]

    archwiki = cases["risk-diverse-038"]
    body = "\n".join(row["text"] for row in archwiki["body_requirements"]["regions"])
    body_only = _evaluate(archwiki, body)
    assert body_only["body_coverage_pass"]
    assert not body_only["stable_task_pass"] and not body_only["content_pass"]
    chrome_only = _evaluate(archwiki, "\n".join(_anchors(archwiki)))
    assert chrome_only["stable_task_pass"]
    assert not chrome_only["body_coverage_pass"] and not chrome_only["content_pass"]


def test_ibiblio_scope_requires_archive_identity_and_collection_introduction(dataset):
    case = next(case for case in dataset["cases"] if case["id"] == "risk-diverse-192")
    complete = (
        "ibiblio.org is a public digital library and collection of collections. "
        "It preserves freely available information about software, music, literature, art, "
        "history, science, politics, and cultural studies in collaboration with the "
        "University of North Carolina at Chapel Hill."
    )
    news_and_footer = "ibiblio News One Two Three Logos Hosted by UNC Chapel Hill"
    wrong_identity = complete.replace("ibiblio.org", "Another Archive")
    identity_only = "ibiblio.org is hosted with UNC Chapel Hill. Browse Logos and news."
    assert task_passed(True, score_attempt(case, complete.encode(), ok=True))
    assert not task_passed(True, score_attempt(case, news_and_footer.encode(), ok=True))
    assert not task_passed(True, score_attempt(case, wrong_identity.encode(), ok=True))
    assert not task_passed(True, score_attempt(case, identity_only.encode(), ok=True))


def test_formal_run_requires_one_complete_hash_bound_diagnostic_log_per_case():
    complete = {
        "case_id": "complete",
        "provider_metadata": {
            "diagnostic_log_path": "private_artifacts/moli/complete/run-001.stderr.log",
            "diagnostic_log_sha256": "a" * 64,
            "diagnostic_log_bytes": 12,
            "diagnostic_log_truncated": False,
        },
    }
    require_complete_diagnostic_logs([complete])
    for row in [
        {"case_id": "missing", "provider_metadata": {}},
        {
            "case_id": "truncated",
            "provider_metadata": {
                **complete["provider_metadata"],
                "diagnostic_log_truncated": True,
            },
        },
    ]:
        with pytest.raises(ValueError, match=row["case_id"]):
            require_complete_diagnostic_logs([row])






@pytest.mark.parametrize(
    ("case_id", "required_chrome"),
    [
        ("risk-011", ["视频", "财经", "科技", "热点", "国际"]),
        ("risk-101", ["首页", "番剧", "直播", "游戏中心", "会员购", "漫画", "赛事"]),
        (
            "risk-diverse-031",
            ["Forums", "Wiki", "Test Exchange", "Read", "View source", "View history"],
        ),
        (
            "risk-diverse-041",
            ["Help/Guide", "Wiki", "Timeline", "Roadmap", "Browse Source", "View Tickets"],
        ),
        (
            "risk-diverse-225",
            ["Products", "Services & Support"],
        ),
    ],
)
def test_page_content_without_declared_initial_chrome_fails(
    dataset,
    case_id,
    required_chrome,
):
    case = next(case for case in dataset["cases"] if case["id"] == case_id)
    anchors = [row["alternatives"][0] for row in case["acceptance_contract"]["stable_text_anchors"]]
    assert all(value in anchors for value in required_chrome)
    partial = "\n".join(value for value in anchors if value not in required_chrome)
    if body := case.get("body_requirements"):
        partial += "\n" + "\n".join(row["text"] for row in body["regions"])
    result = evaluate_content(
        partial,
        [],
        min_content_chars=1,
        acceptance_contract=case["acceptance_contract"],
        body_requirements=case.get("body_requirements"),
        base_url=case["url"],
    )
    assert not result["stable_task_pass"]
    assert not result["content_pass"]



def test_certified_report_preserves_certificate_bound_files(tmp_path, capsys):
    from harness.web_fetch.config import load_dataset
    from harness.web_fetch.providers.fixture import FixtureProvider
    from harness.web_fetch.report_build import build_report
    from harness.web_fetch.runner import run_evaluation

    profile = json.loads(RUN_PROFILE.read_text(encoding="utf-8"))
    provider = profile["provider"]["name"]
    read_frozen_dataset_bytes(DATASET)
    frozen, cases = load_dataset(DATASET)
    measured = measured_risk_control_dataset(
        profile, frozen, hashlib.sha256(RUN_PROFILE.read_bytes()).hexdigest())
    provider_config = tmp_path / "fixture-provider.json"
    provider_config.write_text(json.dumps({provider: {"type": "fixture"}}), encoding="utf-8")
    records, _ = run_evaluation(
        output_dir=tmp_path, dataset_meta=measured, dataset_path=DATASET,
        cases=cases, providers=[FixtureProvider(provider, {"type": "fixture"})],
        provider_config_path=provider_config, runs=1, timeout=5, delay=0,
        provider_concurrency=1, max_failure_retries=0)
    capsys.readouterr()
    _, certified, receipt, _, _ = certified_report_fixture(tmp_path)
    diagnostic = b"DEBUG fixture diagnostic\n"
    for row, certified_row in zip(records, certified, strict=True):
        row["provider_metadata"].update(certified_row["provider_metadata"])
        metadata = row["provider_metadata"]
        metadata["diagnostic_log_sha256"] = hashlib.sha256(diagnostic).hexdigest()
        metadata["diagnostic_log_bytes"] = len(diagnostic)
        path = tmp_path / metadata["diagnostic_log_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(diagnostic)
    results = tmp_path / "results.jsonl"
    results.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    receipt["results_sha256"] = hashlib.sha256(results.read_bytes()).hexdigest()
    (tmp_path / "certification.json").write_text(json.dumps(receipt), encoding="utf-8")
    report = tmp_path / "report"
    index = build_report(tmp_path, report, include_english=True)
    for name in ("dataset.json", "manifest.json", "execution.json",
                 "reference-proof-verification.json", "certification.json"):
        assert (report / name).read_bytes() == (tmp_path / name).read_bytes()
    require_risk_control_certification(report, measured, records)

    # The full 100-case matrix renders the interactive report in both languages.
    home = index.read_text(encoding="utf-8")
    detail = (report / f"attempts/{provider}__risk-diverse-045__r001.html").read_text(encoding="utf-8")
    case_page = (report / "datasets/risk-diverse-045.html").read_text(encoding="utf-8")
    guide = (report / "guide/index.html").read_text(encoding="utf-8")
    assert "本报告包含 100 个任务" in guide and "风控网页正文" in home
    for text in ("组合规则结果", "同条目字段", "运行日志与证据", "diagnostic-log"):
        assert text in detail
    assert "至少 3 条" in case_page and "/admin_posts/" in case_page
    archive_detail = (report / f"attempts/{provider}__risk-diverse-192__r001.html").read_text(encoding="utf-8")
    assert "homepage-news" not in archive_detail
    assert "freely" in (report / "datasets/risk-diverse-192.html").read_text(encoding="utf-8")
    assert "This report contains 100 tasks" in (report / "en/guide/index.html").read_text(encoding="utf-8")


def test_new_risk_certification_uses_frozen_inputs_and_preserves_old_receipts(tmp_path):
    from harness.web_fetch.risk_control import frozen_risk_control_input_snapshot
    dataset, records, receipt, manifest, execution = certified_report_fixture(tmp_path)
    snapshot = frozen_risk_control_input_snapshot(RUN_PROFILE)
    verification = {"schema_version": 1, "kind": "risk_control_frozen_input_verification",
                    "before": snapshot, "after": snapshot, "unchanged": True}
    path = tmp_path / "frozen-input-verification.json"
    path.write_text(json.dumps(verification), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest.pop("reference_proof_verification_sha256")
    manifest["frozen_input_verification_sha256"] = digest
    receipt.pop("reference_proof_verification_sha256")
    receipt.update(kind="certified_risk_control_100_moli_v2",
                   frozen_input_verification_sha256=digest)
    (tmp_path / "reference-proof-verification.json").unlink()
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)
    require_risk_control_certification(tmp_path, dataset, records)
    verification["after"] = {**snapshot, "case_count": 99}
    path.write_text(json.dumps(verification), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["frozen_input_verification_sha256"] = digest
    receipt["frozen_input_verification_sha256"] = digest
    rewrite_certified_report_fixture(tmp_path, records, receipt, manifest, execution)
    with pytest.raises(ValueError, match="frozen input verification"):
        require_risk_control_certification(tmp_path, dataset, records)
