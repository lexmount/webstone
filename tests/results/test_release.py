"""Release controls use isolated synthetic repositories, never real software tags."""

import runpy
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_pypi_dependencies_keep_the_frozen_evaluator_out_of_public_url_metadata():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"] + [
        item for extra in project["project"]["optional-dependencies"].values() for item in extra
    ]
    assert not any(" @ " in item for item in dependencies)
    source = project["tool"]["uv"]["sources"]["webarena-verified"]
    evaluator = next(item for item in lock["package"] if item["name"] == "webarena-verified")
    assert evaluator["version"] == "1.2.3.post1.dev16"
    assert evaluator["source"]["git"] == f"{source['git']}?rev={source['rev']}#{source['rev']}"
    assert f"webarena-verified=={evaluator['version']}" in dependencies


def test_release_requires_matching_metadata_and_exact_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.DEVNULL)

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.com")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "webstone"\nversion = "0.7.0"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "webstone"\nversion = "0.7.0"\n', encoding="utf-8"
    )
    git("add", ".")
    git("commit", "-m", "Synthetic fixture")
    git("tag", "v0.7.0")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RELEASE_TAG", "v0.7.0")
    check = runpy.run_path(str(ROOT / "scripts/check_pypi_release.py"))["main"]
    check()
    monkeypatch.setenv("RELEASE_TAG", "v0.8.0")
    with pytest.raises(SystemExit, match="must agree"):
        check()
    monkeypatch.setenv("RELEASE_TAG", "v0.7.0")
    (tmp_path / "unreleased.txt").write_text("Synthetic later change", encoding="utf-8")
    git("add", ".")
    git("commit", "-m", "Later fixture")
    with pytest.raises(SystemExit, match="exact tagged commit"):
        check()
