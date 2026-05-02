"""
Filesystem storage helpers.

Each dataset gets two directories:
- data/uploads/<dataset_id>/   — the original uploaded file
- data/derived/<dataset_id>/   — derived artefacts:
    meta.json     dataset metadata (filename, dimensions, file type, …)
    raw.parquet   ingested data as an all-string Parquet file
    schema.json   resolved validation schema
    issues.json   validation results

All load_* functions raise FileNotFoundError when the requested file does not
exist, allowing callers to distinguish "not found" from other IO errors.

Note: Parquet serialisation requires either ``pyarrow`` or ``fastparquet`` to
be installed.  Both are listed in requirements.txt.
"""
import os
import json
import logging
import pandas as pd
from config import UPLOADS_DIR, DERIVED_DIR

logger = logging.getLogger(__name__)


# ── Directory helpers ─────────────────────────────────────────────────────────

def dataset_upload_dir(dataset_id: str) -> str:
    """Return (and create if necessary) the upload directory for *dataset_id*."""
    path = os.path.join(UPLOADS_DIR, dataset_id)
    os.makedirs(path, exist_ok=True)
    return path


def dataset_derived_dir(dataset_id: str) -> str:
    """Return (and create if necessary) the derived-artefacts directory."""
    path = os.path.join(DERIVED_DIR, dataset_id)
    os.makedirs(path, exist_ok=True)
    return path


# ── Meta ──────────────────────────────────────────────────────────────────────

def save_meta(dataset_id: str, meta: dict) -> None:
    """Persist dataset metadata as JSON.  Non-serialisable values are coerced to str."""
    path = os.path.join(dataset_derived_dir(dataset_id), "meta.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, default=str)


def load_meta(dataset_id: str) -> dict:
    """Load dataset metadata. Raises FileNotFoundError if meta.json is missing."""
    path = os.path.join(dataset_derived_dir(dataset_id), "meta.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Raw data (Parquet) ────────────────────────────────────────────────────────

def save_raw(dataset_id: str, df: pd.DataFrame) -> None:
    """Persist the ingested DataFrame as a Parquet file (all-string columns)."""
    path = os.path.join(dataset_derived_dir(dataset_id), "raw.parquet")
    df.to_parquet(path, index=False)


def load_raw(dataset_id: str) -> pd.DataFrame:
    """Load the raw DataFrame. Raises FileNotFoundError if raw.parquet is missing."""
    path = os.path.join(dataset_derived_dir(dataset_id), "raw.parquet")
    return pd.read_parquet(path)


# ── Schema ────────────────────────────────────────────────────────────────────

def save_schema(dataset_id: str, schema: dict) -> None:
    """Persist the resolved validation schema as JSON."""
    path = os.path.join(dataset_derived_dir(dataset_id), "schema.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)


def load_schema(dataset_id: str) -> dict:
    """Load the schema. Raises FileNotFoundError if schema.json is missing."""
    path = os.path.join(dataset_derived_dir(dataset_id), "schema.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def schema_exists(dataset_id: str) -> bool:
    """Return True if a schema has been saved for *dataset_id*."""
    path = os.path.join(dataset_derived_dir(dataset_id), "schema.json")
    return os.path.exists(path)


# ── Issues ────────────────────────────────────────────────────────────────────

def save_issues(dataset_id: str, issues: dict) -> None:
    """Persist validation results as JSON."""
    path = os.path.join(dataset_derived_dir(dataset_id), "issues.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(issues, f, indent=2, default=str)


def load_issues(dataset_id: str) -> dict:
    """Load validation results. Raises FileNotFoundError if issues.json is missing."""
    path = os.path.join(dataset_derived_dir(dataset_id), "issues.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def issues_exist(dataset_id: str) -> bool:
    """Return True if validation has already been run for *dataset_id*."""
    path = os.path.join(dataset_derived_dir(dataset_id), "issues.json")
    return os.path.exists(path)
