"""
Type parsers and validation rules engine.

Entry point: ``run_validation(df, schema)`` returns an issues dict that is
persisted as issues.json and consumed by the results view.

Architecture
------------
1. ``_parse_*``   — attempt to convert a raw string to a typed value.
2. ``_apply_rules`` — check range / regex / blocklist rules after parsing.
3. ``validate_cell`` — orchestrate null-check → type-parse → rule-check.
4. ``run_validation`` — iterate over the whole DataFrame, collect all issues,
   compute column and dataset-level statistics.
"""
import re
import math
import logging
from datetime import datetime
from config import NULL_VALUES
from core.utils import make_null_set

logger = logging.getLogger(__name__)

# ── Issue codes ───────────────────────────────────────────────────────────────
# String constants used as the "code" field in every cell/dataset issue.
# Keeping them as module-level names lets callers do equality checks without
# hard-coding string literals.

REQUIRED_EMPTY           = "REQUIRED_EMPTY"
TYPE_MISMATCH            = "TYPE_MISMATCH"
DATE_PARSE_FAILED        = "DATE_PARSE_FAILED"
REGEX_MISMATCH           = "REGEX_MISMATCH"
MIN_LEN_VIOLATED         = "MIN_LEN_VIOLATED"
MAX_LEN_VIOLATED         = "MAX_LEN_VIOLATED"
MIN_VIOLATED             = "MIN_VIOLATED"
MAX_VIOLATED             = "MAX_VIOLATED"
ENUM_NOT_ALLOWED         = "ENUM_NOT_ALLOWED"
NOT_IN_VIOLATED          = "NOT_IN_VIOLATED"
UNIQUE_VIOLATED          = "UNIQUE_VIOLATED"
UNIQUE_TOGETHER_VIOLATED = "UNIQUE_TOGETHER_VIOLATED"
MISSING_COLUMN           = "MISSING_COLUMN"
EXTRA_COLUMN             = "EXTRA_COLUMN"
PARSE_ERROR              = "PARSE_ERROR"


# ── Type parsers ──────────────────────────────────────────────────────────────

def _parse_integer(value_str: str) -> tuple[bool, int | None]:
    """
    Try to parse *value_str* as a whole integer.

    Note: "1.0" is intentionally rejected — a cell that contains a decimal
    point is not an integer even if its fractional part is zero.  This keeps
    inference and validation consistent.
    """
    try:
        return True, int(value_str)
    except ValueError:
        return False, None


def _parse_number(value_str: str) -> tuple[bool, float | None]:
    """
    Parse *value_str* as a finite floating-point number.
    Rejects inf and nan to avoid silently propagating IEEE 754 sentinels.
    """
    try:
        result = float(value_str)
        if not math.isfinite(result):
            return False, None
        return True, result
    except ValueError:
        return False, None


def _parse_boolean(value_str: str) -> tuple[bool, None]:
    """Accept true/false/yes/no/1/0 (case-insensitive). Returns (ok, None)."""
    if value_str.lower() in {"true", "false", "yes", "no", "1", "0"}:
        return True, None
    return False, None


def _parse_date(value_str: str, formats: list[str]) -> tuple[bool, datetime | None]:
    """
    Try each format in *formats* until one matches.
    Returns (False, None) when no format matches or *formats* is empty —
    an empty format list means "reject everything" which forces the user
    to specify at least one format.
    """
    for fmt in formats:
        try:
            return True, datetime.strptime(value_str, fmt)
        except ValueError:
            pass
    return False, None


def _parse_datetime(value_str: str, formats: list[str]) -> tuple[bool, datetime | None]:
    """Alias for _parse_date — datetime parsing uses the same strptime logic."""
    return _parse_date(value_str, formats)


# ── Rule engine ───────────────────────────────────────────────────────────────

def _apply_rules(
    value_str: str,
    parsed_num,
    parsed_date: datetime | None,
    col_type: str,
    rules: dict,
) -> list[tuple[str, str]]:
    """
    Evaluate all secondary rules (range, regex, blocklist) after the primary
    type check has already passed.

    Returns a list of (code, message) pairs — empty means all rules passed.
    """
    issues = []

    # ── String / text rules ──────────────────────────────────────────────────
    if col_type in ("string", "text"):
        if "regex" in rules:
            try:
                if not re.fullmatch(rules["regex"], value_str):
                    issues.append((REGEX_MISMATCH,
                        f"Value does not match pattern {rules['regex']}"))
            except re.error:
                pass  # Invalid regex in the schema — silently skip this rule

        if "min_len" in rules and len(value_str) < rules["min_len"]:
            issues.append((MIN_LEN_VIOLATED,
                f"Length {len(value_str)} < min {rules['min_len']}"))

        if "max_len" in rules and len(value_str) > rules["max_len"]:
            issues.append((MAX_LEN_VIOLATED,
                f"Length {len(value_str)} > max {rules['max_len']}"))

    # ── Numeric range rules ──────────────────────────────────────────────────
    elif col_type in ("integer", "number") and parsed_num is not None:
        if "min" in rules and parsed_num < rules["min"]:
            issues.append((MIN_VIOLATED,
                f"Value {parsed_num} < min {rules['min']}"))
        if "max" in rules and parsed_num > rules["max"]:
            issues.append((MAX_VIOLATED,
                f"Value {parsed_num} > max {rules['max']}"))

    # ── Date / datetime range rules ──────────────────────────────────────────
    elif col_type in ("date", "datetime") and parsed_date is not None:
        boundary_fmt = rules.get("date_format", "%Y-%m-%d")
        if "min_date" in rules:
            try:
                min_dt = datetime.strptime(str(rules["min_date"]), boundary_fmt)
                if parsed_date < min_dt:
                    issues.append((MIN_VIOLATED,
                        f"Date {parsed_date.strftime('%Y-%m-%d')} is before "
                        f"minimum {rules['min_date']}"))
            except ValueError:
                pass  # Unparseable boundary — skip rather than crash
        if "max_date" in rules:
            try:
                max_dt = datetime.strptime(str(rules["max_date"]), boundary_fmt)
                if parsed_date > max_dt:
                    issues.append((MAX_VIOLATED,
                        f"Date {parsed_date.strftime('%Y-%m-%d')} is after "
                        f"maximum {rules['max_date']}"))
            except ValueError:
                pass

    # ── Blocklist (applies to all types) ────────────────────────────────────
    if "not_in" in rules:
        blocklist = {str(v).lower() for v in rules["not_in"]}
        if value_str.lower() in blocklist:
            issues.append((NOT_IN_VIOLATED,
                f"'{value_str}' is in the blocklist"))

    return issues


# ── Cell validator ────────────────────────────────────────────────────────────

def validate_cell(
    value_str: str,
    col_def: dict,
    null_tokens: list[str],
) -> list[tuple[str, str]]:
    """
    Validate a single cell value against its column definition.

    Parameters
    ----------
    value_str  : Raw string value from the DataFrame (already converted via str()).
    col_def    : Column definition dict from the resolved schema.
    null_tokens: Schema-level null tokens (merged with global NULL_VALUES inside).

    Returns a list of (code, message) pairs.  An empty list means the cell is valid.
    """
    norm_val = value_str.strip()

    # Merge schema-level null tokens with the application-wide defaults.
    # Pre-compute a set for O(1) membership tests.
    null_set = make_null_set(null_tokens + NULL_VALUES)

    # ── 1. Null / empty check ────────────────────────────────────────────────
    if norm_val.lower() in null_set:
        # Cell is null — only an error if the column is required/non-nullable
        if col_def.get("required", False) or not col_def.get("nullable", True):
            return [(REQUIRED_EMPTY, "Required field is empty")]
        return []

    col_type = col_def.get("type", "string")
    if col_type == "text":
        col_type = "string"   # "text" is a legacy alias for "string"
    rules = col_def.get("rules") or {}

    # ── 2. Type parse ────────────────────────────────────────────────────────
    parsed_num  = None
    parsed_date = None

    if col_type == "integer":
        ok, parsed_num = _parse_integer(norm_val)
        if not ok:
            return [(TYPE_MISMATCH, f"Expected integer, got '{norm_val}'")]

    elif col_type == "number":
        ok, parsed_num = _parse_number(norm_val)
        if not ok:
            return [(TYPE_MISMATCH, f"Expected number, got '{norm_val}'")]

    elif col_type == "boolean":
        ok, _ = _parse_boolean(norm_val)
        if not ok:
            return [(TYPE_MISMATCH,
                f"Expected boolean (true/false/yes/no/1/0), got '{norm_val}'")]

    elif col_type == "date":
        ok, parsed_date = _parse_date(norm_val, rules.get("formats", []))
        if not ok:
            fmts = rules.get("formats", [])
            msg  = (f"Expected format(s): {', '.join(fmts)}" if fmts
                    else "No date format specified — all dates rejected")
            return [(DATE_PARSE_FAILED, msg)]

    elif col_type == "datetime":
        ok, parsed_date = _parse_datetime(norm_val, rules.get("formats", []))
        if not ok:
            fmts = rules.get("formats", [])
            msg  = (f"Expected format(s): {', '.join(fmts)}" if fmts
                    else "No datetime format specified — all datetimes rejected")
            return [(DATE_PARSE_FAILED, msg)]

    elif col_type == "enum":
        allowed_lower = [str(a).lower() for a in rules.get("allowed", [])]
        if norm_val.lower() not in allowed_lower:
            return [(ENUM_NOT_ALLOWED,
                f"'{norm_val}' not in allowed values: {rules.get('allowed', [])}")]
        # Enum value is valid — still run the blocklist check below via _apply_rules
        return _apply_rules(norm_val, None, None, col_type, rules)

    # ── 3. Secondary rules (range, regex, blocklist) ─────────────────────────
    return _apply_rules(norm_val, parsed_num, parsed_date, col_type, rules)


# ── Dataset validator ─────────────────────────────────────────────────────────

def run_validation(df, schema: dict) -> dict:
    """
    Validate an entire DataFrame against a resolved schema.

    Returns the issues dict (written to issues.json).  Structure::

        {
            "dataset_id":     str,
            "schema_id":      str,
            "run_at":         ISO-8601 timestamp,
            "dataset_issues": [{code, column, message}, ...],
            "cell_issues":    [{row, column, code, message}, ...],
            "column_summary": {col: {invalid_count, total, numeric_stats?}},
            "stats":          {rows, columns, total_cells, invalid_cells,
                               invalid_rows, invalid_columns, worst_column,
                               top_issue_codes},
        }
    """
    import pandas as pd
    from datetime import timezone

    columns_schema  = schema.get("columns", {})
    null_tokens     = schema.get("null_values", [])
    strict          = schema.get("strict", False)
    dataset_issues: list[dict] = []
    cell_issues:    list[dict] = []
    column_summary: dict[str, dict] = {}
    # Per-column set of already-seen values — used for the unique rule
    seen_values:    dict[str, set]  = {}

    file_cols  = list(df.columns)
    total_rows = len(df)

    # Pre-compute the merged null set once so it can be reused inside loops
    null_toks_full = null_tokens + NULL_VALUES
    null_set_full  = make_null_set(null_toks_full)

    # ── Dataset-level: missing required columns ───────────────────────────────
    for col_name, col_def in columns_schema.items():
        if (col_def.get("required")
                and col_name not in file_cols
                and not col_def.get("unvalidated")):
            dataset_issues.append({
                "code":    MISSING_COLUMN,
                "column":  col_name,
                "message": f"Required column '{col_name}' is missing from the file",
            })

    # ── Dataset-level: extra columns in strict mode ───────────────────────────
    # Strict mode rejects any column not present in the template.
    if strict:
        template_file_cols = {
            cd["file_column"]
            for cd in columns_schema.values()
            if not cd.get("unvalidated") and "file_column" in cd
        }
        for fc in file_cols:
            if fc not in template_file_cols:
                dataset_issues.append({
                    "code":    EXTRA_COLUMN,
                    "column":  fc,
                    "message": f"Unexpected column '{fc}' (strict mode)",
                })

    # ── Per-column cell validation ────────────────────────────────────────────
    for col_name in file_cols:
        col_def = columns_schema.get(col_name)

        # Columns not in the schema are recorded in the summary but not validated
        if col_def is None or col_def.get("unvalidated"):
            column_summary[col_name] = {"invalid_count": 0, "total": total_rows}
            continue

        invalid_count = 0
        check_unique  = (col_def.get("rules") or {}).get("unique", False)
        if check_unique:
            seen_values[col_name] = set()

        col_type_raw = col_def.get("type", "string")

        # Collect valid numeric values so we can compute column-level stats
        valid_nums: list[float] = []
        track_nums = col_type_raw in ("integer", "number")

        for row_idx, value in enumerate(df[col_name]):
            # Normalise the cell value to a plain string.
            # pandas nullable types (pd.NA) stringify as "<NA>" which would
            # slip through the null check, so we catch that explicitly.
            if value is None or (hasattr(value, '_value') and value is pd.NA):
                value_str = ""
            else:
                value_str = str(value)
                # Treat pandas NA string representation as empty
                if value_str == "<NA>":
                    value_str = ""

            try:
                cell_errs = validate_cell(value_str, col_def, null_tokens)
            except Exception as exc:
                logger.exception(
                    "Unexpected error validating row %d col %s", row_idx, col_name)
                cell_errs = [(PARSE_ERROR, f"Internal error: {exc}")]

            # ── Unique constraint ────────────────────────────────────────────
            # Only checked when the cell itself is valid and non-null
            if check_unique and not cell_errs:
                stripped = value_str.strip()
                if stripped.lower() not in null_set_full:
                    norm = stripped.lower()
                    if norm in seen_values[col_name]:
                        cell_errs = [(UNIQUE_VIOLATED,
                            f"Duplicate value '{value_str}'")]
                    else:
                        seen_values[col_name].add(norm)

            if cell_errs:
                invalid_count += 1
                for code, message in cell_errs:
                    cell_issues.append({
                        "row":     row_idx,
                        "column":  col_name,
                        "code":    code,
                        "message": message,
                    })

            # Accumulate valid numeric values for stats
            if track_nums and not cell_errs:
                vs = value_str.strip()
                if vs.lower() not in null_set_full:
                    if col_type_raw == "integer":
                        ok, pv = _parse_integer(vs)
                    else:
                        ok, pv = _parse_number(vs)
                    if ok and pv is not None:
                        valid_nums.append(float(pv))

        column_summary[col_name] = {"invalid_count": invalid_count, "total": total_rows}

        # Compute basic numeric statistics (mean, std, min, max) for the column
        if track_nums and valid_nums:
            mean_val = sum(valid_nums) / len(valid_nums)
            ns: dict = {
                "count": len(valid_nums),
                "min":   min(valid_nums),
                "max":   max(valid_nums),
                "mean":  round(mean_val, 6),
            }
            if len(valid_nums) >= 2:
                variance = (
                    sum((x - mean_val) ** 2 for x in valid_nums)
                    / (len(valid_nums) - 1)
                )
                ns["std"] = round(variance ** 0.5, 6)
            column_summary[col_name]["numeric_stats"] = ns

    # ── Dataset-level rules ───────────────────────────────────────────────────
    for rule in schema.get("dataset_rules", []):
        if rule.get("type") == "unique_together":
            # Composite uniqueness: flag any row where the combination of
            # values in the specified columns duplicates an earlier row.
            cols      = rule.get("columns", [])
            available = [c for c in cols if c in file_cols]
            if len(available) < 2:
                continue  # Rule is meaningless without at least two columns

            seen_combos: dict[tuple, int] = {}
            for row_idx in range(total_rows):
                combo = tuple(
                    str(df[c].iloc[row_idx]).strip().lower()
                    for c in available
                )
                # Skip rows that are entirely null in the key columns
                if all(v in null_set_full for v in combo):
                    continue
                if combo in seen_combos:
                    first_row = seen_combos[combo]
                    for col in available:
                        cell_issues.append({
                            "row":     row_idx,
                            "column":  col,
                            "code":    UNIQUE_TOGETHER_VIOLATED,
                            "message": (
                                f"Composite key ({', '.join(available)}) "
                                f"duplicates row {first_row}"
                            ),
                        })
                else:
                    seen_combos[combo] = row_idx

    # ── Recompute invalid_count after all issues are collected ────────────────
    # The per-column loop above may under-count if dataset rules add extra
    # issues after the column loop finishes, so we do a final recount.
    col_invalid_rows: dict[str, set] = {}
    for ci in cell_issues:
        col_invalid_rows.setdefault(ci["column"], set()).add(ci["row"])
    for col in column_summary:
        column_summary[col]["invalid_count"] = len(col_invalid_rows.get(col, set()))
    # Handle columns that appear in cell_issues but not in column_summary
    # (e.g. dataset-rule columns that were marked unvalidated)
    for col, rows in col_invalid_rows.items():
        if col not in column_summary:
            column_summary[col] = {"invalid_count": len(rows), "total": total_rows}

    # ── Summary stats ─────────────────────────────────────────────────────────
    invalid_rows  = len({ci["row"] for ci in cell_issues})
    invalid_cols  = sum(1 for s in column_summary.values() if s["invalid_count"] > 0)
    total_cols    = len(file_cols)
    total_cells   = total_rows * total_cols
    # Count unique (row, col) pairs to avoid double-counting cells with multiple errors
    invalid_cells = len({(ci["row"], ci["column"]) for ci in cell_issues})

    # Column with the most invalid rows
    worst_col = None
    if column_summary:
        worst_col = max(column_summary, key=lambda c: column_summary[c]["invalid_count"])
        if column_summary[worst_col]["invalid_count"] == 0:
            worst_col = None

    # Top 5 most frequent error codes
    code_counts: dict[str, int] = {}
    for ci in cell_issues:
        code_counts[ci["code"]] = code_counts.get(ci["code"], 0) + 1
    top_issue_codes = sorted(code_counts.items(), key=lambda x: -x[1])[:5]

    from datetime import timezone
    return {
        "dataset_id":     schema.get("dataset_id", ""),
        "schema_id":      schema.get("id", ""),
        "run_at":         datetime.now(timezone.utc).isoformat(),
        "dataset_issues": dataset_issues,
        "cell_issues":    cell_issues,
        "column_summary": column_summary,
        "stats": {
            "rows":            total_rows,
            "columns":         total_cols,
            "total_cells":     total_cells,
            "invalid_cells":   invalid_cells,
            "invalid_rows":    invalid_rows,
            "invalid_columns": invalid_cols,
            "worst_column":    worst_col,
            "top_issue_codes": top_issue_codes,
        },
    }
