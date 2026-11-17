"""Require a tested, immutable software tag before distributing a PyPI release."""

import os
import re
import subprocess
import tomllib
from pathlib import Path


def main() -> None:
    tag = os.environ.get("RELEASE_TAG", "")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise SystemExit("Publish an explicit vMAJOR.MINOR.PATCH software release.")
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    lock = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
    name, version = metadata["project"]["name"], metadata["project"]["version"]
    packages = [item for item in lock["package"] if item["name"] == name]
    if name != "webstone" or tag != f"v{version}" or len(packages) != 1:
        raise SystemExit("The software tag, project name and locked package must agree.")
    if packages[0]["version"] != version:
        raise SystemExit("Refresh the lock for the approved software version.")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    tagged = subprocess.check_output(["git", "rev-parse", f"{tag}^{{commit}}"], text=True).strip()
    if head != tagged:
        raise SystemExit("Build the exact tagged commit, not a moving branch.")
    subprocess.run(["git", "merge-base", "--is-ancestor", head, "origin/main"], check=True)
    print(f"Verified {name} {version} at {head}")


if __name__ == "__main__":
    main()
