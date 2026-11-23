"""Compare fetch backends behind a coding agent (Claude Code) on the Web Fetch tasks.

The experiment runs the real ``claude`` CLI as a subprocess, so it works from any shell,
CI job or session; it never reads the session that launches it.
"""
