"""
DataValidator — Flask application entry point.

URL map
-------
GET  /                              → upload form
GET  /upload                        → upload form
POST /upload                        → handle file upload, redirect to schema

GET  /select-sheet/<id>             → sheet-selection UI (multi-sheet XLSX)
POST /select-sheet/<id>             → apply sheet selection

POST /dataset/<id>/set-header       → re-ingest with a different header row

GET  /schema/<id>                   → schema configuration page
POST /schema/<id>                   → submit schema, redirect to validation

POST /validate/<id>                 → run validation, redirect to results
GET  /results/<id>                  → results page (infinite-scroll table)
GET  /results/<id>/rows             → JSON endpoint for the infinite-scroll chunks

GET  /download/<id>/issues.csv      → download issues as CSV
GET  /download/<id>/wrong_rows.csv  → download only invalid rows as CSV
GET  /download/<id>/validation.xlsx → download annotated Excel workbook

POST /dataset/<id>/revalidate       → re-run with the stored schema
POST /dataset/<id>/save-as-template → save current schema as a reusable template

GET  /templates                     → template list
POST /templates/reload              → reload templates from disk
GET  /templates/new                 → new-template form
POST /templates/new                 → save new template
GET  /templates/import              → import-template form
POST /templates/import              → save imported template
GET  /templates/<tid>/export        → download template as JSON
POST /templates/<tid>/duplicate     → duplicate a template
POST /templates/<tid>/delete        → delete a template
GET  /templates/<tid>/edit          → edit-template form
"""
import os
import uuid
import json
import logging
import io

from flask import (
    Flask, request, redirect, url_for, render_template,
    flash, send_file, abort, jsonify,
)
from werkzeug.utils import secure_filename

import config
from core import ingestion, storage, templates_repo, schema_match, validation, stats, export
from core import type_inference

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
app.secret_key = config.SECRET_KEY

# Ensure required directories exist at startup
os.makedirs(config.UPLOADS_DIR, exist_ok=True)
os.makedirs(config.DERIVED_DIR, exist_ok=True)
os.makedirs(config.TEMPLATES_DEFINITIONS_DIR, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _allowed_file(filename: str) -> bool:
    """Return True if *filename* has an extension in ALLOWED_EXTENSIONS."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in config.ALLOWED_EXTENSIONS


# ── Home ──────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("upload.html")


# ── Upload ────────────────────────────────────────────────────────────────────

@app.route("/upload", methods=["GET"])
def upload_form():
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload_file():
    """
    Accept a file upload, ingest it into a string DataFrame, and redirect to
    the schema configuration page.

    Multi-sheet XLSX files redirect to an intermediate sheet-selection page
    before continuing.
    """
    if "file" not in request.files:
        flash("No file part in the request.", "danger")
        return redirect(url_for("upload_form"))

    f = request.files["file"]
    if not f.filename:
        flash("No file selected.", "danger")
        return redirect(url_for("upload_form"))

    if not _allowed_file(f.filename):
        flash(
            f"File type not allowed. Accepted: {', '.join(config.ALLOWED_EXTENSIONS)}",
            "danger",
        )
        return redirect(url_for("upload_form"))

    # Assign a UUID so every upload is isolated and can be referenced later
    dataset_id = str(uuid.uuid4())
    safe_name  = secure_filename(f.filename)
    upload_dir = storage.dataset_upload_dir(dataset_id)
    filepath   = os.path.join(upload_dir, safe_name)
    f.save(filepath)

    ext = safe_name.rsplit(".", 1)[1].lower()
    logger.info("[%s] Uploaded %s (%s)", dataset_id, safe_name, ext)

    try:
        if ext == "csv":
            encoding     = request.form.get("encoding") or None
            df, file_meta = ingestion.ingest_csv(filepath, encoding=encoding)
            meta = {"file_type": "csv", **file_meta}

        elif ext in ("xlsx", "xls"):
            sheet_param  = request.form.get("sheet")
            sheet        = int(sheet_param) if sheet_param and sheet_param.isdigit() else 0
            df, file_meta = ingestion.ingest_xlsx(filepath, sheet_name=sheet)
            meta = {"file_type": "xlsx", **file_meta}

            # When the workbook has multiple sheets and no sheet was selected,
            # save minimal meta (without raw data) and redirect to the picker.
            if len(file_meta.get("sheet_names", [])) > 1 and not sheet_param:
                storage.save_meta(dataset_id, {
                    "file_type":             "xlsx",
                    "filename":              safe_name,
                    "filepath":              filepath,
                    "sheet_names":           file_meta["sheet_names"],
                    "needs_sheet_selection": True,
                })
                return redirect(url_for("select_sheet", dataset_id=dataset_id))

        elif ext == "xml":
            record_path  = request.form.get("record_path") or None
            df, file_meta = ingestion.ingest_xml(filepath, record_path=record_path)
            meta = {"file_type": "xml", **file_meta}

        elif ext == "json":
            df, file_meta = ingestion.ingest_json(filepath)
            meta = {"file_type": "json", **file_meta}

        else:
            flash("Unsupported file type.", "danger")
            return redirect(url_for("upload_form"))

        meta["filename"]   = safe_name
        meta["filepath"]   = filepath
        meta["dataset_id"] = dataset_id
        meta.setdefault("header_row", 0)

        storage.save_raw(dataset_id, df)
        storage.save_meta(dataset_id, meta)
        logger.info("[%s] Ingested %d rows × %d cols", dataset_id, meta["rows"], meta["cols"])

    except Exception as exc:
        logger.exception("[%s] Ingestion failed", dataset_id)
        flash(f"Failed to parse file: {exc}", "danger")
        return redirect(url_for("upload_form"))

    return redirect(url_for("schema_select", dataset_id=dataset_id))


# ── Sheet selection (multi-sheet XLSX) ───────────────────────────────────────

@app.route("/select-sheet/<dataset_id>")
def select_sheet(dataset_id: str):
    try:
        meta = storage.load_meta(dataset_id)
    except FileNotFoundError:
        abort(404)
    return render_template("select_sheet.html", dataset_id=dataset_id, meta=meta)


@app.route("/select-sheet/<dataset_id>", methods=["POST"])
def select_sheet_post(dataset_id: str):
    """Re-ingest the workbook using the user-selected sheet."""
    try:
        meta = storage.load_meta(dataset_id)
    except FileNotFoundError:
        abort(404)

    sheet    = int(request.form.get("sheet", 0))
    filepath = meta["filepath"]

    try:
        df, file_meta = ingestion.ingest_xlsx(filepath, sheet_name=sheet)
    except Exception as exc:
        flash(f"Could not load sheet {sheet}: {exc}", "danger")
        return redirect(url_for("select_sheet", dataset_id=dataset_id))

    meta.update(file_meta)
    meta["needs_sheet_selection"] = False
    storage.save_raw(dataset_id, df)
    storage.save_meta(dataset_id, meta)
    return redirect(url_for("schema_select", dataset_id=dataset_id))


# ── Header-row picker ─────────────────────────────────────────────────────────

@app.route("/dataset/<dataset_id>/set-header", methods=["POST"])
def set_header(dataset_id: str):
    """Re-ingest the file treating a different row as the column header."""
    try:
        meta = storage.load_meta(dataset_id)
    except FileNotFoundError:
        abort(404)

    header_row = int(request.form.get("header_row", 0))
    filepath   = meta["filepath"]
    file_type  = meta["file_type"]
    encoding   = meta.get("encoding_used")
    sheet_name = meta.get("sheet_used", 0)

    # Resolve sheet name to an index if it was stored as a string
    if isinstance(sheet_name, str) and file_type in ("xlsx", "xls"):
        try:
            import pandas as pd
            sheet_name = pd.ExcelFile(filepath).sheet_names.index(sheet_name)
        except Exception:
            logger.warning(
                "[%s] Could not resolve sheet name '%s' to index, defaulting to 0",
                dataset_id, sheet_name,
            )
            sheet_name = 0

    try:
        if file_type == "csv":
            df, file_meta = ingestion.ingest_csv(
                filepath, encoding=encoding, header_row=header_row)
        elif file_type in ("xlsx", "xls"):
            df, file_meta = ingestion.ingest_xlsx(
                filepath, sheet_name=sheet_name, header_row=header_row)
        else:
            flash("Header row selection is not supported for this file type.", "warning")
            return redirect(url_for("schema_select", dataset_id=dataset_id))

        meta.update(file_meta)
        meta["header_row"] = header_row
        storage.save_raw(dataset_id, df)
        storage.save_meta(dataset_id, meta)
        logger.info(
            "[%s] Header row set to %d → %d rows × %d cols",
            dataset_id, header_row, meta["rows"], meta["cols"],
        )
    except Exception as exc:
        logger.exception(
            "[%s] Failed to re-ingest with header_row=%d", dataset_id, header_row)
        flash(f"Failed to apply header row: {exc}", "danger")

    return redirect(url_for("schema_select", dataset_id=dataset_id))


# ── Schema configuration ──────────────────────────────────────────────────────

@app.route("/schema/<dataset_id>", methods=["GET"])
def schema_select(dataset_id: str):
    """
    Render the schema configuration page.

    When no schema has been saved yet, column types are inferred from the data
    and passed to the template as ``inferred_types`` so the dropdowns are
    pre-populated with sensible defaults.
    """
    try:
        meta = storage.load_meta(dataset_id)
    except FileNotFoundError:
        abort(404)

    df        = storage.load_raw(dataset_id)
    templates = templates_repo.get_all_templates()

    # Raw peek rows for the header-row picker (no header promotion)
    peek: list = []
    if meta["file_type"] != "xml":
        sheet_name = meta.get("sheet_used", 0)
        if isinstance(sheet_name, str) and meta["file_type"] in ("xlsx", "xls"):
            try:
                import pandas as pd
                sheet_name = pd.ExcelFile(meta["filepath"]).sheet_names.index(sheet_name)
            except Exception:
                sheet_name = 0
        peek = ingestion.peek_rows(
            meta["filepath"], meta["file_type"],
            encoding=meta.get("encoding_used"),
            sheet_name=sheet_name,
            n=12,
        )

    # Try to load an existing schema for pre-filling the form
    existing_schema = None
    try:
        existing_schema = storage.load_schema(dataset_id)
    except FileNotFoundError:
        pass

    # Infer column types from the data when no schema has been saved yet.
    # This pre-populates the type dropdowns without committing to any schema.
    inferred_types: dict = {}
    if not existing_schema:
        try:
            inferred_types = type_inference.infer_all(df)
        except Exception:
            logger.exception(
                "[%s] Type inference failed — defaulting all columns to string",
                dataset_id,
            )

    return render_template(
        "schema.html",
        dataset_id=dataset_id,
        meta=meta,
        columns=list(df.columns),
        templates=templates,
        preview=df.head(10).to_dict(orient="records"),
        peek_rows=peek,
        current_header_row=meta.get("header_row", 0),
        existing_schema=existing_schema,
        inferred_types=inferred_types,
    )


@app.route("/schema/<dataset_id>", methods=["POST"])
def schema_submit(dataset_id: str):
    """
    Build and persist the schema from the form submission, then redirect to
    validation.

    Two modes:
    - manual   : the user chose a type for each column individually.
    - template : the user selected a saved template; column mappings may need
                 manual resolution if auto-matching was incomplete.
    """
    try:
        meta = storage.load_meta(dataset_id)
        df   = storage.load_raw(dataset_id)
    except FileNotFoundError:
        abort(404)

    mode         = request.form.get("mode", "manual")
    file_columns = list(df.columns)

    if mode == "manual":
        type_map  = {col: request.form.get(f"type_{col}",  "string")  for col in file_columns}
        empty_map = {col: request.form.get(f"empty_{col}", "allowed") for col in file_columns}
        schema    = schema_match.build_manual_schema(dataset_id, file_columns, type_map, empty_map)
        schema["dataset_id"] = dataset_id

        # Inject per-column rules collected from the UI form fields
        for col in file_columns:
            col_type = type_map.get(col, "string")

            # Date / datetime: comma-separated strptime format strings
            if col_type in ("date", "datetime"):
                raw  = request.form.get(f"formats_{col}", "").strip()
                fmts = [f.strip() for f in raw.split(",") if f.strip()]
                if fmts:
                    schema["columns"][col]["rules"]["formats"] = fmts

            # Enum: comma-separated list of allowed values
            elif col_type == "enum":
                raw     = request.form.get(f"allowed_{col}", "").strip()
                allowed = [v.strip() for v in raw.split(",") if v.strip()]
                if allowed:
                    schema["columns"][col]["rules"]["allowed"] = allowed

            # Integer / number: optional min / max bounds
            elif col_type in ("integer", "number"):
                raw_min = request.form.get(f"min_{col}", "").strip()
                raw_max = request.form.get(f"max_{col}", "").strip()
                if raw_min:
                    try:
                        schema["columns"][col]["rules"]["min"] = float(raw_min)
                    except ValueError:
                        pass
                if raw_max:
                    try:
                        schema["columns"][col]["rules"]["max"] = float(raw_max)
                    except ValueError:
                        pass

            # String: optional regex pattern
            elif col_type == "string":
                regex = request.form.get(f"regex_{col}", "").strip()
                if regex:
                    schema["columns"][col]["rules"]["regex"] = regex

        storage.save_schema(dataset_id, schema)
        return redirect(url_for("validate_dataset", dataset_id=dataset_id))

    elif mode == "template":
        template_id = request.form.get("template_id")
        template    = templates_repo.get_template(template_id)
        if not template:
            flash("Template not found.", "danger")
            return redirect(url_for("schema_select", dataset_id=dataset_id))

        match_result   = schema_match.match_template_to_columns(template, file_columns)
        column_mapping = dict(match_result["matched"])

        # Apply any manual mappings the user provided for unresolved columns
        for tc in match_result.get("unresolved", []):
            user_val = request.form.get(f"map_{tc}")
            if user_val and user_val != "__skip__":
                column_mapping[tc] = user_val

        if match_result["unresolved"]:
            still_unresolved = [
                tc for tc in match_result["unresolved"]
                if tc not in column_mapping
            ]
            # Only block submission when a *required* column is still unresolved
            resolved_required = [
                tc for tc in still_unresolved
                if any(
                    cd["name"] == tc and cd.get("required")
                    for cd in template.get("columns", [])
                )
            ]
            if resolved_required and not request.form.get("force_validate"):
                # Re-render the schema page with the mapping UI visible.
                # peek_rows must be passed here too so the header-picker renders.
                peek: list = []
                if meta["file_type"] != "xml":
                    peek = ingestion.peek_rows(
                        meta["filepath"], meta["file_type"],
                        encoding=meta.get("encoding_used"),
                        sheet_name=meta.get("sheet_used", 0),
                        n=12,
                    )
                return render_template(
                    "schema.html",
                    dataset_id=dataset_id,
                    meta=meta,
                    columns=file_columns,
                    templates=templates_repo.get_all_templates(),
                    preview=df.head(10).to_dict(orient="records"),
                    peek_rows=peek,
                    current_header_row=meta.get("header_row", 0),
                    selected_template=template,
                    match_result=match_result,
                    show_mapping=True,
                    existing_schema=None,
                    inferred_types={},
                )

        schema = schema_match.build_resolved_schema(template, column_mapping, file_columns)
        schema["dataset_id"] = dataset_id
        storage.save_schema(dataset_id, schema)
        return redirect(url_for("validate_dataset", dataset_id=dataset_id))

    flash("Unknown schema mode.", "danger")
    return redirect(url_for("schema_select", dataset_id=dataset_id))


# ── Validation ────────────────────────────────────────────────────────────────

@app.route("/validate/<dataset_id>", methods=["POST", "GET"])
def validate_dataset(dataset_id: str):
    """Run validation and redirect to the results page."""
    try:
        df     = storage.load_raw(dataset_id)
        schema = storage.load_schema(dataset_id)
    except FileNotFoundError:
        abort(404)

    logger.info(
        "[%s] Running validation schema=%s rows=%d",
        dataset_id, schema.get("id"), len(df),
    )
    try:
        issues = validation.run_validation(df, schema)
    except Exception as exc:
        # A crash inside run_validation is surfaced as a dataset-level issue
        # so the user still lands on the results page with an explanation.
        logger.exception("[%s] Validation crashed", dataset_id)
        issues = {
            "dataset_id":     dataset_id,
            "schema_id":      schema.get("id", ""),
            "dataset_issues": [{"code": "PARSE_ERROR", "column": None, "message": str(exc)}],
            "cell_issues":    [],
            "column_summary": {},
            "stats": {
                "rows": len(df), "columns": len(df.columns),
                "total_cells": 0, "invalid_cells": 0,
                "invalid_rows": 0, "invalid_columns": 0,
                "worst_column": None, "top_issue_codes": [],
            },
        }

    storage.save_issues(dataset_id, issues)
    logger.info(
        "[%s] Validation done: %d cell issues, %d dataset issues",
        dataset_id,
        len(issues["cell_issues"]),
        len(issues["dataset_issues"]),
    )
    return redirect(url_for("results", dataset_id=dataset_id))


# ── Results ───────────────────────────────────────────────────────────────────

def _build_row_indices(df, issues, mode, cols_filter, error_code_filter):
    """
    Shared logic used by both the results page and the JSON rows endpoint.

    Returns (row_indices, display_cols, invalid_by_row, invalid_count_by_col).
    """
    invalid_by_row, invalid_count_by_col = stats.build_invalid_index(issues)
    invalid_row_set = stats.get_invalid_row_set(issues)

    all_rows = list(range(len(df)))

    # Filter to the requested row subset
    if mode == "wrong_rows":
        row_indices = [i for i in all_rows if i in invalid_row_set]
    elif mode == "correct_rows":
        row_indices = [i for i in all_rows if i not in invalid_row_set]
    else:
        row_indices = all_rows

    # Further filter by a specific error code if requested
    if error_code_filter:
        filtered_rows_for_code = {
            ci["row"] for ci in issues.get("cell_issues", [])
            if ci["code"] == error_code_filter
        }
        row_indices = [i for i in row_indices if i in filtered_rows_for_code]

    # Column subset
    all_columns = list(df.columns)
    if cols_filter == "wrong_only":
        display_cols = [c for c in all_columns if invalid_count_by_col.get(c, 0) > 0]
        if not display_cols:
            display_cols = all_columns   # fall back to all if none are invalid
    else:
        display_cols = all_columns

    return row_indices, display_cols, invalid_by_row, invalid_count_by_col


@app.route("/results/<dataset_id>")
def results(dataset_id: str):
    """Render the results page (table is populated via the /rows JSON endpoint)."""
    try:
        meta   = storage.load_meta(dataset_id)
        df     = storage.load_raw(dataset_id)
        issues = storage.load_issues(dataset_id)
    except FileNotFoundError:
        abort(404)

    mode              = request.args.get("mode",       "all")
    cols_filter       = request.args.get("cols",       "all")
    error_code_filter = request.args.get("error_code", "")

    row_indices, display_cols, _, invalid_count_by_col = _build_row_indices(
        df, issues, mode, cols_filter, error_code_filter
    )
    all_codes = sorted({ci["code"] for ci in issues.get("cell_issues", [])})

    return render_template(
        "results.html",
        dataset_id=dataset_id,
        meta=meta,
        issues=issues,
        display_cols=display_cols,
        all_columns=list(df.columns),
        invalid_count_by_col=invalid_count_by_col,
        total_display_rows=len(row_indices),
        mode=mode,
        cols_filter=cols_filter,
        error_code_filter=error_code_filter,
        all_codes=all_codes,
    )


@app.route("/results/<dataset_id>/rows")
def results_rows(dataset_id: str):
    """
    JSON endpoint for the infinite-scroll table.

    Returns a page of rendered row data::

        {
            "rows":   [{idx, cells: [{v, i, c, m}, ...]}, ...],
            "offset": int,
            "limit":  int,
            "total":  int,
        }

    ``idx`` is a 1-based display counter (not the original DataFrame row index).
    ``i`` = invalid flag, ``c`` = error code, ``m`` = error message.
    """
    try:
        df     = storage.load_raw(dataset_id)
        issues = storage.load_issues(dataset_id)
    except FileNotFoundError:
        abort(404)

    mode              = request.args.get("mode",       "all")
    cols_filter       = request.args.get("cols",       "all")
    error_code_filter = request.args.get("error_code", "")
    offset            = max(0,   int(request.args.get("offset", 0)))
    limit             = min(200, max(1, int(request.args.get("limit", 100))))

    row_indices, display_cols, invalid_by_row, _ = _build_row_indices(
        df, issues, mode, cols_filter, error_code_filter
    )

    chunk    = row_indices[offset: offset + limit]
    chunk_df = df.iloc[chunk][display_cols] if chunk else df.iloc[[]][display_cols]

    rows_out = []
    for local_i, global_i in enumerate(chunk):
        row_invalid = invalid_by_row.get(global_i, {})
        cells = []
        for col in display_cols:
            val = chunk_df.iloc[local_i][col]
            err = row_invalid.get(col)
            cells.append({
                "v": str(val),
                "i": bool(err),
                "c": err[0] if err else None,
                "m": err[1] if err else None,
            })
        rows_out.append({"idx": offset + local_i + 1, "cells": cells})

    return jsonify({
        "rows":    rows_out,
        "offset":  offset,
        "limit":   limit,
        "total":   len(row_indices),
        "columns": display_cols,
    })


# ── Downloads ─────────────────────────────────────────────────────────────────

@app.route("/download/<dataset_id>/issues.csv")
def download_issues(dataset_id: str):
    """Download all cell issues as a flat CSV."""
    try:
        issues = storage.load_issues(dataset_id)
    except FileNotFoundError:
        abort(404)
    csv_bytes = export.issues_to_csv(issues)
    return send_file(
        io.BytesIO(csv_bytes),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"issues_{dataset_id[:8]}.csv",
    )


@app.route("/download/<dataset_id>/wrong_rows.csv")
def download_wrong_rows(dataset_id: str):
    """Download only the rows that have at least one invalid cell."""
    try:
        df     = storage.load_raw(dataset_id)
        issues = storage.load_issues(dataset_id)
    except FileNotFoundError:
        abort(404)
    csv_bytes = export.wrong_rows_to_csv(df, issues)
    return send_file(
        io.BytesIO(csv_bytes),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"wrong_rows_{dataset_id[:8]}.csv",
    )


@app.route("/download/<dataset_id>/validation.xlsx")
def download_issues_xlsx(dataset_id: str):
    """Download a two-sheet Excel workbook (annotated data + issues list)."""
    try:
        df     = storage.load_raw(dataset_id)
        issues = storage.load_issues(dataset_id)
    except FileNotFoundError:
        abort(404)
    xlsx_bytes = export.issues_to_xlsx(df, issues)
    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"validation_{dataset_id[:8]}.xlsx",
    )


# ── Re-validate ───────────────────────────────────────────────────────────────

@app.route("/dataset/<dataset_id>/revalidate", methods=["POST"])
def revalidate(dataset_id: str):
    """Re-run validation with the currently stored schema (no re-upload needed)."""
    if not storage.schema_exists(dataset_id):
        flash("No schema found. Please configure a schema first.", "warning")
        return redirect(url_for("schema_select", dataset_id=dataset_id))
    return redirect(url_for("validate_dataset", dataset_id=dataset_id))


# ── Save as template ──────────────────────────────────────────────────────────

@app.route("/dataset/<dataset_id>/save-as-template", methods=["POST"])
def save_as_template(dataset_id: str):
    """Convert the current stored schema into a reusable named template."""
    try:
        schema = storage.load_schema(dataset_id)
    except FileNotFoundError:
        abort(404)

    name = request.form.get("template_name", "").strip()
    if not name:
        flash("Please provide a template name.", "danger")
        return redirect(url_for("results", dataset_id=dataset_id))

    import time
    tmpl_id = f"saved-{int(time.time())}"

    # Convert the schema columns dict back to the list format expected by templates
    columns_list = []
    for col_name, col_def in schema.get("columns", {}).items():
        if col_def.get("unvalidated"):
            continue   # skip columns that were not validated
        columns_list.append({
            "name":     col_def.get("template_column") or col_name,
            "type":     col_def.get("type", "string"),
            "required": col_def.get("required", False),
            "nullable": col_def.get("nullable", True),
            "rules":    col_def.get("rules") or {},
        })

    template = {
        "id":          tmpl_id,
        "name":        name,
        "strict":      schema.get("strict", False),
        "null_values": schema.get("null_values", []),
        "columns":     columns_list,
    }

    ok, errors = templates_repo.save_template(template)
    if ok:
        flash(f'Saved as template "{name}".', "success")
    else:
        flash("Could not save template: " + "; ".join(errors), "danger")

    return redirect(url_for("results", dataset_id=dataset_id))


# ── Template manager ──────────────────────────────────────────────────────────

@app.route("/templates")
def template_list():
    templates = templates_repo.get_all_templates()
    return render_template("templates_list.html", templates=templates)


@app.route("/templates/reload", methods=["POST"])
def template_reload():
    """Force a reload of all template files from disk."""
    templates_repo.reload_templates()
    flash("Templates reloaded.", "success")
    return redirect(url_for("template_list"))


@app.route("/templates/new", methods=["GET"])
def template_new():
    """Render the new-template form, optionally pre-filling columns from a dataset."""
    dataset_id      = request.args.get("dataset_id")
    prefill_columns = []
    if dataset_id:
        try:
            df              = storage.load_raw(dataset_id)
            prefill_columns = list(df.columns)
        except Exception:
            pass
    return render_template(
        "templates_new.html",
        prefill_columns=prefill_columns,
        dataset_id=dataset_id,
    )


@app.route("/templates/new", methods=["POST"])
def template_new_post():
    """Validate and save a new template from the raw JSON editor."""
    raw_json = request.form.get("template_json", "")
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        flash(f"Invalid JSON: {exc}", "danger")
        return render_template(
            "templates_new.html", prefill_columns=[], raw_json=raw_json)

    ok, errors = templates_repo.save_template(data)
    if not ok:
        flash("Template validation errors: " + "; ".join(errors), "danger")
        return render_template(
            "templates_new.html", prefill_columns=[], raw_json=raw_json)

    flash(f"Template '{data['name']}' saved.", "success")
    return redirect(url_for("template_list"))


@app.route("/templates/import", methods=["GET"])
def template_import():
    return render_template("templates_import.html")


@app.route("/templates/import", methods=["POST"])
def template_import_post():
    """Import a template from an uploaded .json file."""
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Please select a file to upload.", "danger")
        return redirect(url_for("template_import"))
    if not f.filename.endswith(".json"):
        flash("Please upload a .json file.", "danger")
        return redirect(url_for("template_import"))
    try:
        data = json.load(f)
    except json.JSONDecodeError as exc:
        flash(f"Invalid JSON: {exc}", "danger")
        return redirect(url_for("template_import"))

    ok, errors = templates_repo.save_template(data)
    if not ok:
        flash("Template validation errors: " + "; ".join(errors), "danger")
        return redirect(url_for("template_import"))

    flash(f"Template '{data.get('name', data.get('id'))}' imported.", "success")
    return redirect(url_for("template_list"))


@app.route("/templates/<template_id>/export")
def template_export(template_id: str):
    """Download a template as a .json file."""
    tmpl = templates_repo.get_template(template_id)
    if not tmpl:
        abort(404)
    json_bytes = json.dumps(tmpl, indent=2).encode("utf-8")
    return send_file(
        io.BytesIO(json_bytes),
        mimetype="application/json",
        as_attachment=True,
        download_name=f"{template_id}.json",
    )


@app.route("/templates/<template_id>/duplicate", methods=["POST"])
def template_duplicate(template_id: str):
    """Create a copy of an existing template with a unique id."""
    new = templates_repo.duplicate_template(template_id)
    if not new:
        flash("Template not found.", "danger")
    else:
        flash(f"Duplicated as '{new['name']}'.", "success")
    return redirect(url_for("template_list"))


@app.route("/templates/<template_id>/delete", methods=["POST"])
def template_delete(template_id: str):
    """Permanently delete a template file."""
    ok = templates_repo.delete_template(template_id)
    if ok:
        flash("Template deleted.", "success")
    else:
        flash("Template not found.", "danger")
    return redirect(url_for("template_list"))


@app.route("/templates/<template_id>/edit", methods=["GET"])
def template_edit(template_id: str):
    """Open the JSON editor pre-filled with an existing template."""
    tmpl = templates_repo.get_template(template_id)
    if not tmpl:
        abort(404)
    return render_template(
        "templates_new.html",
        prefill_columns=[],
        raw_json=json.dumps(tmpl, indent=2),
        edit_mode=True,
        template_id=template_id,
    )


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
