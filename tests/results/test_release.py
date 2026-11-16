"""Release controls use isolated synthetic repositories, never real software tags."""

import runpy
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_release_requires_matching_metadata_and_exact_tag(tmp_path, monkeypatch):
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
