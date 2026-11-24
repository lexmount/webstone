"""Lexbench evidence, resource comparisons and report adapter."""

from harness.environments.browser import LAYOUT_PUBLICATION, engines_with


def layout_engine() -> str:
    """The one registered browser whose layout publication modes Lexbench measures."""
    engines = engines_with(LAYOUT_PUBLICATION)
    if len(engines) != 1:
        raise RuntimeError("Lexbench requires exactly one layout-publishing browser adapter")
    return engines[0]
