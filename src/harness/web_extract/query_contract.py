"""Independent, deterministic JSON-answer contracts and page/query composition.

This file is also the frozen standalone evaluator of a prepared query cohort.
It imports no browser, provider, credentials or execution pipeline.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal
from fractions import Fraction
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator, ValidationError, validators


def json_integer(checker, value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float, Decimal))
        and Decimal(str(value)).is_finite()
        and Decimal(str(value)) == Decimal(str(value)).to_integral_value()
    )


def decimal_numbers(value):
    """Interpret JSON decimal spellings identically in schemas and native answers."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: decimal_numbers(child) for key, child in value.items()}
    if isinstance(value, list):
        return [decimal_numbers(child) for child in value]
    return value


def decimal_keyword(name):
    original = Draft202012Validator.VALIDATORS[name]

    def validate(validator, constraint, instance, schema):
        yield from original(
            validator, decimal_numbers(constraint), decimal_numbers(instance), schema
        )

    return validate


def multiple_of(validator, divisor, instance, schema):
    if validator.is_type(instance, "number"):
        if (Fraction(Decimal(str(instance))) / Fraction(Decimal(str(divisor)))).denominator != 1:
            yield ValidationError(f"{instance!r} is not a multiple of {divisor}")


Validator = validators.extend(
    Draft202012Validator,
    validators={
        **{
            name: decimal_keyword(name)
            for name in (
                "const",
                "enum",
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "uniqueItems",
            )
        },
        "multipleOf": multiple_of,
    },
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine("integer", json_integer),
)


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON object key: " + key)
            result[key] = value
        return result

    def nonfinite(value):
        raise ValueError("Non-finite JSON value: " + value)

    return json.loads(text, object_pairs_hook=pairs, parse_float=Decimal, parse_constant=nonfinite)


def canonical(value, policy=None, path=""):
    """Type-preserving equality with explicit per-path text/array policy.

    Unordered arrays retain multiplicity. Objects retain complete record structure.
    """
    policy = policy or {}
    overrides = policy.get("paths", {}).get(path, {})
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
        if not number.is_finite():
            raise ValueError("Non-finite number")
        return ("number", number)
    if isinstance(value, str):
        mode = overrides.get("text", policy.get("text", "whitespace"))
        if mode not in {"literal", "whitespace"}:
            raise ValueError("Unsupported text equality policy")
        return ("string", " ".join(value.split()) if mode == "whitespace" else value)
    if isinstance(value, dict):
        return (
            "object",
            tuple(
                (
                    key,
                    canonical(item, policy, path + "/" + key.replace("~", "~0").replace("/", "~1")),
                )
                for key, item in sorted(value.items())
            ),
        )
    if isinstance(value, list):
        items = [
            canonical(item, policy, path + "/" + str(index)) for index, item in enumerate(value)
        ]
        mode = overrides.get("array", "ordered")
        if mode not in {"ordered", "unordered"}:
            raise ValueError("Unsupported array equality policy")
        return ("array", frozenset(Counter(items).items()) if mode == "unordered" else tuple(items))
    raise ValueError("Unsupported native JSON type")


def validate_query(query, value):
    errors = sorted(
        Validator(scoring_schema(query["schema"])).iter_errors(value), key=lambda e: str(e.path)
    )
    content_equal = canonical(value, query.get("equality")) == canonical(
        query["answer"], query.get("equality")
    )
    return {
        "query_id": query["id"],
        "schema_valid": not errors,
        "content_equal": content_equal,
        "accepted": not errors and content_equal,
        "schema_errors": [{"path": list(e.absolute_path), "message": e.message} for e in errors],
    }


def schema_scopes(schema, resource=None):
    """Visit schema locations, preserving literal data in const, enum and defaults."""
    if resource is None or isinstance(schema, dict) and "$id" in schema:
        resource = schema
    yield schema, resource
    if isinstance(schema, bool):
        return
    for key in ("$defs", "definitions", "properties", "patternProperties", "dependentSchemas"):
        for child in schema.get(key, {}).values():
            yield from schema_scopes(child, resource)
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        for child in schema.get(key, []):
            yield from schema_scopes(child, resource)
    for key in (
        "items",
        "contains",
        "additionalProperties",
        "unevaluatedProperties",
        "propertyNames",
        "unevaluatedItems",
        "not",
        "if",
        "then",
        "else",
        "contentSchema",
    ):
        if key in schema:
            yield from schema_scopes(schema[key], resource)


def schema_nodes(schema):
    return (node for node, _ in schema_scopes(schema))


def validate_schema(schema):
    """Keep all schema branches closed and reproducible without external lookups."""
    Draft202012Validator.check_schema(schema)
    scopes = list(schema_scopes(schema))
    declared = {id(node) for node, _ in scopes}
    for node, resource in scopes:
        if isinstance(node, bool):
            continue
        if any(key in node and not node[key].startswith("#") for key in ("$ref", "$dynamicRef")):
            raise ValueError("Task schemas must not depend on external references")
        for key in ("$ref", "$dynamicRef"):
            if key not in node:
                continue
            fragment = unquote(node[key][1:])
            if fragment.startswith("/"):
                target = resource
                try:
                    for part in fragment[1:].split("/"):
                        part = part.replace("~1", "/").replace("~0", "~")
                        if isinstance(target, list) and not re.fullmatch(r"0|[1-9][0-9]*", part):
                            raise ValueError("Invalid JSON Pointer array index")
                        target = target[int(part)] if isinstance(target, list) else target[part]
                except (KeyError, IndexError, TypeError, ValueError):
                    target = None
                valid = id(target) in declared
            else:
                valid = not fragment or any(
                    owner is resource
                    and isinstance(candidate, dict)
                    and fragment in {candidate.get("$anchor"), candidate.get("$dynamicAnchor")}
                    for candidate, owner in scopes
                )
            if not valid:
                raise ValueError("Local references must target declared schema locations")
        if "$schema" in node and node["$schema"].rstrip("#") not in {
            "https://json-schema.org/draft/2020-12/schema",
            "http://json-schema.org/draft/2020-12/schema",
        }:
            raise ValueError("Query schemas use the Draft 2020-12 dialect")
        kind = node.get("type")
        if kind == "object" or isinstance(kind, list) and "object" in kind:
            properties = node.get("properties", {})
            if (
                not properties
                or set(node.get("required", [])) != set(properties)
                or node.get("additionalProperties") is not False
            ):
                raise ValueError(
                    "Every requested object field must be required and extra keys rejected"
                )


def scoring_schema(schema):
    """Use one dialect and exact numeric validator throughout every nested scope."""
    validate_schema(schema)
    result = copy.deepcopy(schema)
    for node in schema_nodes(result):
        if isinstance(node, dict):
            node.pop("$schema", None)
    return result


def validate_dataset(dataset):
    if dataset.get("schema") != "web-extract-query-catalog/1":
        raise ValueError("A versioned Web Extract query catalog is required")
    if not dataset["pages"]:
        raise ValueError("A dataset must contain pages")
    page_ids = set()
    for page in dataset["pages"]:
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]+", page["id"])
            or page["id"] in page_ids
            or not page["queries"]
        ):
            raise ValueError("Page IDs must be distinct and queries nonempty")
        page_ids.add(page["id"])
        url = urlsplit(page["url"])
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError("Every page needs an explicit HTTP URL without credentials")
        regions = {region["id"]: region for region in page["regions"]}
        if len(regions) != len(page["regions"]):
            raise ValueError("Page region IDs must be distinct")
        characteristics = page["required_characteristics"]
        if not characteristics or any(
            not isinstance(tag, str) or not tag.strip() for tag in characteristics
        ):
            raise ValueError("Declare the page's independently observed characteristics")
        query_ids = set()
        for query in page["queries"]:
            if (
                not re.fullmatch(r"[A-Za-z0-9_-]+", query["id"])
                or query["id"] in query_ids
                or not query["query"].strip()
            ):
                raise ValueError("Queries need distinct page-local IDs and nonempty instructions")
            query_ids.add(query["id"])
            selection = query["selection"]
            region = regions.get(selection["region_id"], {})
            if (
                region.get("primary") is not True
                or not selection.get("rationale", "").strip()
                or not set(characteristics).issubset(region.get("characteristics", []))
            ):
                raise ValueError(
                    "Each query must target primary content with the page's characteristics"
                )
            if query.get("judge"):
                raise ValueError(
                    "This query contract requires deterministic acceptance; "
                    "model judges are not enabled"
                )
            validate_schema(query["schema"])
            if not isinstance(query["schema"], dict) or query["schema"].get("type") != "object":
                raise ValueError("Query answers must be JSON objects")
            if not validate_query(query, query["answer"])["accepted"]:
                raise ValueError("Reference answer fails its own contract")


def page_schema(queries):
    """Keep local references and anchors scoped to their original query schema."""
    properties = {}
    for query in queries:
        child = copy.deepcopy(query["schema"])
        # The combined root owns the dialect; retaining it here makes jsonschema
        # switch back to its default validator and lose our exact numeric rules.
        child.pop("$schema", None)
        identity = hashlib.sha256(
            json.dumps([query["id"], child], sort_keys=True).encode()
        ).hexdigest()
        child["$id"] = "urn:webstone:query:" + identity
        properties[query["id"]] = child
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "required": [q["id"] for q in queries],
        "additionalProperties": False,
    }


def compile_cases(dataset, unit="query", *, max_schema_bytes=None):
    """Select actual request packaging, rather than merely regrouping reported scores."""
    if unit not in {"query", "page"}:
        raise ValueError("unit must be query or page")
    validate_dataset(dataset)
    result = []
    for page in dataset["pages"]:
        groups = [[q] for q in page["queries"]] if unit == "query" else [page["queries"]]
        for queries in groups:
            if unit == "query":
                schema = copy.deepcopy(queries[0]["schema"])
                instruction = queries[0]["query"]
                case_id = (
                    "query-"
                    + hashlib.sha256(
                        json.dumps([page["id"], queries[0]["id"]]).encode()
                    ).hexdigest()[:24]
                )
            else:
                schema = page_schema(queries)
                instruction = (
                    "Answer every named task independently on this one page. Preserve each task's "
                    "scope and filter. Return a single JSON object keyed by the exact task IDs. "
                    "Do not merge answer fields across tasks.\n\n"
                    + "\n\n".join("Task " + q["id"] + ":\n" + q["query"] for q in queries)
                )
                case_id = page["id"]
            Draft202012Validator.check_schema(schema)
            if max_schema_bytes is not None and len(json.dumps(schema).encode()) > max_schema_bytes:
                raise ValueError("Combined/request schema exceeds the frozen limit: " + case_id)
            result.append(
                {
                    "id": case_id,
                    "page_id": page["id"],
                    "unit": unit,
                    "query_ids": [q["id"] for q in queries],
                    "input": {"url": page["url"], "query": instruction, "schema": schema},
                }
            )
    return result


def score(truth, predictions, **options):
    """The existing track scorer hook, retaining one verdict per atomic question."""
    if options:
        raise ValueError("Query answers do not use field-list reference policies")
    if set(truth) != set(predictions):
        raise ValueError("Predictions must cover the complete declared query population")
    rows, executions, pages = [], [], defaultdict(list)
    seen = set()
    units = {reference["task"]["unit"] for reference in truth.values()}
    if len(units) != 1 or not units.issubset({"query", "page"}):
        raise ValueError("Score one nonempty request unit at a time")
    for case_id, reference in sorted(truth.items()):
        task = reference["task"]
        answer = predictions[case_id]
        unit = task["unit"]
        queries = task["queries"]
        if unit == "query":
            valid = validate_query(queries[0], answer)["schema_valid"]
        else:
            schema = page_schema(queries)
            valid = Validator(scoring_schema(schema)).is_valid(answer)
        accepted = []
        for query in queries:
            key = (task["page_id"], query["id"])
            if key in seen:
                raise ValueError("Every page query must be scored exactly once")
            seen.add(key)
            if unit == "page" and (not isinstance(answer, dict) or query["id"] not in answer):
                verdict = {
                    "query_id": query["id"],
                    "schema_valid": False,
                    "content_equal": False,
                    "accepted": False,
                }
            else:
                value = answer if unit == "query" else answer[query["id"]]
                verdict = validate_query(query, value)
            verdict.update(page_id=task["page_id"], case_id=case_id)
            rows.append(verdict)
            accepted.append(verdict["accepted"])
            pages[task["page_id"]].append(verdict["accepted"])
        executions.append(
            {
                "id": case_id,
                "page_id": task["page_id"],
                "schema_valid": valid,
                "accepted": valid and all(accepted),
            }
        )
    page_rows = [
        {
            "id": pid,
            "queries": len(results),
            "accepted": sum(results),
            "accuracy": sum(results) / len(results),
            "fully_correct": all(results)
            and all(row["schema_valid"] for row in executions if row["page_id"] == pid),
        }
        for pid, results in sorted(pages.items())
    ]
    return {
        "query_accuracy": {
            "accepted": sum(r["accepted"] for r in rows),
            "total": len(rows),
            "rate": sum(r["accepted"] for r in rows) / len(rows),
        },
        "page_macro_query_accuracy": {
            "pages": len(page_rows),
            "rate": sum(r["accuracy"] for r in page_rows) / len(page_rows),
        },
        "page_all_correct": {
            "accepted": sum(r["fully_correct"] for r in page_rows),
            "total": len(page_rows),
            "rate": sum(r["fully_correct"] for r in page_rows) / len(page_rows),
        },
        "request_accuracy": {
            "accepted": sum(r["accepted"] for r in executions),
            "total": len(executions),
            "rate": sum(r["accepted"] for r in executions) / len(executions),
        },
        "queries": rows,
        "pages": page_rows,
        "requests": executions,
    }
