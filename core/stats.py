"""
Helpers to compute display-ready statistics from an issues dict.

These are lightweight functions that post-process the output of
``run_validation`` for use in the results view and export layer.
"""


def build_invalid_index(issues: dict) -> tuple[dict, dict]:
    """
    Build two lookup structures from the flat cell_issues list.

    Returns
    -------
    invalid_by_row : dict[int, dict[str, tuple[str, str]]]
        ``{row_index: {column_name: (error_code, error_message)}}``
        Only the first error per (row, column) pair is stored.  When a cell
        has multiple issues they all appear in the Issues sheet of the Excel
        export, but the results table tooltip shows only one.

    invalid_count_by_col : dict[str, int]
        ``{column_name: number_of_invalid_rows}``
        Used to render the badge counts in the column headers.
    """
    invalid_by_row: dict[int, dict[str, tuple]] = {}

    for ci in issues.get("cell_issues", []):
        row = ci["row"]
        col = ci["column"]
        # setdefault prevents overwriting an already-stored error for this cell
        invalid_by_row.setdefault(row, {}).setdefault(col, (ci["code"], ci["message"]))

    # Prefer the pre-computed counts from column_summary (available after
    # run_validation) over recomputing from cell_issues.
    column_summary = issues.get("column_summary", {})
    if column_summary:
        invalid_count_by_col = {
            col: summary["invalid_count"]
            for col, summary in column_summary.items()
            if summary["invalid_count"] > 0
        }
    else:
        # Fallback for issues dicts that pre-date the column_summary field
        counts: dict[str, set] = {}
        for ci in issues.get("cell_issues", []):
            counts.setdefault(ci["column"], set()).add(ci["row"])
        invalid_count_by_col = {col: len(rows) for col, rows in counts.items()}

    return invalid_by_row, invalid_count_by_col


def get_invalid_row_set(issues: dict) -> set[int]:
    """Return the set of row indices that contain at least one cell issue."""
    return {ci["row"] for ci in issues.get("cell_issues", [])}
