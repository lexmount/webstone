import json
import math
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from harness.lexbench import charts as MODULE
from tests.test_explicit_utf8_io import ascii_locale_environment


class MoliVersionResourceChartTests(unittest.TestCase):
    def test_registered_method_without_resource_measurement_has_a_clear_error(self):
        historical = {**self.data, "baseline": "114_off"}
        methods = [
            {**item, "final_passed_cases": item["passed_cases"]}
            for item in historical["configurations"]
        ]
        methods.append({"id": "unmeasured", "final_passed_cases": 1})
        with self.assertRaisesRegex(ValueError, "resource.*unmeasured"):
            MODULE.combine_comparisons(methods, historical, None)

    def test_linux_release_results_use_one_baseline_and_keep_point_labels(self):
        historical = {**self.data, "baseline": "114_off"}
        row = {
            **self.data["configurations"][0],
            "id": "119_on",
            "label": "Moli 1.1.9 修复版 · 开启 layout",
            "label_en": "Moli 1.1.9 patched · Layout on",
            "platform": "Linux",
            "memory_kind": "PSS",
            "pss_mib": self.data["configurations"][0]["pss_mib"] * 1.1,
            "cpu_ms": self.data["configurations"][0]["cpu_ms"] * 0.8,
            "passed_cases": 360,
        }
        methods = [
            {**item, "final_passed_cases": item["passed_cases"]}
            for item in historical["configurations"] + [row]
        ]
        combined = MODULE.combine_comparisons(methods, historical, {"configurations": [row]})
        self.assertAlmostEqual(combined["configurations"][-1]["memory_change_pct"], 10)
        self.assertAlmostEqual(combined["configurations"][-1]["cpu_change_pct"], -20)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "linux.svg"
            MODULE.render_chart(combined, output, ("memory", "cpu"))
            chart = output.read_text(encoding="utf-8")
            self.assertEqual(chart.count("data-point-label="), 8)
            self.assertIn("pp / +10.00%", chart)
            self.assertIn("pp / -20.00%", chart)
            self.assertIn("1.1.9 修复版 ✦", chart)
            self.assertNotIn("RSS", chart)
            self.assertNotIn("不同执行环境", chart)

    def test_chart_marks_retained_success_from_a_different_resource_build(self):
        row = self.data["configurations"][1]
        row.update(functional_binary_sha256="a" * 64, resource_binary_sha256="b" * 64)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "builds.svg"
            for language in ("zh", "en"):
                for metrics in (("pss",), ("pss", "cpu")):
                    MODULE.render_chart(self.data, output, metrics, language)
                    rendered = output.read_text(encoding="utf-8")
                    self.assertIn(" †:", rendered)
                    self.assertIn("不同构建" if language == "zh" else "different builds", rendered)
            row["resource_binary_sha256"] = row["functional_binary_sha256"]
            MODULE.render_chart(self.data, output)
            self.assertNotIn("†", output.read_text(encoding="utf-8"))

    def test_chart_command_reads_and_writes_utf8_under_an_ascii_locale(self):
        row = self.data["configurations"][1]
        row.update(functional_binary_sha256="a" * 64, resource_binary_sha256="b" * 64)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "comparison.json"
            source.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            output = Path(directory) / "chart.svg"
            subprocess.run(
                [sys.executable, "-m", "harness.lexbench", "chart",
                 "--input", str(source), "--output", str(output), "--metrics", "pss,cpu"],
                env=ascii_locale_environment(), check=True, capture_output=True,
            )
            rendered = output.read_text(encoding="utf-8")
            self.assertIn(" †:", rendered)
            self.assertIn("Moli 1.1.4 · 关闭 layout", rendered)

    def test_near_identical_methods_keep_both_numbered_markers_visible(self):
        self.data["configurations"][1]["pss_mib"] = self.data["configurations"][0]["pss_mib"]
        self.data["configurations"][1]["success_rate_pct"] = 0.01
        svg = self.render(("pss",))
        points = re.findall(r'<circle cx="([^"]+)" cy="([^"]+)" r="12"', svg)
        self.assertEqual(len(points), 3)
        a, b = [(float(x), float(y)) for x, y in points[:2]]
        self.assertGreaterEqual(math.dist(a, b), 27)
        self.assertIn('stroke-width="1.5"', svg)

    def test_combined_chart_keeps_registry_order_and_baseline_without_cross_platform_deltas(self):
        historical = {**self.data, "baseline": "114_off"}
        mac = {"configurations": []}
        for i, passed in enumerate((1, 360, 255, 1)):
            row = dict(self.data["configurations"][0])
            row.update(
                id=f"mac_{i}",
                passed_cases=passed,
                rss_mib=100 + i,
                label=f"mac {i}",
                label_en=f"mac {i}",
            )
            mac["configurations"].append(row)
        sources = historical["configurations"] + mac["configurations"]
        methods = [{**r, "final_passed_cases": r["passed_cases"]} for r in sources]
        combined = MODULE.combine_comparisons(methods, historical, mac)
        self.assertEqual(combined["baseline"], "114_off")
        self.assertEqual(len({r["color"] for r in combined["configurations"]}), 7)
        self.assertEqual([r["id"] for r in combined["configurations"]], [r["id"] for r in methods])
        for row in combined["configurations"][3:]:
            self.assertIsNone(row["memory_change_pct"])
            self.assertIsNone(row["cpu_change_pct"])
            self.assertEqual(row["memory_kind"], "RSS")
        self.assertAlmostEqual(combined["configurations"][4]["success_change_pp"], 360 / 372 * 100)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "all.svg"
            for language in ("zh", "en"):
                MODULE.render_chart(combined, output, ("memory", "cpu"), language)
                text = output.read_text(encoding="utf-8")
                self.assertIn("Linux PSS", text)
                self.assertIn("macOS RSS", text)
                self.assertIn("360/372", text)
                self.assertIn("+96.77", text)
                self.assertIn("pp / —", text)
                self.assertEqual(text.count("data-point-label="), 14)
                self.assertIn("1.1.8 ✦", text)
                self.assertNotIn("MiB (+", text)
                self.assertEqual(text.count('<path d="M '), 8)
            for metric in ("memory", "cpu"):
                MODULE.render_chart(combined, output, (metric,), "en")
                text = output.read_text(encoding="utf-8")
                self.assertIn("360/372", text)
                self.assertEqual(text.count('<path d="M '), 4)
                self.assertIn("MiB" if metric == "memory" else "ms", text)
                self.assertNotIn("21.41 ms" if metric == "memory" else "77.22 MiB", text)

    def setUp(self):
        self.data = {
            "resource_population": {
                "chart_calls": 1045,
                "chart_tasks": 209,
                "total_tasks": 557,
                "calls_per_task": 5,
            },
            "configurations": [
                {
                    "id": "114_off",
                    "label": "Moli 1.1.4 · 关闭 layout",
                    "label_en": "Moli 1.1.4 · layout off",
                    "short_label": "1.1.4 关闭",
                    "short_label_en": "1.1.4 off",
                    "passed_cases": 0,
                    "total_cases": 372,
                    "success_rate_pct": 0,
                    "pss_mib": 77.216796875,
                    "pss_change_pct": 0,
                    "cpu_ms": 21.406698564593302,
                    "cpu_change_pct": 0,
                },
                {
                    "id": "118_off",
                    "label": "Moli 1.1.8 · 关闭 layout",
                    "label_en": "Moli 1.1.8 · layout off",
                    "short_label": "1.1.8 关闭",
                    "short_label_en": "1.1.8 off",
                    "passed_cases": 1,
                    "total_cases": 372,
                    "success_rate_pct": 0.2688172043010753,
                    "pss_mib": 77.1015625,
                    "pss_change_pct": -0.1492348551916045,
                    "cpu_ms": 21.320574162679424,
                    "cpu_change_pct": -0.4023245417970611,
                },
                {
                    "id": "118_on",
                    "label": "Moli 1.1.8 · 开启 layout",
                    "label_en": "Moli 1.1.8 · layout on",
                    "short_label": "1.1.8 开启",
                    "short_label_en": "1.1.8 on",
                    "passed_cases": 255,
                    "total_cases": 372,
                    "success_rate_pct": 68.54838709677419,
                    "pss_mib": 80.537109375,
                    "pss_change_pct": 4.299987352978363,
                    "cpu_ms": 21.6555023923445,
                    "cpu_change_pct": 1.1622708985248087,
                },
            ],
        }

    def render(self, metrics=("pss", "cpu"), language="zh"):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "chart.svg"
            MODULE.render_chart(self.data, output, metrics, language)
            return output.read_text(encoding="utf-8")

    def test_panels_label_absolute_values_baseline_changes_and_all_legend_entries(self):
        # Numeric strings are accepted after validation.
        self.data["configurations"][0]["pss_mib"] = "77.216796875"
        self.data["configurations"][0]["cpu_ms"] = "21.406698564593302"
        svg = self.render()
        for expected in ("77.22 MiB (+0.00%)", "77.10 MiB (-0.15%)", "80.54 MiB (+4.30%)",
                         "21.41 ms (+0.00%)", "21.32 ms (-0.40%)", "21.66 ms (+1.16%)"):
            self.assertIn(expected, svg)
        svg = self.render(language="en")
        for expected in ("Resource experiment: 557 tasks × 5 calls",
                         "Median peak process-tree PSS (MiB)", "Mean CPU time per execution (ms)",
                         "80.54 MiB (+4.30%)", "21.66 ms (+1.16%)"):
            self.assertIn(expected, svg)
        for metric, language, title, absent in (
            ("pss", "en", "Median peak process-tree PSS (MiB)", ("CPU", "21.41 ms")),
            ("cpu", "en", "Mean CPU time per execution (ms)", ("PSS", "77.22 MiB")),
            ("pss", "zh", None, ("21.41 ms",)),
            ("cpu", "zh", "单次执行平均 CPU 时间（ms）", ("PSS",)),
        ):
            with self.subTest(metric=metric, language=language):
                svg = self.render((metric,), language)
                if title:
                    self.assertIn(title, svg)
                labels = (("1.1.4 off", "1.1.8 off", "1.1.8 on") if language == "en"
                          else ("1.1.4 关闭", "1.1.8 关闭", "1.1.8 开启"))
                for label in labels:
                    self.assertIn(label, svg)
                self.assertNotIn("Moli 1.1.8 · layout on:", svg)
                self.assertNotIn("Moli 1.1.8 · 开启 layout:", svg)
                for text in absent:
                    self.assertNotIn(text, svg)

    def test_macos_rss_cpu_chart_does_not_label_memory_as_pss(self):
        for row in self.data["configurations"]:
            row["rss_mib"] = row.pop("pss_mib")
            row["rss_change_pct"] = row.pop("pss_change_pct")
        for language in ("zh", "en"):
            with self.subTest(language=language):
                svg = self.render(("rss", "cpu"), language)
                self.assertIn("RSS", svg)
                self.assertIn("CPU", svg)
                self.assertNotIn("PSS", svg)
                self.assertIn("80.54 MiB (+4.30%)", svg)
                self.assertIn("21.66 ms (+1.16%)", svg)
                memory_only = self.render(("rss",), language)
                self.assertIn("RSS", memory_only)
                self.assertIn("80.54 MiB (+4.30%)", memory_only)
                self.assertNotIn("PSS", memory_only)
                self.assertNotIn("CPU", memory_only)

    def test_y_axis_expands_to_include_rates_above_eighty_percent(self):
        self.data["configurations"][2]["success_rate_pct"] = 95
        svg = self.render()
        self.assertIn(">100%</text>", svg)
        point = re.search(r'<circle cx="[^"]+" cy="([^"]+)" r="12" fill="#7652c7"', svg)
        self.assertIsNotNone(point)
        self.assertGreaterEqual(float(point.group(1)), 145)

    def test_future_configuration_is_rendered_without_code_changes(self):
        future = dict(self.data["configurations"][2])
        future.update(
            {
                "id": "120_on",
                "label": "Moli 1.2.0 · 开启 layout",
                "label_en": "Moli 1.2.0 · layout on",
                "short_label": "1.2.0 开启布局",
                "short_label_en": "1.2.0 layout on",
                "passed_cases": 300,
                "success_rate_pct": 300 / 372 * 100,
                "color": "#2775c9",
            }
        )
        self.data["configurations"].append(future)
        svg = self.render()
        self.assertIn("4 种配置", svg)
        self.assertIn("Moli 1.2.0 · 开启 layout", svg)
        self.assertIn("300/372 (80.6%)", svg)

    def test_invalid_chart_input_has_a_named_error(self):
        def population(field, value=None, delete=False):
            def change(data):
                if delete:
                    del data["resource_population"][field]
                else:
                    data["resource_population"][field] = value
            return change

        def row(index, field, value=None, delete=False):
            def change(data):
                if delete:
                    del data["configurations"][index][field]
                else:
                    data["configurations"][index][field] = value
            return change

        def every_row(field, value):
            def change(data):
                for item in data["configurations"]:
                    item[field] = value
            return change

        def clear(data):
            data["configurations"] = []

        cases = [
            (row(0, "total_cases", 371), "same positive total_cases", None),
            (population("chart_tasks", delete=True),
             "resource_population missing required field.*chart_tasks", None),
            *((population("total_tasks", value),
               "resource_population field total_tasks must be a positive integer", None)
              for value in ("557", 557.0, 0, True)),
            (clear, "configurations must contain at least one row", None),
            (row(0, "pss_change_pct", delete=True),
             "configuration row 0 missing required field.*pss_change_pct", None),
            (row(0, "color", '#fff" onload="alert(1)'), "color must be a six-digit hex color", None),
            (every_row("total_cases", "372.5"), "integer total_cases", None),
            (row(1, "id", "114_off"), "configuration ids must be unique", None),
            (row(0, "passed_cases", 0.5), "integer passed_cases", None),
            (row(0, "pss_mib", float("nan")), "field pss_mib must be finite", None),
            (lambda data: None, "duplicates", ("pss", "pss")),
        ]
        original = json.dumps(self.data)
        for change, message, metrics in cases:
            with self.subTest(message=message):
                self.data = json.loads(original)
                change(self.data)
                with self.assertRaisesRegex(ValueError, message):
                    self.render(metrics or ("pss", "cpu"))


if __name__ == "__main__":
    unittest.main()
