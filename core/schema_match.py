"""
Template-to-file column matching and schema resolution.

Three public functions:

match_template_to_columns
    Compare a template's column list against the actual file columns using
    case-insensitive name matching (including aliases).  Returns a summary of
    which columns matched, which are missing, which are extra, and which need
    manual resolution.

build_resolved_schema
    Given a mapping of template-column → file-column, produce a flat schema
    dict keyed by file-column name.  Unmatched file columns are included as
    "unvalidated" entries so they appear in the results view.

build_manual_schema
    Build a minimal schema from the user's manual type/empty selections on the
    schema configuration page.
"""
from core.utils import normalize_column_name


def match_template_to_columns(template: dict, file_columns: list[str]) -> dict:
    """
    Match template column definitions to file column names.

    Matching is case-insensitive and also checks each column's ``aliases``
    list so that minor naming variations (e.g. "Order ID" vs "order_id") are
    handled automatically.

    Returns
    -------
    dict with keys:
        matched    : {template_col_name: file_col_name}
        missing    : [template_col_name]   — required columns not found in file
        extra      : [file_col_name]       — file columns not in template
        unresolved : [template_col_name]   — any unmatched template columns
                     (includes both required and optional)
    """
    # Build a normalised lookup so matching is case-insensitive
    norm_file: dict[str, str] = {normalize_column_name(c): c for c in file_columns}

    matched:            dict[str, str] = {}
    unmatched_template: list[str]      = []

    for col_def in template.get("columns", []):
        tname = col_def["name"]
        # Try the canonical name first, then any registered aliases
        candidates = [tname] + col_def.get("aliases", [])
        found = None
        for cand in candidates:
            norm_cand = normalize_column_name(cand)
            if norm_cand in norm_file:
                found = norm_file[norm_cand]
                break
        if found:
            matched[tname] = found
        else:
            unmatched_template.append(tname)

    # Extra columns = file columns not matched to any template column
    matched_file_cols = set(matched.values())
    extra = [c for c in file_columns if c not in matched_file_cols]

    # Missing = unmatched columns that the template marks as required
    missing = [
        col_def["name"]
        for col_def in template.get("columns", [])
        if col_def["name"] in unmatched_template and col_def.get("required", False)
    ]

    # Unresolved = all unmatched template columns (required and optional)
    unresolved = [
        col_def["name"]
        for col_def in template.get("columns", [])
        if col_def["name"] in unmatched_template
    ]

    return {
        "matched":    matched,
        "missing":    missing,
        "extra":      extra,
        "unresolved": unresolved,
    }


def build_resolved_schema(
    template: dict,
    column_mapping: dict[str, str],
    file_columns: list[str],
) -> dict:
    """
    Construct the runtime validation schema from a resolved column mapping.

    Parameters
    ----------
    template       : the template dict (as loaded from disk)
    column_mapping : {template_col_name → file_col_name} — may be a subset of
                     the template columns if some were skipped
    file_columns   : all column names present in the uploaded file

    Returns a schema dict whose ``columns`` sub-dict is keyed by file column
    name.  File columns not present in *column_mapping* are included as
    ``unvalidated`` entries so they appear in the results view without rules.
    """
    columns: dict[str, dict] = {}

    for col_def in template.get("columns", []):
        tname = col_def["name"]
        fname = column_mapping.get(tname)
        if fname:
            # Copy the template column def and annotate with both names
            entry                    = dict(col_def)
            entry["file_column"]     = fname
            entry["template_column"] = tname
            columns[fname]           = entry

    # Mark unmatched file columns as unvalidated
    matched_file = set(column_mapping.values())
    for fc in file_columns:
        if fc not in matched_file:
            columns[fc] = {
                "name":            fc,
                "file_column":     fc,
                "template_column": None,
                "type":            "string",
                "required":        False,
                "nullable":        True,
                "unvalidated":     True,
                "rules":           {},
            }

    return {
        "id":          template.get("id", "unknown"),
        "name":        template.get("name", ""),
        "strict":      template.get("strict", False),
        "null_values": template.get("null_values", []),
        "columns":     columns,
    }


def build_manual_schema(
    dataset_id: str,
    file_columns: list[str],
    type_map: dict[str, str],
    empty_map: dict[str, str] | None = None,
) -> dict:
    """
    Build a schema from the manual column-type selections made in the UI.

    Parameters
    ----------
    dataset_id  : used to generate a unique schema id
    file_columns: ordered list of all columns in the file
    type_map    : {col_name: type_string} — e.g. {"date_col": "date"}
    empty_map   : {col_name: "allowed"|"error"} — whether empty cells are
                  permitted.  Defaults to "allowed" for unspecified columns.

    Note: additional rules (regex, min/max, formats, allowed values) are
    injected by the caller (app.py) after this function returns.
    """
    columns: dict[str, dict] = {}

    for col in file_columns:
        empty    = (empty_map or {}).get(col, "allowed")
        required = empty == "error"   # "Error if empty" → the column is required
        nullable = not required       # required columns must not be null

        columns[col] = {
            "name":            col,
            "file_column":     col,
            "template_column": None,
            "type":            type_map.get(col, "string"),
            "required":        required,
            "nullable":        nullable,
            "unvalidated":     False,
            "rules":           {},   # rules injected by app.py after this call
        }

    return {
        "id":          f"manual-{dataset_id}",
        "name":        "Manual Schema",
        "strict":      False,
        "null_values": [],
        "columns":     columns,
    }
