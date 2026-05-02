"""
File ingestion — CSV, XLSX/XLS, JSON, XML.

Every ingest function returns a ``(df, meta)`` tuple where:
- ``df``   is an all-string DataFrame (dtype=str, NaN replaced with "")
- ``meta`` is a plain dict with at least ``rows``, ``cols``, and ``columns``

Keeping every cell as a string avoids pandas type inference surprises and
ensures the validation engine always receives plain string input.
"""
import csv
import io
import json as _json
import logging
import pandas as pd
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# Encodings attempted in order when no encoding is specified for CSV files
ENCODINGS_TO_TRY = ["utf-8", "utf-8-sig", "latin-1"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_delimiter(filepath: str, encoding: str) -> str:
    """
    Use csv.Sniffer to detect the delimiter of a CSV file.
    Falls back to comma if detection fails or is ambiguous.
    """
    try:
        with open(filepath, "r", encoding=encoding, errors="replace") as f:
            sample = f.read(8192)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        return ","


def _clean_string_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Normalise a string DataFrame produced by ``astype(str)`` or
    ``pd.read_*(..., dtype=str)``.

    - Replaces the literal strings "None", "NaN", "<NA>", and "nan" with ""
      so that missing values propagate as empty strings rather than
      looking like real data values.
    - Strips leading/trailing whitespace from column names.
    """
    # These strings appear when pandas converts its internal NA sentinels to str
    NA_STRINGS = {"None", "NaN", "<NA>", "nan"}
    df = df.replace(NA_STRINGS, "")
    df.columns = [str(c).strip() for c in df.columns]
    return df


# ── CSV ───────────────────────────────────────────────────────────────────────

def ingest_csv(
    filepath: str,
    encoding: str | None = None,
    header_row: int = 0,
) -> tuple[pd.DataFrame, dict]:
    """
    Read a CSV file into an all-string DataFrame.

    Tries each encoding in ``ENCODINGS_TO_TRY`` until one succeeds.
    The delimiter is auto-detected via csv.Sniffer.
    """
    meta      = {}
    encodings = [encoding] if encoding else ENCODINGS_TO_TRY
    df        = None

    for enc in encodings:
        try:
            delimiter = _detect_delimiter(filepath, enc)
            df = pd.read_csv(
                filepath,
                dtype=str,
                encoding=enc,
                keep_default_na=False,   # prevent pandas from turning "NA" into NaN
                header=header_row,
                sep=delimiter,
            )
            meta["encoding_used"] = enc
            meta["delimiter"]     = delimiter
            break
        except (UnicodeDecodeError, Exception) as exc:
            logger.warning("CSV encoding %s failed: %s", enc, exc)

    if df is None:
        raise ValueError("Could not parse CSV with any attempted encoding.")

    df = _clean_string_df(df)
    meta["rows"]       = len(df)
    meta["cols"]       = len(df.columns)
    meta["columns"]    = list(df.columns)
    meta["header_row"] = header_row
    return df, meta


# ── XLSX / XLS ────────────────────────────────────────────────────────────────

def ingest_xlsx(
    filepath: str,
    sheet_name=0,
    header_row: int = 0,
) -> tuple[pd.DataFrame, dict]:
    """
    Read an Excel file into an all-string DataFrame.

    ``sheet_name`` may be an integer index or a sheet-name string.
    Raises ``IndexError`` / ``ValueError`` from pandas if the sheet does not
    exist — the caller in app.py should guard accordingly.
    """
    xl          = pd.ExcelFile(filepath)
    sheet_names = xl.sheet_names

    df = pd.read_excel(
        filepath,
        sheet_name=sheet_name,
        dtype=str,
        keep_default_na=False,
        header=header_row,
    )

    df = _clean_string_df(df)

    # Resolve the sheet index to a name for display purposes
    used_name = (sheet_names[sheet_name]
                 if isinstance(sheet_name, int) and sheet_name < len(sheet_names)
                 else sheet_name)

    meta = {
        "sheet_names": sheet_names,
        "sheet_used":  used_name,
        "rows":        len(df),
        "cols":        len(df.columns),
        "columns":     list(df.columns),
        "header_row":  header_row,
    }
    return df, meta


# ── JSON ──────────────────────────────────────────────────────────────────────

def ingest_json(filepath: str) -> tuple[pd.DataFrame, dict]:
    """
    Parse a JSON file into a flat all-string DataFrame.

    Accepted structures:
    - A top-level JSON array of objects.
    - An object with a recognised list key (data / records / rows / items /
      results / values) — the first matching key is used.
    - A single JSON object (produces a one-row table).
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        data = _json.load(f)

    WRAPPER_KEYS = ("data", "records", "rows", "items", "results", "values")

    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = None
        for key in WRAPPER_KEYS:
            if key in data and isinstance(data[key], list):
                records = data[key]
                break
        if records is None:
            records = [data]   # single-object → one-row table
    else:
        raise ValueError("JSON root must be an array or object.")

    # Build DataFrame — fillna before astype(str) to avoid "NaN" strings
    df = pd.DataFrame(records).fillna("").astype(str)
    df = _clean_string_df(df)

    meta = {
        "rows":    len(df),
        "cols":    len(df.columns),
        "columns": list(df.columns),
    }
    return df, meta


# ── XML ───────────────────────────────────────────────────────────────────────

def ingest_xml(filepath: str, record_path: str | None = None) -> tuple[pd.DataFrame, dict]:
    """
    Parse an XML file into a flat all-string DataFrame.

    ``record_path`` is a dot-separated path to the repeated elements that
    represent rows (e.g. ``"root.records.record"``).  When omitted, the
    deepest repeated element tag is auto-detected.
    """
    tree = ET.parse(filepath)
    root = tree.getroot()

    if record_path:
        elements = _resolve_path(root, record_path)
    else:
        elements, record_path = _auto_detect_records(root)

    if not elements:
        raise ValueError(f"No elements found at record_path='{record_path}'")

    rows = []
    for elem in elements:
        row: dict[str, str] = {}
        # Child element text → column
        for child in elem:
            tag       = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            row[tag]  = (child.text or "").strip()
        # Element attributes → columns prefixed with "@"
        for k, v in elem.attrib.items():
            row[f"@{k}"] = v
        rows.append(row)

    # fillna before astype(str) to prevent "NaN" from appearing in cells where
    # a tag was absent for some elements but present for others
    df = pd.DataFrame(rows).fillna("").astype(str)
    df = _clean_string_df(df)

    meta = {
        "record_path": record_path,
        "rows":        len(df),
        "cols":        len(df.columns),
        "columns":     list(df.columns),
    }
    return df, meta


# ── Peek (header-row picker) ──────────────────────────────────────────────────

def peek_rows(
    filepath: str,
    file_type: str,
    encoding: str | None = None,
    sheet_name=0,
    n: int = 12,
) -> list[list[str]]:
    """
    Read the first *n* physical rows of a file **without** promoting any row
    to a header.  Used by the header-row picker UI.

    Returns a list of lists (row 0 = first row in the file).
    Returns an empty list on any read failure so the UI degrades gracefully.
    """
    if file_type == "csv":
        encodings = [encoding] if encoding else ENCODINGS_TO_TRY
        for enc in encodings:
            try:
                delimiter = _detect_delimiter(filepath, enc)
                df = pd.read_csv(
                    filepath,
                    dtype=str,
                    encoding=enc,
                    keep_default_na=False,
                    header=None,   # no header promotion
                    nrows=n,
                    sep=delimiter,
                )
                return df.fillna("").values.tolist()
            except Exception as exc:
                logger.debug("peek_rows CSV encoding %s failed: %s", enc, exc)
        return []

    elif file_type in ("xlsx", "xls"):
        try:
            df = pd.read_excel(
                filepath,
                sheet_name=sheet_name,
                dtype=str,
                keep_default_na=False,
                header=None,
                nrows=n,
            )
            return df.fillna("").values.tolist()
        except Exception as exc:
            logger.warning("peek_rows XLSX failed: %s", exc)
            return []

    elif file_type == "json":
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                data = _json.load(f)
            WRAPPER_KEYS = ("data", "records", "rows", "items", "results", "values")
            if isinstance(data, list):
                records = data[:n]
            elif isinstance(data, dict):
                records = None
                for key in WRAPPER_KEYS:
                    if key in data and isinstance(data[key], list):
                        records = data[key][:n]
                        break
                if records is None:
                    records = [data]
            else:
                return []
            df = pd.DataFrame(records).fillna("").astype(str)
            return df.values.tolist()
        except Exception as exc:
            logger.warning("peek_rows JSON failed: %s", exc)
            return []

    # XML: column names come from element tags, not row positions —
    # the header-picker UI is not shown for XML files.
    return []


# ── XML helpers ───────────────────────────────────────────────────────────────

def _resolve_path(root: ET.Element, dot_path: str) -> list[ET.Element]:
    """Walk *root* following the dot-separated *dot_path* and return all matching elements."""
    parts = dot_path.strip().split(".")
    # Skip the root tag if it is the first segment
    if parts and root.tag.split("}")[-1] == parts[0]:
        parts = parts[1:]
    current = [root]
    for part in parts:
        next_level: list[ET.Element] = []
        for elem in current:
            next_level.extend(elem.findall(part))
        current = next_level
    return current


def _auto_detect_records(root: ET.Element) -> tuple[list[ET.Element], str]:
    """
    Walk the XML tree depth-first to find the first level that contains
    multiple sibling elements with the same tag — those are the records.

    Falls back to the direct children of the root if no repeated level is found.
    """
    def _walk(elem, path):
        children = list(elem)
        if not children:
            return [], path
        tags = [c.tag.split("}")[-1] for c in children]
        if len(tags) > 1 and len(set(tags)) == 1:
            # All children share the same tag → treat them as records
            record_tag = tags[0]
            full_path  = f"{path}.{record_tag}" if path else record_tag
            return children, full_path
        # Recurse into each child looking for a repeated level
        for child in children:
            child_tag  = child.tag.split("}")[-1]
            child_path = f"{path}.{child_tag}" if path else child_tag
            result, found_path = _walk(child, child_path)
            if result:
                return result, found_path
        return [], path

    root_tag          = root.tag.split("}")[-1]
    elements, path    = _walk(root, root_tag)

    if not elements:
        # Fallback: use direct children of the root as records
        elements  = list(root)
        first_tag = elements[0].tag.split("}")[-1] if elements else "record"
        path      = f"{root_tag}.{first_tag}"

    return elements, path


def get_xml_record_path_candidates(filepath: str) -> list[str]:
    """
    Return a list of candidate dot-paths for the user to choose from when
    the auto-detection is ambiguous.  Used by the XML record-path picker UI.
    """
    tree = ET.parse(filepath)
    root = tree.getroot()
    candidates: list[str] = []

    def _walk(elem, path, depth=0):
        if depth > 6:   # cap recursion depth to avoid pathological XML files
            return
        children = list(elem)
        tags     = [c.tag.split("}")[-1] for c in children]
        if len(tags) > 1 and len(set(tags)) == 1:
            candidates.append(f"{path}.{tags[0]}" if path else tags[0])
        for child in children:
            child_tag  = child.tag.split("}")[-1]
            child_path = f"{path}.{child_tag}" if path else child_tag
            _walk(child, child_path, depth + 1)

    root_tag = root.tag.split("}")[-1]
    _walk(root, root_tag)
    return candidates or [root_tag]
