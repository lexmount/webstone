"""Named, versioned schemas for the records that stages exchange.

A track declares one schema per record kind (call record, scored result). Every
schema maps the common roles below onto the track's own field names, so the
method identity, case, timing, evidence binding and verdict mean the same thing
in every track while each track keeps its field names. The stage that reads a
record file validates it here before using it (docs/spec.md, "Stages and
artifacts").

A record may name its schema in a ``schema`` field (``<name>/<version>``). A
record without one is read as the first schema the reader passes: the version
that historical files were written in.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from harness.record_io import load_jsonl

NUMBER = (int, float)
TEXT = (str,)
OPTIONAL_TEXT = (str, type(None))

# Common role -> accepted value types. Every schema binds each role to a field.
COMMON_ROLES: dict[str, tuple[type, ...]] = {
    "method": TEXT,      # method (provider, browser or model configuration) identity
    "case": TEXT,        # task or case identifier
    "elapsed": NUMBER,   # call duration in milliseconds
    "evidence": TEXT,    # binding to the raw output: run-relative path or digest
    "verdict": (bool,),  # whether the call delivered a usable response
}


class RecordSchemaError(ValueError):
    """A record does not match the schema its reader requires."""


@dataclass(frozen=True)
class RecordSchema:
    name: str
    version: int
    roles: Mapping[str, str]
    fields: Mapping[str, tuple[type, ...]] = field(default_factory=dict)
    optional: Mapping[str, tuple[type, ...]] = field(default_factory=dict)
    # Roles whose field may hold null, such as the elapsed time of an untimed call.
    nullable: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if set(self.roles) != set(COMMON_ROLES):
            raise ValueError(f"{self.id}: bind every common role: {', '.join(COMMON_ROLES)}")
        if not self.nullable <= set(COMMON_ROLES):
            raise ValueError(f"{self.id}: only common roles can be nullable")

    @property
    def id(self) -> str:
        return f"{self.name}/{self.version}"

    def required(self) -> dict[str, tuple[type, ...]]:
        roles = {self.roles[role]: (*types, type(None)) if role in self.nullable else types
                 for role, types in COMMON_ROLES.items()}
        return {**roles, **self.fields}

    def extend(self, name: str, version: int, **fields: tuple[type, ...]) -> RecordSchema:
        """A schema with the same roles and additional required fields."""
        return RecordSchema(name, version, self.roles, {**self.fields, **fields}, self.optional,
                            self.nullable)

    def validate(self, record: object, where: str = "record") -> dict:
        if not isinstance(record, dict):
            raise RecordSchemaError(f"{where}: {self.id} record must be an object")
        for key, types in self.required().items():
            if key not in record:
                raise RecordSchemaError(f"{where}: {self.id} record lacks {key!r}")
            _check_type(record[key], types, f"{where}: {self.id} field {key!r}")
        for key, types in self.optional.items():
            if key in record:
                _check_type(record[key], types, f"{where}: {self.id} field {key!r}")
        return record


def _check_type(value: object, types: tuple[type, ...], where: str) -> None:
    # bool is an int subclass; a numeric field never accepts a boolean.
    if isinstance(value, bool) and bool not in types:
        raise RecordSchemaError(f"{where} has type bool")
    if not isinstance(value, types):
        raise RecordSchemaError(f"{where} has type {type(value).__name__}")


def validate_records(records: Iterable[object], *schemas: RecordSchema,
                     source: str = "records") -> list[dict]:
    """Validate each record against the schema it names; unnamed records use ``schemas[0]``."""
    if not schemas:
        raise ValueError("validate_records requires at least one schema")
    known = {schema.id: schema for schema in schemas}
    rows = []
    for index, record in enumerate(records, 1):
        declared = record.get("schema") if isinstance(record, dict) else None
        if declared is not None and not isinstance(declared, str):
            raise RecordSchemaError(f"{source} row {index}: schema must be a string")
        schema = known.get(declared) if declared is not None else schemas[0]
        if schema is None:
            raise RecordSchemaError(f"{source} row {index}: unsupported schema {declared!r}; "
                                    f"this reader accepts {', '.join(known)}")
        rows.append(schema.validate(record, f"{source} row {index}"))
    return rows


def read_records(path: Path, *schemas: RecordSchema) -> list[dict]:
    """Read a JSON Lines record file and validate every record."""
    return validate_records(load_jsonl(path, skip_blank=True), *schemas, source=Path(path).name)
