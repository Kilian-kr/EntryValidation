"""
JSON-Schema validation for template definition files.

The template schema is loaded once at import time from template_schema.json.
If that file is missing or malformed the module raises a clear RuntimeError
rather than crashing with an opaque AttributeError later.
"""
import json
import os
import jsonschema

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "template_schema.json")

# Load the meta-schema that describes what a valid template looks like.
# Fail loudly at startup so a missing/corrupt schema file is caught immediately.
try:
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as _f:
        _TEMPLATE_SCHEMA = json.load(_f)
except FileNotFoundError:
    raise RuntimeError(
        f"Template schema file not found: {_SCHEMA_PATH}. "
        "Ensure template_schema.json exists inside the core/ directory."
    )
except json.JSONDecodeError as _e:
    raise RuntimeError(
        f"Template schema file is not valid JSON ({_SCHEMA_PATH}): {_e}"
    )


def validate_template_json(data: dict) -> list[str]:
    """
    Validate *data* against the template JSON schema.

    Returns a list of human-readable error messages.
    An empty list means the template is valid.
    """
    validator = jsonschema.Draft7Validator(_TEMPLATE_SCHEMA)
    # Sort errors by their JSON path so the list is deterministic
    return [
        e.message
        for e in sorted(validator.iter_errors(data), key=lambda e: e.path)
    ]
