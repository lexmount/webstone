"""Large literal masks preserve record ownership without quadratic cleanup."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("identifier", [
    "named-link-code-mask-valid-sibling",
    "named-link-code-mask-invalid-prefix",
])
def test_large_code_mask_scores_within_bounded_time(identifier):
    code = """
import sys
from lexbench_webfetch.metrics import task_passed
from lexbench_webfetch.scoring import score_attempt
from lexbench_webfetch.scoring_regressions import load_scoring_regressions

control = load_scoring_regressions()[sys.argv[1]]
artifact = control['artifact'].replace('payload', 'x' * 300000).encode()
metrics = score_attempt(control['case'], artifact, ok=control['ok'])
assert task_passed(control['ok'], metrics, 'body') is control['expected_task_pass']
"""
    subprocess.run([sys.executable, "-c", code, identifier], check=True, timeout=10)
