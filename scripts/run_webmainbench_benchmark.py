"""Kept because frozen latency certifiers name it: ``webstone run --benchmark webmainbench``."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.web_fetch.cli import main

if __name__ == "__main__":
    main(["run", "--benchmark", "webmainbench", *sys.argv[1:]])
