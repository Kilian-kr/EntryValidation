"""
In-memory template repository.

Templates are stored as individual .json files under TEMPLATES_DEFINITIONS_DIR.
They are loaded into the module-level ``_templates`` dict on first access and
can be reloaded at any time via ``reload_templates()``.
"""
import copy
import os
import re
import json
import logging
from config import TEMPLATES_DEFINITIONS_DIR
from core.template_validator import validate_template_json

logger = logging.getLogger(__name__)

# Module-level cache: {template_id: template_dict}
_templates: dict[str, dict] = {}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_filename(template_id: str) -> str:
    """
    Derive a safe filename from a template id.

    Strips any path separators and characters that could cause directory
    traversal or filesystem issues.  Only alphanumerics, hyphens, underscores,
    and dots are kept.
    """
    # Remove path separators and null bytes first
    name = template_id.replace("/", "").replace("\\", "").replace("\0", "")
    # Keep only safe characters
    name = re.sub(r"[^\w.\-]", "_", name)
    # Prevent names that start with a dot (hidden files / relative paths)
    name = name.lstrip(".")
    if not name:
        name = "template"
    return f"{name}.json"


# ── Public API ────────────────────────────────────────────────────────────────

def reload_templates() -> dict[str, dict]:
    """
    Scan TEMPLATES_DEFINITIONS_DIR, validate each .json file, and rebuild the
    in-memory cache.  Invalid or unreadable files are logged and skipped.
    """
    global _templates
    loaded: dict[str, dict] = {}
    os.makedirs(TEMPLATES_DEFINITIONS_DIR, exist_ok=True)

    for fname in os.listdir(TEMPLATES_DEFINITIONS_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(TEMPLATES_DEFINITIONS_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            errors = validate_template_json(data)
            if errors:
                # Log but skip invalid templates so they don't pollute the cache
                logger.warning("Template %s failed validation: %s", fname, errors)
                continue
            loaded[data["id"]] = data
        except Exception as exc:
            logger.error("Could not load template %s: %s", fname, exc)

    _templates = loaded
    logger.info("Loaded %d template(s)", len(_templates))
    return _templates


def get_all_templates() -> dict[str, dict]:
    """Return all valid templates, loading from disk on first call."""
    if not _templates:
        reload_templates()
    return _templates


def get_template(template_id: str) -> dict | None:
    """Return a single template by id, or None if not found."""
    return get_all_templates().get(template_id)


def save_template(data: dict) -> tuple[bool, list[str]]:
    """
    Validate *data* and write it to disk as <id>.json.

    Returns ``(True, [])`` on success or ``(False, [error, ...])`` on failure.
    """
    errors = validate_template_json(data)
    if errors:
        return False, errors

    os.makedirs(TEMPLATES_DEFINITIONS_DIR, exist_ok=True)

    # Use a sanitised filename derived from the template id to prevent path
    # traversal attacks (e.g. an id containing "../" or absolute paths).
    fname = _safe_filename(data["id"])
    fpath = os.path.join(TEMPLATES_DEFINITIONS_DIR, fname)

    with open(fpath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    reload_templates()
    return True, []


def delete_template(template_id: str) -> bool:
    """Remove a template from disk and reload the cache. Returns True if deleted."""
    fname = _safe_filename(template_id)
    fpath = os.path.join(TEMPLATES_DEFINITIONS_DIR, fname)
    if os.path.exists(fpath):
        os.remove(fpath)
        reload_templates()
        return True
    return False


def duplicate_template(template_id: str) -> dict | None:
    """
    Create a copy of an existing template with a unique id.

    The copy id is ``<original_id>-copy`` or ``<original_id>-copy-2``,
    ``-copy-3``, etc. if earlier copies already exist.
    """
    tmpl = get_template(template_id)
    if not tmpl:
        return None

    # Find a copy id that doesn't already exist on disk
    base_id = f"{template_id}-copy"
    new_id  = base_id
    counter = 2
    existing = get_all_templates()
    while new_id in existing:
        new_id = f"{base_id}-{counter}"
        counter += 1

    new        = copy.deepcopy(tmpl)
    new["id"]   = new_id
    new["name"] = f"{tmpl['name']} (Copy)"
    save_template(new)
    return new
