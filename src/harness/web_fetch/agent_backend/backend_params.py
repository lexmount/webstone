"""Per-task-family request parameters for the MCP fetch backends.

The agent only supplies a URL. The harness decides every other request parameter from the task's family, with
the same rules the Web Fetch providers use, so a backend is configured exactly as it is in a provider run:

* ``moli``: ``MoliWebFetchProvider.native_cli_command``; dynamic tasks use ``--wait-until domstable``, other
  web tasks ``--wait-until done``. A task the native route cannot configure has no parameters.
* ``lexmount``: dynamic tasks request ``engine_preference: lightmount_domstable``. A task that declares
  per-task hints gets its own timeout, mirroring ``LexmountProvider.fetch``; a task that declares the uniform
  native marker (no per-task assistance) gets the family-level timeout, the caller budget. Other tasks send
  no options.
* ``builtin``: Claude Code's WebFetch has no request parameters.

Parameters are chosen by family, never by task id, host or expected answer.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pathlib
import subprocess

from lexbench_webfetch.models import EvalCase

from harness.distribution import repository_root
from harness.web_fetch.providers.http_client import inner_timeout_budget
from harness.web_fetch.providers.lexmount import LexmountProvider
from harness.web_fetch.providers.moli import MoliWebFetchProvider, _family_wait_until
from harness.web_fetch.retrieval_hints import plan_for_case

CALLER_BUDGET_SECONDS = 45  # the native caller budget the Web Fetch providers run under
_FIELDS = {f.name for f in dataclasses.fields(EvalCase)}


def to_case(case: dict) -> EvalCase:
    return EvalCase(**{k: v for k, v in case.items() if k in _FIELDS})


def moli_provider(binary: str | None = None) -> MoliWebFetchProvider:
    """The reference ``moli-webfetch`` provider configuration, pointed at the local binary."""
    binary = binary or os.environ.get("MOLI_BINARY", "moli")
    resolved = pathlib.Path(binary)
    config = json.loads((repository_root() / "configs" / "providers.example.json").read_text(encoding="utf-8"))["providers"]["moli-webfetch"]
    config = {k: (os.path.expandvars(v) if isinstance(v, str) else v) for k, v in config.items()}
    config.pop("native_dns_library", None)
    config["binary"] = str(resolved)
    config["expected_reported_version"] = subprocess.run([str(resolved), "--version"], capture_output=True,
                                                          text=True, timeout=30).stdout.strip()
    config["expected_binary_sha256"] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return MoliWebFetchProvider("moli-webfetch", config)


def params_for(arm_backend: str, case: dict, *, moli: MoliWebFetchProvider | None = None) -> dict:
    """Return the request parameters for one task, or raise ValueError if the backend cannot be configured."""
    if arm_backend == "builtin":
        return {}
    eval_case = to_case(case)
    if arm_backend == "moli":
        provider = moli or moli_provider()
        if eval_case.track == "document":
            # The provider picks a document route from ``input_media_kind``, which the current edition does not
            # declare. The document family therefore uses moli's automatic output (the form its document route uses
            # and the only one moli accepts for a PDF download), the same for every document task; whatever moli
            # returns, text or original bytes, is scored as is.
            config = provider.config
            argv = ["fetch", "--wait-until", _family_wait_until(config, "document")]
            if config.get("block_private_networks", True):
                argv.append("--block-private-networks")
            if config.get("log_level"):
                argv += ["--log-level", str(config["log_level"])]
            return {"argv": argv}
        command = provider.native_cli_command(eval_case)
        if command[-1] != eval_case.url:
            raise ValueError("unexpected moli command shape")
        return {"argv": command[1:-1]}
    if arm_backend == "lexmount":
        if eval_case.track != "dynamic":
            return {}
        family_timeout_ms = max(1, int(inner_timeout_budget(CALLER_BUDGET_SECONDS) * 1000))
        if (eval_case.retrieval_hints or {}).get("mode") == "native":  # uniform native family: no per-task assistance
            timeout_ms = family_timeout_ms
        else:
            plan = plan_for_case(eval_case)
            if plan is None:
                raise ValueError("invalid dynamic retrieval_hints")
            timeout_ms = min(plan.timeout_ms, family_timeout_ms)
        engine = LexmountProvider("lexmount", {}).config.get("dynamic_engine", "lightmount_domstable")
        return {"options": {"engine_preference": engine, "timeout_ms": timeout_ms}}
    raise ValueError(f"unknown backend {arm_backend}")


def ineligible(cases: dict, backends: list[str], *, moli: MoliWebFetchProvider | None = None) -> dict[str, str]:
    """Tasks that any selected backend cannot be configured for, with the reason. They are excluded for every arm."""
    result = {}
    for cid, case in cases.items():
        for backend in backends:
            try:
                params_for(backend, case, moli=moli)
            except ValueError as error:
                result[cid] = f"{backend}: {error}"
                break
    return result
