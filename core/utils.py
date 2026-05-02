"""Shared utility helpers used across core modules."""
import re
import unicodedata
import logging

logger = logging.getLogger(__name__)


def slugify(value: str) -> str:
    """Convert a string to a URL-safe slug (lowercase, hyphens, ASCII only)."""
    value = unicodedata.normalize("NFKD", str(value))
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)


def safe_str(v) -> str:
    """Return str(v), or empty string for None."""
    if v is None:
        return ""
    return str(v)


def is_null_value(value_str: str, null_tokens: list[str]) -> bool:
    """
    Return True when *value_str* (already stripped) matches any token in
    *null_tokens* (case-insensitive).

    Performance note: callers that invoke this in a tight per-cell loop should
    pre-build a frozenset of lowercased tokens and pass it, or use the
    ``make_null_set`` helper below to avoid rebuilding the set on every call.
    """
    normalized = value_str.strip().lower()
    return normalized in {t.lower() for t in null_tokens}


def make_null_set(null_tokens: list[str]) -> frozenset[str]:
    """
    Pre-compute a frozenset of lowercased null tokens for repeated membership
    tests.  Use this instead of calling ``is_null_value`` in a tight loop.

    Example::

        null_set = make_null_set(schema_null_tokens + NULL_VALUES)
        for value in column:
            if value.lower() in null_set:
                ...
    """
    return frozenset(t.lower() for t in null_tokens)


def normalize_column_name(name: str) -> str:
    """Lowercase + strip a column name for case-insensitive matching."""
    return str(name).strip().lower()
