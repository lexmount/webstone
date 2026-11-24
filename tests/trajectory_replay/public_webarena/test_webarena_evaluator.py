from __future__ import annotations

import json
from urllib.parse import urlencode

import pytest

from harness.trajectory_replay.core.suite import BrowserTask
from harness.trajectory_replay.core.evaluators import EvaluatorRegistry, TaskEvidence
from harness.trajectory_replay.public_webarena.evaluators import WebArenaVerifiedEvaluator

_NAVIGATE_HEADERS = [
    {"name": "Accept", "value": "text/html"},
    {"name": "Sec-Fetch-Dest", "value": "document"},
    {"name": "Sec-Fetch-Mode", "value": "navigate"},
    {"name": "Sec-Fetch-User", "value": "?1"},
]


def _entry(url, *, method="GET", headers=(), post=None, status=200, mime="text/html",
           text="", redirect="", cookies=()):
    request = {"method": method, "url": url, "headers": list(headers)}
    if post is not None:
        request["postData"] = post
    return {
        "request": request,
        "response": {
            "status": status,
            "redirectURL": redirect,
            "headers": [],
            "cookies": list(cookies),
            "content": {"mimeType": mime, "text": text},
        },
    }


def _form_entry(url, post_data):
    return _entry(
        url,
        method="POST",
        headers=[{"name": "content-type", "value": "application/x-www-form-urlencoded"}],
        post={
            "mimeType": "application/x-www-form-urlencoded",
            "text": urlencode(post_data),
            "params": [{"name": key, "value": str(value)} for key, value in post_data.items()],
        },
        status=302,
    )


def _wishlist_entry(site):
    return _entry(
        f"{site}/wishlist/index/add/",
        method="POST",
        post={"mimeType": "application/x-www-form-urlencoded", "text": "product=123"},
        status=302,
        redirect=f"{site}/wishlist/",
        cookies=[
            {
                "name": "mage-messages",
                "value": "White%20desk%20has%20been%20added%20to%20your%20wish%20list.",
            }
        ],
    )


def _task(task_id, revision=2):
    return BrowserTask(
        task_id=f"webarena-{task_id}",
        title=f"Official task {task_id}",
        tool_calls=(),
        assertions=(),
        tags=(),
        difficulty="simple",
        ground_truth={"expected_actions": []},
        source={"url": f"https://example.test/{task_id}.trace.zip"},
        evaluator={"kind": "webarena_verified", "task_id": task_id, "revision": revision},
        environment={"kind": "webarena_verified"},
    )


def _evidence(tmp_path, entries, *, task_type="mutate", status="SUCCESS", data=None,
              process_passed=True, name="network.har"):
    har = tmp_path / name
    har.write_text(
        json.dumps({"log": {"version": "1.2", "creator": {"name": "test", "version": "1"},
                            "entries": entries}}),
        encoding="utf-8",
    )
    return TaskEvidence(
        outputs={},
        process_passed=process_passed,
        agent_response={"task_type": task_type, "status": status, "retrieved_data": data},
        network_har_path=har,
    )


def _evaluate(tmp_path, environments, task, evidence):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"environments": environments}), encoding="utf-8")
    return WebArenaVerifiedEvaluator(config_path=config).evaluate(task, evidence)


def test_official_webarena_verified_evaluator_accepts_programmatic_evidence(tmp_path):
    result = _evaluate(
        tmp_path,
        {"__SHOPPING_ADMIN__": {"urls": ["http://shopping-admin.test/admin"]}},
        _task(4),
        _evidence(
            tmp_path,
            [_entry("http://shopping-admin.test/admin", text="ok")],
            task_type="retrieve",
            data=["Impulse Duffle", "Overnight Duffle", "Hawkeye Yoga Short-32-Blue"],
        ),
    )

    assert (result.status, result.score) == ("pass", 1.0)


def test_webarena_evaluator_fails_closed_on_empty_har_without_trace_fallback(tmp_path):
    result = _evaluate(
        tmp_path,
        {"__SHOPPING_ADMIN__": {"urls": ["http://shopping-admin.test/admin"]}},
        _task(4),
        _evidence(tmp_path, [], task_type="retrieve", data=[], process_passed=False),
    )

    assert result.status == "error"
    assert result.error == "network.har evidence contains no entries"


@pytest.mark.parametrize(
    ("directions_engine", "expected_status"),
    [("fossgis_osrm_car", "pass"), ("fossgis_osrm_bicycle", "fail")],
)
def test_official_task_760_checks_the_hoboken_car_route(
    tmp_path, directions_engine, expected_status
):
    route = "/route/v1/driving/-75.44225386838299,40.651163100000005;-74.0323752,40.7433066"
    entries = [
        _entry("http://map.test/directions", headers=_NAVIGATE_HEADERS),
        _entry(
            f"http://map.test{route}",
            headers=[
                {
                    "name": "Cookie",
                    "value": f"_osm_directions_engine={directions_engine}; [REDACTED]",
                }
            ],
            mime="application/json",
            text="{}",
        ),
    ]
    result = _evaluate(
        tmp_path,
        {
            "__MAP__": {"urls": ["http://map.test"]},
            "__SHOPPING_ADMIN__": {"urls": ["http://shopping-admin.test"]},
        },
        _task(760, revision=6),
        _evidence(tmp_path, entries, task_type="navigate"),
    )

    assert result.status == expected_status


def test_official_task_678_checks_the_order_grid_xhr(tmp_path):
    query = (
        "namespace=sales_order_grid&filters%5Bplaceholder%5D=true&"
        "filters%5Bstatus%5D=canceled&search=&keywordUpdated=false"
    )
    orders = "http://shopping-admin.test/admin/sales/order/"
    entries = [
        _entry(orders, headers=_NAVIGATE_HEADERS),
        _entry(
            f"http://shopping-admin.test/admin/mui/index/render/?{query}",
            headers=[{"name": "Referer", "value": orders}],
            mime="application/json",
            text="{}",
        ),
    ]
    result = _evaluate(
        tmp_path,
        {"__SHOPPING_ADMIN__": {"urls": ["http://shopping-admin.test/admin"]}},
        _task(678, revision=3),
        _evidence(tmp_path, entries, task_type="navigate"),
    )

    assert result.status == "pass"


@pytest.mark.parametrize(
    ("assignee_ids", "expected_status"),
    [([2330], "pass"), ([2331], "fail")],
)
def test_official_task_811_compares_singleton_array_as_an_array(
    tmp_path, assignee_ids, expected_status
):
    entry = _entry(
        "http://gitlab.test/a11yproject/a11yproject.com/-/issues/1478.json",
        method="PUT",
        headers=[{"name": "content-type", "value": "application/json"}],
        post={
            "mimeType": "application/json",
            "text": json.dumps({"issue": {"assignee_ids": assignee_ids}}),
        },
        mime="application/json",
        text="{}",
    )
    result = _evaluate(
        tmp_path,
        {"__GITLAB__": {"urls": ["http://gitlab.test"]}},
        _task(811, revision=3),
        _evidence(tmp_path, [entry]),
    )

    assert result.status == expected_status
    assert result.checks[1]["status"] == ("success" if expected_status == "pass" else "failure")


@pytest.mark.parametrize(
    ("retrieved_data", "expected_status"),
    [(None, "pass"), ([0], "fail")],
)
def test_official_task_319_preserves_null_retrieved_data_key(
    tmp_path, retrieved_data, expected_status
):
    result = _evaluate(
        tmp_path,
        {"__SHOPPING__": {"urls": ["http://shopping.test"]}},
        _task(319),
        _evidence(
            tmp_path,
            [_entry("http://shopping.test/sales/order/history/", text="ok")],
            task_type="retrieve",
            status="NOT_FOUND_ERROR",
            data=retrieved_data,
        ),
    )

    assert result.status == expected_status


def test_official_task_604_binds_dynamic_forum_id_from_the_submitted_form(tmp_path):
    entry = _form_entry(
        "http://reddit.test/submit",
        {
            "submission[title]": "what is the SOTA web navigation agent repo",
            "submission[forum]": "10043",
        },
    )
    result = _evaluate(
        tmp_path,
        {"__REDDIT__": {"urls": ["http://reddit.test"]}},
        _task(604),
        _evidence(tmp_path, [entry]),
    )

    assert result.status == "pass"


@pytest.mark.parametrize(
    ("comment_key", "expected_status"),
    [("reply_to_submission_69[comment]", "pass"), ("reply_to_submission_70[comment]", "fail")],
)
def test_official_task_611_requires_one_post_id_in_comment_url_and_form_key(
    tmp_path, comment_key, expected_status
):
    entries = [
        _form_entry(
            "http://reddit.test/submit",
            {"submission[title]": "Harry Potter", "submission[forum]": "10037"},
        ),
        _form_entry("http://reddit.test/f/books/69/-/comment", {comment_key: "Wonderful journey"}),
    ]
    result = _evaluate(
        tmp_path,
        {"__REDDIT__": {"urls": ["http://reddit.test"]}},
        _task(611),
        _evidence(tmp_path, entries),
    )

    assert result.status == expected_status


def test_evaluator_registry_follows_runtime_config_rewritten_by_slice_reset(tmp_path):
    """Task 514 reads the safe flash cookie; a slice reset rewrites the site domain."""
    config = tmp_path / "config.json"
    registry = EvaluatorRegistry(config_path=config)

    for generation in (1, 2):
        site = f"http://slice-generation-{generation}.test"
        config.write_text(
            json.dumps({"environments": {"__SHOPPING__": {"urls": [site]}}}), encoding="utf-8"
        )
        evidence = _evidence(tmp_path, [_wishlist_entry(site)], name=f"network-{generation}.har")
        result = registry.evaluate(_task(514), evidence)
        assert (generation, result.status) == (generation, "pass"), result.error
