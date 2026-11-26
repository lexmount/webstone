#!/usr/bin/env python3
"""Thin entry point; the implementation lives in harness.web_fetch.agent_backend."""
from harness.web_fetch.agent_backend.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
