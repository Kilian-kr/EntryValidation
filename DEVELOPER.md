# Developer Reference — DataValidator

This document is the technical reference for developers working on or extending DataValidator. It covers architecture, module internals, data structures, extension points, and known limitations.

> **AI Disclosure** — This documentation was generated with AI assistance and reviewed for factual correctness against the source code.

---

## Table of Contents

1. [Architecture overview](#1-architecture-overview)
2. [Request lifecycle](#2-request-lifecycle)
3. [Module reference](#3-module-reference)
4. [Data structures](#4-data-structures)
5. [Validation engine](#5-validation-engine)
6. [Type inference](#6-type-inference)
7. [Template system](#7-template-system)
8. [Front-end architecture](#8-front-end-architecture)
9. [Extension points](#9-extension-points)
10. [Error handling patterns](#10-error-handling-patterns)
11. [Known limitations and technical debt](#11-known-limitations-and-technical-debt)

---

## 1. Architecture overview

DataValidator is a single-process Flask application with no database. All persistent state is stored as flat files on the local filesystem. There are no background workers — every operation is synchronous and triggered by an HTTP request.

```
Browser
  │
  │  HTTP
  ▼
app.py  (Flask routes)
  │
  ├── core/ingestion.py       Read file → all-string DataFrame
  ├── core/type_inference.py  Guess column types from data
  ├── core/schema_match.py    Match template columns to file columns
  ├── core/validation.py      Run rules against the DataFrame
  ├── core/stats.py           Post-process issues for display
  ├── core/export.py          Generate CSV / XLSX downloads
  ├── core/storage.py         Read/write files to disk
  ├── core/templates_repo.py  Load / cache / save template JSON files
  └── core/utils.py           Shared helpers
```

The application is entirely **stateless between requests** — every route loads what it needs from disk, does its work, and returns. The only in-memory state is the template cache in `templates_repo.py`, which is rebuilt from disk on first access.

---

## 2. Request lifecycle

### 2.1 Upload

```
POST /upload
  ├─ Validate extension against ALLOWED_EXTENSIONS
  ├─ Generate dataset_id = uuid4()
  ├─ Save uploaded file → data/uploads/<id>/<filename>
  ├─ ingestion.ingest_*(filepath) → (df, meta)
  │     All cells are string dtype; NaN/None → ""
  ├─ storage.save_raw(id, df)     → data/derived/<id>/raw.parquet
  ├─ storage.save_meta(id, meta)  → data/derived/<id>/meta.json
  └─ redirect → GET /schema/<id>

  Special case — multi-sheet XLSX:
  ├─ storage.save_meta() only (no raw.parquet yet)
  └─ redirect → GET /select-sheet/<id>
       └─ POST /select-sheet/<id>
            ├─ ingest_xlsx(filepath, sheet=selected)
            ├─ storage.save_raw() + save_meta()
            └─ redirect → GET /schema/<id>
```

### 2.2 Schema configuration

```
GET /schema/<id>
  ├─ storage.load_raw()
  ├─ type_inference.infer_all(df)    (only when no schema saved yet)
  ├─ ingestion.peek_rows()           (for the header-row picker)
  └─ render schema.html

POST /schema/<id>  (mode=manual)
  ├─ schema_match.build_manual_schema(file_columns, type_map, empty_map)
  ├─ Inject per-column rules (formats, allowed, min/max, regex) from form
  ├─ storage.save_schema()  → data/derived/<id>/schema.json
  └─ redirect → POST /validate/<id>

POST /schema/<id>  (mode=template)
  ├─ schema_match.match_template_to_columns(template, file_columns)
  │     Auto-match by name + aliases (case-insensitive)
  ├─ Apply manual overrides for unresolved columns from form
  ├─ If required columns still unresolved → re-render with mapping UI
  ├─ schema_match.build_resolved_schema(template, column_mapping, file_columns)
  ├─ storage.save_schema()
  └─ redirect → POST /validate/<id>
```

### 2.3 Validation

```
POST /validate/<id>
  ├─ storage.load_raw() + load_schema()
  ├─ validation.run_validation(df, schema) → issues dict
  │     Pass 1: dataset-level checks (missing/extra columns)
  │     Pass 2: per-column cell loop (type + rules)
  │     Pass 3: dataset rules (unique_together)
  │     Pass 4: recount invalid_count per column
  ├─ storage.save_issues()  → data/derived/<id>/issues.json
  └─ redirect → GET /results/<id>
```

### 2.4 Results display

```
GET /results/<id>
  ├─ storage.load_raw() + load_issues()
  ├─ stats.build_invalid_index(issues)
  ├─ Apply toolbar filters (mode, cols, error_code)
  └─ render results.html  (table body is empty — data loaded via JS)

GET /results/<id>/rows?offset=0&limit=100&mode=all&cols=all&error_code=
  ├─ Same filter logic as above
  ├─ Slice row_indices[offset:offset+limit]
  └─ Return JSON  {rows: [{idx, cells: [{v, i, c, m}]}], total, offset, limit}
```

---

## 3. Module reference

### `app.py`

Flask application entry point. Contains all route handlers. No business logic — every handler delegates immediately to a `core/` module.

**Route table:**

| Method | URL | Handler | Description |
|--------|-----|---------|-------------|
| GET | `/` | `index` | Upload form |
| GET/POST | `/upload` | `upload_form` / `upload_file` | File upload |
| GET/POST | `/select-sheet/<id>` | `select_sheet` / `select_sheet_post` | Sheet picker (multi-sheet XLSX) |
| POST | `/dataset/<id>/set-header` | `set_header` | Re-ingest with different header row |
| GET/POST | `/schema/<id>` | `schema_select` / `schema_submit` | Schema configuration |
| GET/POST | `/validate/<id>` | `validate_dataset` | Run validation |
| GET | `/results/<id>` | `results` | Results page |
| GET | `/results/<id>/rows` | `results_rows` | JSON rows (infinite scroll) |
| GET | `/download/<id>/issues.csv` | `download_issues` | Download issues CSV |
| GET | `/download/<id>/wrong_rows.csv` | `download_wrong_rows` | Download invalid rows CSV |
| GET | `/download/<id>/validation.xlsx` | `download_issues_xlsx` | Download annotated Excel |
| POST | `/dataset/<id>/revalidate` | `revalidate` | Re-run with stored schema |
| POST | `/dataset/<id>/save-as-template` | `save_as_template` | Save schema as template |
| GET | `/templates` | `template_list` | Template manager |
| POST | `/templates/reload` | `template_reload` | Reload templates from disk |
| GET/POST | `/templates/new` | `template_new` / `template_new_post` | Create template |
| GET/POST | `/templates/import` | `template_import` / `template_import_post` | Import template JSON |
| GET | `/templates/<tid>/export` | `template_export` | Download template JSON |
| POST | `/templates/<tid>/duplicate` | `template_duplicate` | Duplicate template |
| POST | `/templates/<tid>/delete` | `template_delete` | Delete template |
| GET | `/templates/<tid>/edit` | `template_edit` | Edit template JSON |

---

### `config.py`

Application-wide constants read from environment variables. Import directly wherever needed — do not pass config values through function arguments.

| Name | Type | Description |
|------|------|-------------|
| `BASE_DIR` | `str` | Absolute path of the project root |
| `UPLOADS_DIR` | `str` | `data/uploads/` |
| `DERIVED_DIR` | `str` | `data/derived/` |
| `TEMPLATES_DEFINITIONS_DIR` | `str` | `templates_definitions/` |
| `MAX_UPLOAD_BYTES` | `int` | Max upload size (default 100 MB) |
| `ALLOWED_EXTENSIONS` | `set` | `{"csv","xlsx","xls","xml","json"}` |
| `NULL_VALUES` | `list[str]` | Global null tokens applied to every dataset |
| `SECRET_KEY` | `str` | Flask session secret (used for flash messages) |

---

### `core/ingestion.py`

Reads source files into all-string DataFrames. Every public function returns `(df, meta)`.

**Contract every ingestion function must satisfy:**
- All cell values are `str` dtype.
- `NaN`, `None`, `"NaN"`, `"<NA>"`, `"None"` are replaced with `""` via `_clean_string_df()`.
- `meta` contains at minimum: `rows` (int), `cols` (int), `columns` (list of str).
- Column names are stripped of leading/trailing whitespace.

**Public API:**

```python
ingest_csv(filepath, encoding=None, header_row=0) → (df, meta)
ingest_xlsx(filepath, sheet_name=0, header_row=0) → (df, meta)
ingest_json(filepath) → (df, meta)
ingest_xml(filepath, record_path=None) → (df, meta)
peek_rows(filepath, file_type, encoding=None, sheet_name=0, n=12) → list[list[str]]
get_xml_record_path_candidates(filepath) → list[str]
```

`peek_rows` reads without promoting any row to a header — used exclusively by the header-row picker UI. It returns an empty list on failure rather than raising, so the picker degrades gracefully.

---

### `core/type_inference.py`

Analyses a sample of each column and returns a best-guess type dict. Results are passed to the schema configuration template as `inferred_types` to pre-populate dropdowns. They are **never saved to disk** — the user always has the opportunity to override.

```python
infer_column(series: pd.Series) → dict
# Returns one of:
# {"type": "string"}
# {"type": "integer"}
# {"type": "number"}
# {"type": "boolean"}
# {"type": "date",     "formats": ["%Y-%m-%d"]}
# {"type": "datetime", "formats": ["%Y-%m-%d %H:%M:%S"]}
# {"type": "enum",     "allowed": ["A", "B", "C"]}

infer_all(df: pd.DataFrame) → dict[str, dict]
# Returns {col_name: infer_column(series)} for every column.
# Errors in individual columns are caught and logged; the column falls back to string.
```

---

### `core/schema_match.py`

Handles two concerns: matching template columns to file columns, and building the runtime schema dict from either a template or manual selections.

```python
match_template_to_columns(template, file_columns) → {
    "matched":    {template_col: file_col},
    "missing":    [template_col],   # required, not found
    "extra":      [file_col],       # in file, not in template
    "unresolved": [template_col],   # any unmatched (required or optional)
}

build_resolved_schema(template, column_mapping, file_columns) → schema_dict
# column_mapping: {template_col_name → file_col_name}
# Unmatched file columns become unvalidated entries.

build_manual_schema(dataset_id, file_columns, type_map, empty_map) → schema_dict
# type_map:  {col: "date"|"integer"|...}
# empty_map: {col: "allowed"|"error"}
# Rules (formats, min/max, etc.) are injected by app.py after this call.
```

Column matching is case-insensitive and checks both the canonical `name` and the `aliases` list of each template column.

---

### `core/validation.py`

The core rules engine. Contains type parsers, a rule checker, a cell validator, and the main dataset validator.

```python
# Low-level type parsers — return (ok: bool, parsed_value | None)
_parse_integer(value_str) → (bool, int | None)
_parse_number(value_str)  → (bool, float | None)
_parse_boolean(value_str) → (bool, None)
_parse_date(value_str, formats) → (bool, datetime | None)

# Rule engine — called after type parsing succeeds
_apply_rules(value_str, parsed_num, parsed_date, col_type, rules) → [(code, message)]

# Cell-level entry point
validate_cell(value_str, col_def, null_tokens) → [(code, message)]

# Dataset-level entry point
run_validation(df, schema) → issues_dict
```

All issue codes are module-level string constants:

| Constant | Meaning |
|----------|---------|
| `REQUIRED_EMPTY` | Null value in a required/non-nullable column |
| `TYPE_MISMATCH` | Value cannot be parsed as the declared type |
| `DATE_PARSE_FAILED` | Date/datetime string matches no declared format |
| `REGEX_MISMATCH` | String does not match the `regex` rule |
| `MIN_LEN_VIOLATED` | String shorter than `min_len` |
| `MAX_LEN_VIOLATED` | String longer than `max_len` |
| `MIN_VIOLATED` | Numeric/date value below `min` / `min_date` |
| `MAX_VIOLATED` | Numeric/date value above `max` / `max_date` |
| `ENUM_NOT_ALLOWED` | Value not in the `allowed` list |
| `NOT_IN_VIOLATED` | Value appears in the `not_in` blocklist |
| `UNIQUE_VIOLATED` | Duplicate value in a `unique: true` column |
| `UNIQUE_TOGETHER_VIOLATED` | Duplicate composite key |
| `MISSING_COLUMN` | Required column absent from the file |
| `EXTRA_COLUMN` | Unexpected column (strict mode only) |
| `PARSE_ERROR` | Internal exception during validation |

---

### `core/stats.py`

Lightweight post-processing of the issues dict for display purposes.

```python
build_invalid_index(issues) → (invalid_by_row, invalid_count_by_col)
# invalid_by_row:       {row_idx: {col_name: (code, message)}}
# invalid_count_by_col: {col_name: int}

get_invalid_row_set(issues) → set[int]
```

`invalid_by_row` stores only the **first** error per (row, column) pair — used to render the tooltip in the results table. The full list of errors is always available in `issues["cell_issues"]`.

---

### `core/export.py`

Generates downloadable artefacts from a `(df, issues)` pair.

```python
issues_to_csv(issues) → bytes         # UTF-8 CSV of all cell issues
wrong_rows_to_csv(df, issues) → bytes  # CSV of only the invalid rows
issues_to_xlsx(df, issues) → bytes     # Two-sheet annotated Excel workbook
```

The Excel export uses `df.iterrows()` (not `itertuples`) to avoid column-name mangling for columns that contain spaces or other non-identifier characters.

---

### `core/storage.py`

Thin read/write wrappers around the filesystem. Every `load_*` function raises `FileNotFoundError` when the target file does not exist — callers use this to distinguish "not found" from genuine IO errors.

```python
# Directories
dataset_upload_dir(dataset_id) → str   # creates if missing
dataset_derived_dir(dataset_id) → str  # creates if missing

# Meta
save_meta(dataset_id, meta)
load_meta(dataset_id) → dict

# Raw data (Parquet — requires pyarrow)
save_raw(dataset_id, df)
load_raw(dataset_id) → pd.DataFrame

# Schema
save_schema(dataset_id, schema)
load_schema(dataset_id) → dict
schema_exists(dataset_id) → bool

# Issues
save_issues(dataset_id, issues)
load_issues(dataset_id) → dict
issues_exist(dataset_id) → bool
```

---

### `core/templates_repo.py`

Manages an in-memory cache of template dicts loaded from `templates_definitions/*.json`. The cache is a module-level dict `_templates` and is rebuilt from disk whenever `reload_templates()` is called.

```python
reload_templates() → dict[str, dict]  # scans disk, validates, rebuilds cache
get_all_templates() → dict[str, dict] # loads on first call
get_template(template_id) → dict | None
save_template(data) → (bool, [errors])
delete_template(template_id) → bool
duplicate_template(template_id) → dict | None  # auto-generates a unique copy id
```

Template IDs are sanitised by `_safe_filename()` before being used as filenames to prevent path traversal attacks.

---

### `core/template_validator.py`

Validates a template dict against `core/template_schema.json` (a JSON Schema Draft 7 document) using the `jsonschema` library.

```python
validate_template_json(data: dict) → list[str]
# Returns human-readable error messages; empty list = valid.
```

The schema is loaded at import time. If `template_schema.json` is missing or malformed, a `RuntimeError` is raised at startup.

---

### `core/utils.py`

Shared helpers used across the `core/` package.

```python
slugify(value) → str             # URL-safe ASCII slug
safe_str(v) → str                # str(v) or "" for None
is_null_value(value_str, null_tokens) → bool   # single call
make_null_set(null_tokens) → frozenset[str]    # pre-compute for tight loops
normalize_column_name(name) → str              # lowercase + strip
```

Use `make_null_set()` instead of calling `is_null_value()` in a per-cell loop — it pre-computes the frozenset once so membership tests are O(1) with no per-call allocation.

---

## 4. Data structures

### 4.1 `meta.json`

```json
{
  "file_type":     "csv",
  "filename":      "orders.csv",
  "filepath":      "/absolute/path/to/orders.csv",
  "dataset_id":    "3fa85f64-...",
  "header_row":    0,
  "rows":          1000,
  "cols":          7,
  "columns":       ["order_id", "date", "status", "..."],
  "encoding_used": "utf-8",
  "delimiter":     ","
}
```

Additional keys for XLSX: `sheet_names`, `sheet_used`.
Additional keys for XML: `record_path`.

---

### 4.2 `schema.json`

```json
{
  "id":          "manual-<dataset_id>",
  "name":        "Manual Schema",
  "strict":      false,
  "null_values": [],
  "dataset_id":  "<dataset_id>",
  "columns": {
    "order_id": {
      "name":            "order_id",
      "file_column":     "order_id",
      "template_column": null,
      "type":            "integer",
      "required":        true,
      "nullable":        false,
      "unvalidated":     false,
      "rules": {
        "min": 1
      }
    },
    "notes": {
      "name":        "notes",
      "file_column": "notes",
      "type":        "string",
      "unvalidated": true,
      "rules":       {}
    }
  }
}
```

`columns` is keyed by **file column name**. `template_column` is non-null only when a template was used. `unvalidated: true` marks columns that appear in the file but were not configured — they are displayed in the results but no rules are checked.

All available rule keys:

| Key | Types | Description |
|-----|-------|-------------|
| `regex` | string | Full-match regular expression |
| `min_len` | string | Minimum string length |
| `max_len` | string | Maximum string length |
| `min` | integer, number | Minimum value (inclusive) |
| `max` | integer, number | Maximum value (inclusive) |
| `formats` | date, datetime | List of strptime format strings |
| `min_date` | date, datetime | Earliest allowed date (strptime format set by `date_format`) |
| `max_date` | date, datetime | Latest allowed date |
| `date_format` | date, datetime | strptime format for `min_date`/`max_date` boundaries (default `%Y-%m-%d`) |
| `allowed` | enum | List of permitted values (case-insensitive) |
| `not_in` | all | Blocklist of forbidden values |
| `unique` | all | No duplicate values allowed in this column |

---

### 4.3 `issues.json`

```json
{
  "dataset_id": "<dataset_id>",
  "schema_id":  "manual-<dataset_id>",
  "run_at":     "2024-01-15T10:30:00+00:00",

  "dataset_issues": [
    {
      "code":    "MISSING_COLUMN",
      "column":  "order_id",
      "message": "Required column 'order_id' is missing from the file"
    }
  ],

  "cell_issues": [
    {
      "row":     4,
      "column":  "price",
      "code":    "TYPE_MISMATCH",
      "message": "Expected number, got 'N/A'"
    }
  ],

  "column_summary": {
    "price": {
      "invalid_count": 3,
      "total":         1000,
      "numeric_stats": {
        "count": 997,
        "min":   0.5,
        "max":   999.99,
        "mean":  42.381,
        "std":   18.204
      }
    }
  },

  "stats": {
    "rows":            1000,
    "columns":         7,
    "total_cells":     7000,
    "invalid_cells":   3,
    "invalid_rows":    3,
    "invalid_columns": 1,
    "worst_column":    "price",
    "top_issue_codes": [["TYPE_MISMATCH", 3]]
  }
}
```

**Important:** `row` in `cell_issues` is the **0-based DataFrame index**, not the 1-based display number shown in the UI.

---

### 4.4 Template JSON (stored in `templates_definitions/`)

```json
{
  "id":          "orders-v1",
  "name":        "Orders Template",
  "version":     "1.0",
  "description": "Standard order export format",
  "strict":      false,
  "null_values": ["N/A", "TBD"],
  "dataset_rules": [
    { "type": "unique_together", "columns": ["order_id", "line_item"] }
  ],
  "columns": [
    {
      "name":     "order_id",
      "aliases":  ["Order ID", "OrderID", "id"],
      "type":     "integer",
      "required": true,
      "nullable": false,
      "rules":    { "min": 1 }
    },
    {
      "name":     "order_date",
      "type":     "date",
      "required": true,
      "nullable": false,
      "rules":    { "formats": ["%Y-%m-%d", "%d/%m/%Y"] }
    },
    {
      "name":     "status",
      "type":     "enum",
      "required": false,
      "nullable": true,
      "rules":    { "allowed": ["pending", "shipped", "delivered", "cancelled"] }
    }
  ]
}
```

The full JSON Schema that validates this format is in `core/template_schema.json`.

---

## 5. Validation engine

### 5.1 Cell validation flow

`validate_cell(value_str, col_def, null_tokens)` runs in three sequential stages:

```
value_str
    │
    ▼
1. NULL CHECK
   value.strip().lower() in null_set?
       yes → required/nullable? → REQUIRED_EMPTY or []
       no  → continue
    │
    ▼
2. TYPE PARSE
   col_type = col_def["type"]
   match col_type:
       "integer"  → int(value)         fails → TYPE_MISMATCH
       "number"   → float(value)       fails → TYPE_MISMATCH
       "boolean"  → token in set       fails → TYPE_MISMATCH
       "date"     → strptime(formats)  fails → DATE_PARSE_FAILED
       "datetime" → strptime(formats)  fails → DATE_PARSE_FAILED
       "enum"     → value in allowed   fails → ENUM_NOT_ALLOWED
                    then _apply_rules() for not_in check
       "string"   → always passes type check
    │
    ▼
3. RULE CHECK  (_apply_rules)
   string:           regex, min_len, max_len
   integer/number:   min, max
   date/datetime:    min_date, max_date
   all types:        not_in blocklist
```

### 5.2 Dataset validation passes

`run_validation(df, schema)` runs four passes over the data:

**Pass 1 — Dataset pre-checks**
Checks for missing required columns and (in strict mode) extra columns. These produce `dataset_issues`, not `cell_issues`.

**Pass 2 — Per-column cell loop**
Iterates every column × every row. For each cell, calls `validate_cell()`. Also handles:
- The `unique` rule (maintains a per-column `seen_values` set)
- Numeric stats accumulation (mean, std, min, max)

**Pass 3 — Dataset rules**
Currently only `unique_together`. Iterates all rows and checks composite key uniqueness across multiple columns.

**Pass 4 — Recount**
Recomputes `invalid_count` in `column_summary` from the final `cell_issues` list. This ensures that issues added in Pass 3 are reflected in the counts.

### 5.3 Null handling

Null detection works as follows:

1. The cell value is stripped and lowercased.
2. It is checked against the merged set of: schema-level `null_values` + global `NULL_VALUES` from `config.py`.
3. If it matches, the cell is considered null. Whether that is an error depends on `required` and `nullable` in the column definition.

Global default null tokens: `""`, `" "`, `"NA"`, `"N/A"`, `"NULL"`, `"null"`, `"n/a"`, `"na"`, `"-"`, `"--"`.

Schemas cannot currently *remove* tokens from the global list — they can only add to it.

---

## 6. Type inference

`infer_column(series)` samples up to 500 non-null values and applies a 95% match-rate threshold. The detection order is fixed and significant:

```
1. integer   — checked before number  (integers are a subset of numbers)
2. number    — float with isfinite check
3. datetime  — checked before date    (datetime formats are more specific)
4. date
5. boolean   — checked after integer  ("0"/"1" should be integer, not boolean)
6. enum      — ≤20 unique values + low cardinality ratio
7. string    — fallback
```

Boolean tokens are `true`, `false`, `yes`, `no` (case-insensitive). The tokens `"0"` and `"1"` are deliberately excluded so they do not interfere with integer detection.

For enum detection the thresholds are:
- `n_unique ≤ 20` AND
- Either `n_total ≤ 100` OR `n_unique / n_total ≤ 0.10`

Inference is **advisory only** — the user always sees the inferred type pre-selected in the schema UI and can change it before running validation.

---

## 7. Template system

### 7.1 Storage

Templates are individual JSON files in `templates_definitions/`. The filename is derived from the template's `id` field via `_safe_filename()`, which strips path separators and non-safe characters to prevent directory traversal.

The in-memory cache is a module-level `dict` in `templates_repo.py`. It is populated on first access and can be force-reloaded via `POST /templates/reload` or programmatically via `reload_templates()`.

### 7.2 Matching

When a user selects a template, `match_template_to_columns()` tries to match each template column to a file column by:
1. Comparing the normalised (lowercased + stripped) column `name`.
2. Comparing each entry in `aliases`.

Matching is case-insensitive but does not collapse internal whitespace — `"First Name"` and `"firstname"` will not match unless one is listed as an alias of the other.

Unmatched columns fall into one of three categories:
- **Missing** — the template marks the column as `required: true` — the user must map it manually or proceed with a warning.
- **Unresolved** — the template column was optional — it can be mapped or skipped.
- **Extra** — file columns not in the template — always included as `unvalidated` entries.

### 7.3 Strict mode

When `"strict": true` in the template, any file column not mapped to a template column generates an `EXTRA_COLUMN` dataset issue. This is useful for enforcing that no unexpected columns appear in the data.

---

## 8. Front-end architecture

The front-end is plain HTML + CSS + vanilla JavaScript. No build step, no bundler, no framework.

### 8.1 Results table (infinite scroll)

The results page renders only the page shell on the server. The table body is empty on initial load. `static/results.js` takes over:

1. Reads configuration from `window.__resultsConfig` (injected as a `<script>` block by `results.html`):
   ```js
   window.__resultsConfig = {
     rowsUrl: "/results/<id>/rows",
     params:  { mode: "all", cols: "all", error_code: "" },
     total:   1500
   };
   ```
2. Creates an `IntersectionObserver` watching a sentinel `<div>` at the bottom of the table.
3. When the sentinel becomes visible, fetches the next 100 rows from `/results/<id>/rows`.
4. Appends rows to the `<tbody>` and updates the row-count label.
5. Continues until `offset >= total`.

Each row JSON object looks like:
```json
{
  "idx": 42,
  "cells": [
    {"v": "1001",       "i": false, "c": null,           "m": null},
    {"v": "not-a-date", "i": true,  "c": "DATE_PARSE_FAILED", "m": "Expected format(s): %Y-%m-%d"}
  ]
}
```

`i` = invalid flag, `c` = error code, `m` = error message.

### 8.2 Schema page date picker

The date/datetime format selector is a custom multi-select dropdown built in vanilla JS (inside `schema.html`). Key functions:

| Function | Description |
|----------|-------------|
| `fmtVals(wrap)` | Read current selected formats from the hidden `<input>` |
| `fmtSetVals(wrap, vals)` | Write values + re-render tags + sync option highlights |
| `fmtRender(wrap)` | Render the tag pills in the trigger element |
| `fmtSyncOpts(wrap)` | Toggle the `fmt-option-on` class on list items |
| `fmtOpen(wrap)` / `fmtClose(wrap)` | Show/hide the dropdown panel |
| `updateOptions(sel)` | Called on type-select change — shows/hides the relevant options cell |

The hidden `<input name="formats_<col>">` is the actual form field submitted. The visible picker is purely cosmetic.

### 8.3 Templates

All Jinja2 templates extend `layout.html`. Key template files:

| File | Route |
|------|-------|
| `upload.html` | `/upload` |
| `select_sheet.html` | `/select-sheet/<id>` |
| `schema.html` | `/schema/<id>` |
| `results.html` | `/results/<id>` |
| `templates_list.html` | `/templates` |
| `templates_new.html` | `/templates/new`, `/templates/<id>/edit` |
| `templates_import.html` | `/templates/import` |
| `layout.html` | Base layout (nav, flash messages, CSS/JS links) |

---

## 9. Extension points

### 9.1 Adding a new validation rule

1. **Add to the JSON schema** — open `core/template_schema.json` and add the new key under `properties.columns.items.properties.rules`.
2. **Implement the check** — add the logic to `_apply_rules()` in `core/validation.py`. Return `[(ISSUE_CODE, message)]` on failure or `[]` on pass.
3. **Add an issue code** — add a module-level constant at the top of `validation.py`.
4. **Expose in the UI** — add a form field to the relevant type's `data-opt` div in `templates/schema.html`.
5. **Inject from the form** — add an `elif col_type == "..."` branch in the `schema_submit` handler in `app.py` to read the field and write it into `schema["columns"][col]["rules"]`.

### 9.2 Adding a new file type

1. **Register the extension** — add it to `ALLOWED_EXTENSIONS` in `config.py`.
2. **Write an ingestion function** in `core/ingestion.py`:
   ```python
   def ingest_mytype(filepath: str) -> tuple[pd.DataFrame, dict]:
       # Must return an all-string DataFrame with "" for missing values
       df = ...
       df = _clean_string_df(df)
       meta = {"rows": len(df), "cols": len(df.columns), "columns": list(df.columns)}
       return df, meta
   ```
3. **Add a route branch** — add `elif ext == "mytype":` in `upload_file` in `app.py`.
4. **Handle peek rows** — if the format supports a header-row picker, add a branch to `peek_rows()` in `ingestion.py`.

### 9.3 Adding a new column type

1. **Add to the enum** in `core/template_schema.json` under `properties.columns.items.properties.type.enum`.
2. **Add a parser** in `core/validation.py` (follow the `_parse_*` pattern).
3. **Add the branch** in `validate_cell()`.
4. **Add to the type select** in `schema.html` — the `{% for t in [...] %}` loop that renders the type dropdown.
5. **Add an options div** — add a `<div data-opt="mytype">` block in the options cell section of `schema.html`.
6. **Update `updateOptions()`** — the JS function automatically shows/hides `[data-opt]` divs, so no JS changes are needed unless the new type has special UI behaviour.
7. **Update `type_inference.py`** if the new type can be auto-detected from data.

---

## 10. Error handling patterns

### Flask routes
Every route that loads dataset files wraps `storage.load_*()` calls in a `try/except FileNotFoundError` and calls `abort(404)`. Any other exception during ingestion or validation is caught, logged with `logger.exception()`, and surfaced to the user via `flash()`.

### Validation engine
Exceptions inside the per-cell loop are caught and recorded as `PARSE_ERROR` cell issues so a single bad cell does not abort the entire validation run.

### Ingestion
CSV encoding failures are tried in sequence; only if all encodings fail is a `ValueError` raised. `peek_rows()` returns `[]` on any failure rather than raising, so the header-picker UI degrades gracefully.

### Type inference
Errors in individual columns are caught in `infer_all()` and logged; the column falls back to `"string"`. An exception in `infer_all()` itself is caught in the `schema_select` route and logged; the template renders with an empty `inferred_types` dict.

### Template loading
Templates that fail JSON Schema validation are logged at WARNING and skipped. A corrupt template file does not prevent the rest from loading.

---

## 11. Known limitations and technical debt

| Area | Issue |
|------|-------|
| **No cleanup** | Uploaded files and derived artefacts accumulate indefinitely under `data/`. There is no TTL or cleanup job. |
| **Synchronous validation** | Large files (>100k rows, many columns) block the request thread for the duration of validation. There is no progress indicator or async execution. |
| **Single error per cell in UI** | `stats.build_invalid_index()` stores only the first error per (row, column) pair for the results table tooltip. A cell with multiple rule violations shows only one. The full list is in `issues.json` and the Excel export. |
| **unique_together first row** | When a `unique_together` violation is found, only the duplicate row is flagged — not the original first occurrence. |
| **No schema null-token override** | The global `NULL_VALUES` list in `config.py` is always applied. A schema cannot opt specific tokens out, only add more. |
| **XML security** | `xml.etree.ElementTree` is not safe against maliciously crafted XML (XXE / billion-laughs). Do not use with untrusted XML files. |
| **int("1.0") rejected** | The integer parser uses `int()` directly, so `"1.0"` fails the integer type check even though it is a whole number. This is consistent between inference and validation but may surprise users. |
| **No tests** | The `tests/` directory exists but is not populated. Adding pytest tests for `validation.py` and `ingestion.py` is the highest-value testing investment. |