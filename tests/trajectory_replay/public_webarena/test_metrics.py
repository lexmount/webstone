from __future__ import annotations

from harness.trajectory_replay.public_webarena.metrics import summarize_attempts


def test_summary_keeps_functional_scores_separate_and_uses_pass_intersection():
    rows = [
        {
            "engine": "chromium",
            "task_id": "a",
            "complexity": "step_01_05",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 10,
            "resource": {"cpu_ms": 5, "memory_peak_bytes": 100, "network_rx_bytes": 20},
        },
        {
            "engine": "moli",
            "task_id": "a",
            "complexity": "step_01_05",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 8,
            "resource": {"cpu_ms": 2, "memory_peak_bytes": 50, "network_rx_bytes": 18},
        },
        {
            "engine": "chromium",
            "task_id": "b",
            "complexity": "step_06_10",
            "attempt": 1,
            "status": "pass",
            "duration_ms": 7,
            "resource": {"cpu_ms": 3, "memory_peak_bytes": 90, "network_rx_bytes": 10},
        },
        {
            "engine": "moli",
            "task_id": "b",
            "complexity": "step_06_10",
            "attempt": 1,
            "status": "fail",
            "duration_ms": 6,
            "error": "timeout",
            "resource": {"cpu_ms": 1, "memory_peak_bytes": 40, "network_rx_bytes": 9},
        },
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    assert summary["functional"]["chromium"]["passes"] == 2
    assert summary["functional"]["moli"]["passes"] == 1
    assert summary["resource_comparison"]["intersection_attempts"] == 1
    assert summary["resource_comparison"]["by_engine"]["chromium"]["cpu_ms"]["median"] == 5
    assert summary["resource_comparison"]["by_engine"]["moli"]["cpu_ms"]["median"] == 2
    assert summary["resource_comparison"]["by_engine"]["chromium"]["duration_ms"]["median"] == 10
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["median"] == 8
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["p50"] == 8
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["mean"] == 8
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["p95"] == 8
    assert summary["resource_comparison"]["paired_ratios"]["metrics"]["duration_ms"]["p50"] == 0.8
    assert summary["resource_comparison"]["paired_ratios"]["metrics"]["duration_ms"]["values"] == [
        {"task_id": "a", "denominator": 10.0, "numerator": 8.0, "ratio": 0.8}
    ]
    assert summary["resource_comparison"]["paired_ratios"]["p95_method"] == (
        "nearest-rank: sorted_values[ceil(0.95*n)-1]"
    )
    relative = summary["resource_comparison"]["relative_ratios"]
    assert relative["baseline_engine"] == "chromium"
    assert relative["by_engine"]["moli"]["duration_ms"]["p50"] == 0.8
    assert summary["by_complexity"]["step_01_05"]["functional"]["moli"]["passes"] == 1
    assert (
        summary["by_complexity"]["step_06_10"]["resource_comparison"]["intersection_attempts"] == 0
    )
    assert summary["by_task"]["a"]["functional"]["chromium"]["passes"] == 1
    assert summary["by_task"]["b"]["representative_errors"]["moli"] == ["timeout"]


def test_summary_builds_per_task_ratios_against_one_baseline_for_multiple_engines():
    rows = [
        {
            "engine": engine,
            "task_id": "a",
            "complexity": "simple",
            "attempt": 1,
            "status": "pass",
            "result_status": "pass",
            "duration_ms": duration,
            "resource": {"cpu_ms": cpu},
        }
        for engine, duration, cpu in (
            ("chromium", 10, 4),
            ("moli", 5, 2),
            ("webkit", 20, 8),
        )
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli", "webkit"))

    relative = summary["resource_comparison"]["relative_ratios"]
    assert relative["baseline_engine"] == "chromium"
    assert relative["by_engine"]["moli"]["duration_ms"]["p50"] == 0.5
    assert relative["by_engine"]["webkit"]["duration_ms"]["p50"] == 2.0
    assert summary["resource_comparison"]["paired_ratios"]["metrics"] == {}




def test_summary_separates_first_pass_retry_recovery_and_final_outcome():
    rows = [
        {
            "engine": "chromium",
            "task_id": "stable",
            "complexity": "simple",
            "attempt": 1,
            "is_retry": False,
            "status": "pass",
            "duration_ms": 10,
            "resource": {},
        },
        {
            "engine": "moli",
            "task_id": "stable",
            "complexity": "simple",
            "attempt": 1,
            "is_retry": False,
            "status": "fail",
            "duration_ms": 9,
            "resource": {},
        },
        {
            "engine": "moli",
            "task_id": "stable",
            "complexity": "simple",
            "attempt": 2,
            "is_retry": True,
            "status": "pass",
            "duration_ms": 11,
            "resource": {},
        },
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    assert summary["functional"]["chromium"]["first_passes"] == 1
    assert summary["functional"]["moli"]["first_passes"] == 0
    assert summary["functional"]["moli"]["recovered"] == 1
    assert summary["functional"]["moli"]["final_passes"] == 1
    assert summary["functional"]["moli"]["physical_attempts"] == 2
    assert summary["resource_comparison"]["intersection_results"] == 1
    assert summary["resource_comparison"]["by_engine"]["moli"]["duration_ms"]["p50"] == 11


def test_summary_uses_last_attempt_not_best_attempt_for_final_result():
    rows = [
        {
            "engine": engine,
            "task_id": "task",
            "attempt": attempt,
            "is_retry": attempt == 2,
            "status": status,
            "result_status": status,
            "combined_status": status,
            "duration_ms": 100,
            "execution_duration_ms": 10,
            "resource": {},
        }
        for engine in ("chromium", "moli")
        for attempt, status in ((1, "pass"), (2, "fail"))
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    assert summary["functional"]["chromium"]["result_final_passes"] == 0
    assert summary["functional"]["moli"]["result_final_passes"] == 0


def test_resource_comparison_excludes_environment_preparation_time():
    rows = [
        {
            "engine": engine,
            "task_id": "task",
            "attempt": 1,
            "is_retry": False,
            "status": "pass",
            "result_status": "pass",
            "combined_status": "pass",
            "duration_ms": wall,
            "execution_duration_ms": execution,
            "resource": {},
        }
        for engine, wall, execution in (
            ("chromium", 1000, 100),
            ("moli", 9000, 50),
        )
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    by_engine = summary["resource_comparison"]["by_engine"]
    assert by_engine["chromium"]["duration_ms"]["p50"] == 100
    assert by_engine["moli"]["duration_ms"]["p50"] == 50
    assert summary["resource_comparison"]["paired_ratios"]["metrics"]["duration_ms"][
        "p50"
    ] == 0.5


def test_summary_reports_result_and_combined_correctness_independently():
    rows = [
        {
            "engine": engine,
            "task_id": "multi-path",
            "complexity": "medium",
            "attempt": 1,
            "status": "pass",
            "result_status": "pass",
            "process_status": "fail" if engine == "moli" else "pass",
            "combined_status": "fail" if engine == "moli" else "pass",
            "duration_ms": 10,
            "resource": {"cpu_ms": 1},
        }
        for engine in ("chromium", "moli")
    ]

    summary = summarize_attempts(rows, engines=("chromium", "moli"))

    assert summary["functional"]["moli"]["result_first_passes"] == 1
    assert summary["functional"]["moli"]["combined_first_passes"] == 0
    assert summary["resource_comparison"]["intersection_attempts"] == 1
