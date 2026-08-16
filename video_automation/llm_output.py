"""Shared parsing/validation for structured (JSON schema) LLM output.

Both the local llama.cpp path and the cloud providers (OpenAI, Google) go
through these helpers so schema violations surface as repairable errors
instead of silently dropping fields downstream.
"""

from __future__ import annotations

import json
from typing import Any


class StructuredOutputError(ValueError):
    """Raised when an LLM response cannot be parsed or fails schema validation."""


def parse_structured_json(text: str, *, provider: str = "LLM") -> dict[str, Any]:
    """Parse an LLM reply into a JSON object, tolerating code fences and prose."""
    value = str(text or "").strip()
    if value.startswith("```"):
        value = value.strip("`").strip()
        if value.lower().startswith("json"):
            value = value[4:].lstrip()
    try:
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(value):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise StructuredOutputError(f"{provider} returned invalid JSON.")


def validate_required_shape(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate required keys and basic types/limits from a JSON-schema subset.

    Supported: type (object/array/string/number/integer/boolean), required,
    properties, items, minItems, maxItems, minimum, maximum. Extra object
    keys are allowed; downstream normalization drops unknown fields.
    """
    expected = schema.get("type")
    if expected == "object":
        _require(isinstance(value, dict), f"{path} must be an object.")
        for key in schema.get("required", []):
            _require(key in value, f"{path}.{key} is required.")
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, child_schema in properties.items():
                if key in value and isinstance(child_schema, dict):
                    validate_required_shape(value[key], child_schema, f"{path}.{key}")
    elif expected == "array":
        _require(isinstance(value, list), f"{path} must be an array.")
        min_items = schema.get("minItems")
        if isinstance(min_items, int):
            _require(len(value) >= min_items, f"{path} must contain at least {min_items} items.")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int):
            _require(len(value) <= max_items, f"{path} must contain at most {max_items} items.")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_required_shape(item, item_schema, f"{path}[{index}]")
    elif expected == "string":
        _require(isinstance(value, str), f"{path} must be a string.")
    elif expected == "integer":
        _require(isinstance(value, int) and not isinstance(value, bool), f"{path} must be an integer.")
    elif expected == "boolean":
        _require(isinstance(value, bool), f"{path} must be a boolean.")
    elif expected == "number":
        _require(
            isinstance(value, (int, float)) and not isinstance(value, bool),
            f"{path} must be a number.",
        )
    _check_bounds(value, schema, path)


def _check_bounds(value: Any, schema: dict[str, Any], path: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return
    minimum = schema.get("minimum")
    if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
        _require(float(value) >= float(minimum), f"{path} must be >= {minimum}.")
    maximum = schema.get("maximum")
    if isinstance(maximum, (int, float)) and not isinstance(maximum, bool):
        _require(float(value) <= float(maximum), f"{path} must be <= {maximum}.")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StructuredOutputError(message)
