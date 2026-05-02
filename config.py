"""
Application-wide configuration.

All tuneable values are read from environment variables so the app can be
configured without touching source code.  Sensible defaults are provided for
local development; see the README for production guidance.
"""
import os

# ── Paths ─────────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Uploaded source files are stored under data/uploads/<dataset_id>/
DATA_DIR    = os.path.join(BASE_DIR, "data")
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")

# Derived artefacts (raw.parquet, schema.json, issues.json, meta.json)
# are stored under data/derived/<dataset_id>/
DERIVED_DIR = os.path.join(DATA_DIR, "derived")

# Re-usable validation templates live here as individual .json files
TEMPLATES_DEFINITIONS_DIR = os.path.join(BASE_DIR, "templates_definitions")

# ── Upload limits ─────────────────────────────────────────────────────────────

# Maximum allowed upload size in bytes (default 100 MB)
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", 100 * 1024 * 1024))

# File extensions accepted by the upload endpoint
ALLOWED_EXTENSIONS = {"csv", "xlsx", "xls", "xml", "json"}

# ── Null / empty-value tokens ─────────────────────────────────────────────────
# Cells whose stripped value matches any of these tokens are treated as empty.
# Applied globally; individual schemas may add extra tokens via null_values.

NULL_VALUES = ["", " ", "NA", "N/A", "NULL", "null", "n/a", "na", "-", "--"]

# ── Security ──────────────────────────────────────────────────────────────────
# IMPORTANT: change SECRET_KEY in any non-development environment.

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-in-prod")
