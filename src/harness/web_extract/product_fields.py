"""Report and analysis declarations of the fixed-field (single-schema) benchmark.

The benchmark adapter in ``dataset.py`` declares the fields and schema; that file
is hash-bound into frozen cohort plans, so the report-side additions live here.
Shared report and analysis code reads these names instead of spelling fields out.
"""

from harness.web_extract.dataset import fixed_field_benchmark

BENCHMARK = fixed_field_benchmark()
# Fields the method returns, in report column order.
DECLARED = BENCHMARK.fields
# Upstream-derived fields that carry their own score, with the declared field each derives from.
REPORTED_DERIVED = {"OutOfStock": "availability"}
# Every field the upstream scorer scores, declared and derived.
SCORED = (*DECLARED, *BENCHMARK.derived_fields)
# Every scored field a report shows.
REPORTED = (*DECLARED, *REPORTED_DERIVED)
# Declared fields whose schema admits numbers; the upstream scorer matches them as decimals.
NUMERIC = frozenset(field for field in DECLARED
                    if "number" in BENCHMARK.schema["properties"][field]["type"])


def label_key(prefix, field):
    """Catalog key of one field label, such as ``field_labels_price``."""
    return prefix + "_" + field
