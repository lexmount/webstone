from harness.distribution import source_path

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.headless_runner import require_expected_provider_versions
from harness.reporting.publication_target import (
    require_publication_site,
    require_publication_version,
)
from scripts.check_track_naming import repository_contract_findings

ROOT = Path(__file__).resolve().parents[1]


class RuntimeEntrypointTests(unittest.TestCase):
    def test_catalog_replay_rejects_specialized_modules_schemas_targets_and_repairs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scripts = root / "scripts"
            scripts.mkdir()
            (scripts / "run_webarena_full_chromium.py").write_text("", encoding='utf-8')
            (scripts / "repair_webarena_case_42.py").write_text("", encoding='utf-8')
            harness = source_path("harness", root=root) / "trajectory_replay"
            harness.mkdir(parents=True)
            (harness / "full_catalog.py").write_text("", encoding='utf-8')
            (harness / "writer.py").write_text(
                'SCHEMA = "lex_webarena_verified_full_replay_run/3"\n',
                encoding='utf-8'
            )
            (root / "Makefile").write_text("webarena-full-release-check:\n\ttrue\n", encoding='utf-8')

            findings = "\n".join(repository_contract_findings(root))

            self.assertIn("run_webarena_full_chromium.py", findings)
            self.assertIn("repair_webarena_case_42.py", findings)
            self.assertIn("full_catalog.py", findings)
            self.assertIn("legacy full schemas are read-only", findings)
            self.assertIn("must not encode one dataset size", findings)

    def test_repository_pins_python_and_uv(self) -> None:
        self.assertEqual((ROOT / ".python-version").read_text(encoding='utf-8').strip(), "3.11")
        self.assertTrue((ROOT / "uv.lock").is_file())
        makefile = (ROOT / "Makefile").read_text(encoding='utf-8')
        self.assertIn("$(UV) run --frozen python", makefile)
        self.assertIn("$(UV) sync --frozen --all-extras", makefile)
        self.assertIn('open(".python-version")', makefile)

    def test_versioned_moli_slots_reject_wrong_or_missing_binaries(self) -> None:
        valid = {
            "moli-1.0.6": {"reported_version": "moli 1.0.6"},
            "moli": {"reported_version": "moli 1.1.5"},
            "moli-1.1.3": {"reported_version": "moli 1.1.3"},
        }
        configs = {name: {'expected_version': version} for name, version in (
            ('moli-1.0.6', '1.0.6'), ('moli', '1.1.5'), ('moli-1.1.3', '1.1.3'))}
        require_expected_provider_versions(valid, configs)
        for value in (None, "moli 1.1.3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                require_expected_provider_versions({
                    **valid,
                    "moli": {"reported_version": value},
                }, configs)

    def test_web_fetch_docs_do_not_recommend_system_python(self) -> None:
        for relative in ("README.md", "harness/web_fetch/README.md", "harness/web_fetch/HEADLESS.md"):
            text = source_path(relative, root=ROOT).read_text(encoding='utf-8')
            direct = re.findall(r"(?m)^\s*(?:python|python3)(?:\s|$)", text)
            self.assertEqual(direct, [], relative)
            unfrozen = re.findall(r"(?m)^\s*uv run (?!--frozen\b)", text)
            self.assertEqual(unfrozen, [], relative)

    def test_dsh_worker_uses_execution_result_contract_and_fails_non_2xx(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("Node is required for worker execution tests")
        capability = subprocess.run(
            [node, "--input-type=module", "-e",
             "import {stripTypeScriptTypes} from 'node:module'"],
            capture_output=True, timeout=10,
        )
        if capability.returncode:
            self.skipTest("Worker execution tests require Node with native TypeScript erasure")
        subprocess.run(
            [node, "--experimental-vm-modules", str(ROOT / "tests/test_dsh_worker.mjs"),
             str(source_path("harness/web_fetch/workers/dsh_webfetch_worker.mts", root=ROOT))],
            capture_output=True, text=True, check=True, timeout=30,
        )

    def test_publication_receipt_must_target_selected_instance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "report.json"
            for slug in ("report-one", "report-two"):
                profile.write_text(json.dumps({
                    "artifact_hub_base_url": "https://reports.example.org",
                    "artifact_hub_site": slug,
                }), encoding='utf-8')
                self.assertEqual(require_publication_site(
                    {"slug": slug}, profile, "https://reports.example.org"), slug)
                with self.assertRaisesRegex(ValueError, "expected"):
                    require_publication_site({"slug": "another-report"}, profile,
                                             "https://reports.example.org")

    def test_publication_site_has_no_default_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "report.json"
            profile.write_text(json.dumps({"artifact_hub_site": "report-one"}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, "explicit HTTPS base URL"):
                require_publication_site({"slug": "report-one"}, profile, "https://reports.example.org")

    def test_publication_version_defaults_to_profile_and_rejects_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile.json'
            profile.write_text(json.dumps({'artifact_hub_expected_version': 'ver_profile'}), encoding='utf-8')
            self.assertEqual(require_publication_version(profile), 'ver_profile')
            self.assertEqual(require_publication_version(profile, 'ver_profile'), 'ver_profile')
            with self.assertRaisesRegex(ValueError, 'expected'):
                require_publication_version(profile, 'ver_cli')

    def test_publication_version_fails_closed_when_profile_binding_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / 'profile.json'
            for value in (None, '', 'version-without-prefix', 7):
                profile.write_text(json.dumps({'artifact_hub_expected_version': value}), encoding='utf-8')
                with self.subTest(value=value), self.assertRaisesRegex(
                    ValueError, 'artifact_hub_expected_version'
                ):
                    require_publication_version(profile)

    def test_publication_owners_do_not_load_repository_eval_credentials(self) -> None:
        for relative in ('src/harness/web_fetch/report_publish.py',
                         'src/harness/web_fetch/publication_verify.py',
                         'scripts/rename_artifact_site.py'):
            self.assertNotIn('load_runtime_env', (ROOT/relative).read_text(encoding='utf-8'), relative)


if __name__ == "__main__":
    unittest.main()
