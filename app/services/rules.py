"""
Load the extraction rules (which fields to scrape and how) from the
`scraping_field_config` MySQL table, falling back to the code defaults
in app.services.parser.DEFAULT_RULES when the DB/table is not available.

Table shape (one row per rule) — see sql/schema.sql:

  field_name  name of the output field (e.g. "title")
  kind        'field' | 'discard_phrase' | 'block_phrase' | 'block_class'
  selectors   JSON array of CSS selectors, tried in order
  pattern     phrase (or regex when is_regex=1) for phrase/class rules
  is_regex    whether `pattern` is a regex
  attribute   take this HTML attribute instead of the text (e.g. "src")
  regex       post-extraction regex applied to the matched text
  regex_on_html  regex applied directly to the raw HTML
  exclude_class_contains    skip elements whose class contains this
  exclude_ancestor_class    skip elements inside such an ancestor
  default_value             null marker when nothing is found
  required                  page fails if this field is missing
  priority                  ordering
  enabled                   only enabled rows are used
"""
import json

from app.database import db_manager
from app.services.parser import DEFAULT_RULES
from app.utils.logger import logger


def rows_to_rules(rows: list) -> dict:
    """
    Convert DB rows into the rules dict consumed by the parser.
    Rows with kind='field' become field rules; phrase rows fill the
    discard/block lists; 'block_class' rows fill block_classes.
    """
    rules = {
        "fields": [],
        "discard": [],
        "block": [],
        "block_classes": [],
    }
    for row in rows:
        kind = row.get("kind") or "field"
        if kind == "field":
            selectors = row.get("selectors") or []
            if isinstance(selectors, str):
                try:
                    selectors = json.loads(selectors)
                except (TypeError, ValueError):
                    selectors = [selectors]
            rule = {
                "field_name": row.get("field_name"),
                "selectors": selectors,
                "attribute": row.get("attribute"),
                "regex": row.get("regex"),
                "regex_on_html": row.get("regex_on_html"),
                "exclude_class_contains": row.get("exclude_class_contains"),
                "exclude_ancestor_class": row.get("exclude_ancestor_class"),
                "default_value": row.get("default_value") or "",
                "required": bool(row.get("required")),
            }
            rules["fields"].append(rule)
        elif kind == "discard_phrase":
            rules["discard"].append(row.get("pattern") or "")
        elif kind == "block_phrase":
            rules["block"].append(row.get("pattern") or "")
        elif kind == "block_class":
            rules["block_classes"].append(row.get("pattern") or "")
    return rules


def load_rules() -> dict:
    """
    Load rules from the DB config table; fall back to DEFAULT_RULES when
    the DB is not configured, the table is missing, or it has no fields.
    """
    if not db_manager.is_db_configured():
        logger.info("No DB configured - using built-in default field rules")
        return DEFAULT_RULES

    # DB configured: the config table is the single source of truth.
    # No silent fallback to code defaults.
    rows = db_manager.load_field_config()
    if not rows:
        raise RuntimeError(
            f"Field-config table '{db_manager.cfg.SCRAPE_FIELDS_TABLE}' is missing or empty. "
            "Create and seed it with sql/field_config.sql."
        )
    rules = rows_to_rules(rows)
    if not rules["fields"]:
        raise RuntimeError(
            f"Field-config table '{db_manager.cfg.SCRAPE_FIELDS_TABLE}' has no enabled "
            "'field' rows - nothing to extract."
        )
    logger.info("Loaded %d field rule(s) + %d phrase/class rule(s) from DB",
                len(rules["fields"]),
                len(rules["discard"]) + len(rules["block"]) + len(rules["block_classes"]))
    return rules
