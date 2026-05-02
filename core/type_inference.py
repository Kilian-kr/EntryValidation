"""
Column type inference.

Analyses a sample of each DataFrame column and returns a best-guess type
together with relevant options (detected formats for date/datetime, distinct
values for enum).  Results are used to pre-populate the schema configuration
UI so users do not have to classify every column manually.

Detection order (first match wins):
    integer → number → datetime → date → boolean → enum → string

Integer is checked before boolean so that columns containing only "0" and "1"
are classified as integers rather than booleans (both are valid tokens for
each type, but integer is the more specific and useful classification).
Boolean is checked after numeric types for the same reason.
Datetime is checked before date so that "2024-01-15 10:30:00" is not
matched by a date-only format that happens to match the date part.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import pandas as pd

from config import NULL_VALUES
from core.utils import make_null_set

# Pre-compute the global null set for fast membership tests
_NULL_SET = make_null_set(NULL_VALUES)

# Fraction of non-null values that must match for a type to be declared.
# 0.95 allows up to 5% "dirty" or mixed cells without changing the inferred type.
_THRESHOLD = 0.95

# Maximum number of values sampled per column.
# Keeps inference fast on large files; 500 is sufficient for reliable detection.
_SAMPLE = 500

# Enum detection parameters:
#   - at most _ENUM_MAX_UNIQUE distinct values in the column
#   - for large columns (>100 rows) the unique/total ratio must also be low
_ENUM_MAX_UNIQUE = 20
_ENUM_MAX_RATIO  = 0.10

# Boolean tokens (case-insensitive).
# NOTE: "1" and "0" are intentionally excluded here; a column of 0/1 integers
# should be classified as integer, not boolean.  Users can override in the UI.
_BOOL_TOKENS = frozenset({"true", "false", "yes", "no"})

# Common date formats, tried in order.
# More specific / unambiguous formats come first.
_DATE_FORMATS: list[str] = [
    "%Y-%m-%d",    # 2024-01-15  (ISO 8601 — most common in data files)
    "%d/%m/%Y",    # 15/01/2024  (European)
    "%m/%d/%Y",    # 01/15/2024  (US)
    "%d.%m.%Y",    # 15.01.2024  (Central/Eastern European)
    "%d-%m-%Y",    # 15-01-2024
    "%Y/%m/%d",    # 2024/01/15
]

# Common datetime formats, tried before date formats to avoid partial matches.
_DATETIME_FORMATS: list[str] = [
    "%Y-%m-%d %H:%M:%S",    # 2024-01-15 10:30:00
    "%Y-%m-%dT%H:%M:%S",    # 2024-01-15T10:30:00  (ISO 8601 with T separator)
    "%Y-%m-%d %H:%M",       # 2024-01-15 10:30     (no seconds)
    "%d/%m/%Y %H:%M:%S",    # 15/01/2024 10:30:00
    "%d/%m/%Y %H:%M",       # 15/01/2024 10:30
    "%d.%m.%Y %H:%M:%S",    # 15.01.2024 10:30:00
    "%d.%m.%Y %H:%M",       # 15.01.2024 10:30
    "%m/%d/%Y %H:%M:%S",    # 01/15/2024 10:30:00
    "%m/%d/%Y %H:%M",       # 01/15/2024 10:30
]


# ── Low-level test functions ───────────────────────────────────────────────────

def _match_rate(values: list[str], test) -> float:
    """Return the fraction of *values* for which *test(v)* is True."""
    if not values:
        return 0.0
    return sum(1 for v in values if test(v)) / len(values)


def _is_integer(s: str) -> bool:
    """Return True if *s* can be parsed as a whole integer (no decimals)."""
    try:
        int(s)
        return True
    except ValueError:
        return False


def _is_number(s: str) -> bool:
    """Return True if *s* is a finite float (includes integers)."""
    try:
        return math.isfinite(float(s))
    except ValueError:
        return False


def _is_boolean(s: str) -> bool:
    """Return True if *s* is a recognised boolean token (case-insensitive)."""
    return s.lower() in _BOOL_TOKENS


def _matches_fmt(s: str, fmt: str) -> bool:
    """Return True if *s* can be parsed with strptime format *fmt*."""
    try:
        datetime.strptime(s, fmt)
        return True
    except ValueError:
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def infer_column(series: pd.Series) -> dict[str, Any]:
    """
    Infer the most appropriate validation type for a single DataFrame column.

    Returns a dict with at minimum ``{"type": <str>}`` and optionally:
    - ``{"type": "date",     "formats": ["%Y-%m-%d"]}``
    - ``{"type": "datetime", "formats": ["%Y-%m-%d %H:%M:%S"]}``
    - ``{"type": "enum",     "allowed": ["A", "B", "C"]}``

    The returned formats / allowed list is used to pre-populate the schema UI.
    """
    # Convert to strings and drop pandas NaN/None; strip whitespace
    raw    = series.dropna().astype(str)
    values = [v.strip() for v in raw if v.strip() and v.strip().lower() not in _NULL_SET]

    if not values:
        # All cells are null/empty — cannot determine a type
        return {"type": "string"}

    # Take a fixed-size sample for the rate-based checks (keeps inference fast)
    sample = values[:_SAMPLE]

    # ── Integer (checked before boolean — "0"/"1" are integers, not booleans) ─
    if _match_rate(sample, _is_integer) >= _THRESHOLD:
        return {"type": "integer"}

    # ── Floating-point number ─────────────────────────────────────────────────
    if _match_rate(sample, _is_number) >= _THRESHOLD:
        return {"type": "number"}

    # ── Datetime (checked before date — more specific format) ────────────────
    for fmt in _DATETIME_FORMATS:
        if _match_rate(sample, lambda s, f=fmt: _matches_fmt(s, f)) >= _THRESHOLD:
            return {"type": "datetime", "formats": [fmt]}

    # ── Date ──────────────────────────────────────────────────────────────────
    for fmt in _DATE_FORMATS:
        if _match_rate(sample, lambda s, f=fmt: _matches_fmt(s, f)) >= _THRESHOLD:
            return {"type": "date", "formats": [fmt]}

    # ── Boolean (true/false/yes/no — after numeric to avoid "1"/"0" collision) ─
    if _match_rate(sample, _is_boolean) >= _THRESHOLD:
        return {"type": "boolean"}

    # ── Enum (low-cardinality categorical column) ─────────────────────────────
    # Use the same sample for cardinality checks for consistency with the
    # rate-based checks above.
    unique_vals = sorted(set(sample))
    n_unique    = len(unique_vals)
    n_total     = len(sample)
    is_enum = (
        n_unique <= _ENUM_MAX_UNIQUE
        and (n_total <= 100 or n_unique / n_total <= _ENUM_MAX_RATIO)
    )
    if is_enum:
        return {"type": "enum", "allowed": unique_vals}

    # ── Fallback: free-form text ──────────────────────────────────────────────
    return {"type": "string"}


def infer_all(df: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """
    Infer types for every column in *df*.

    Returns ``{column_name: infer_column(series)}`` for each column.
    Errors in individual columns are caught and logged so a single bad column
    does not abort inference for the rest of the dataset.
    """
    import logging
    log = logging.getLogger(__name__)
    result: dict[str, dict[str, Any]] = {}
    for col in df.columns:
        try:
            result[col] = infer_column(df[col])
        except Exception as exc:
            log.warning("Type inference failed for column %r: %s", col, exc)
            result[col] = {"type": "string"}
    return result
