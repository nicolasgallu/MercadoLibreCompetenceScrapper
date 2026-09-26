"""
Rules-driven HTML parsing / classification for Mercado Libre pages.

Nothing about WHICH fields to extract is hardcoded here: field rules come
from the `scraping_field_config` table (see app/services/rules.py) and can
be added/removed per field, e.g.:

    field_name = "title"
    selectors  = ["h1.ui-pdp-title"]
    required   = 1

A field rule supports:
  - selectors          ordered list of CSS selectors (first non-empty wins)
  - attribute          take an attribute instead of text (e.g. "src")
  - regex              post-extraction regex (group 1 wins, else full match)
  - regex_on_html      regex applied to the RAW html (alternative extraction)
  - exclude_class_contains   skip elements whose class contains this
  - exclude_ancestor_class   skip elements inside such an ancestor
  - default_value      used when nothing is found (also the "null" marker)
  - required           page is FAILED if this field is missing

DEFAULT_RULES below mirror the verified Mercado Libre selectors so the
pipeline works even before the config table exists.
"""
import re

from bs4 import BeautifulSoup

# Upstream HTTP statuses that mean the product/listing no longer exists.
DEAD_STATUS_CODES = {404, 410}

# Statuses that mean we were blocked or the site is having issues.
BLOCKED_STATUS_CODES = {403, 429, 503}

DEFAULT_RULES = {
    "fields": [
        {
            "field_name": "title",
            "selectors": ["h1.ui-pdp-title"],
            "default_value": "n/a",
            "required": True,
        },
        {
            "field_name": "price",
            "selectors": [
                "div.ui-pdp-price__second-line span.andes-money-amount__fraction",
                ".ui-pdp-price__part span.andes-money-amount__fraction",
                "span.andes-money-amount__fraction",
            ],
            "exclude_class_contains": "--previous",
            "exclude_ancestor_class": "poly-card__content",
            "default_value": "",
        },
        {
            "field_name": "competitor",
            "selectors": [
                "h2.ui-seller-data-header__title",
                ".ui-seller-data-header__title",
            ],
            "regex_on_html": r"[Vv]endido por\s*(?:</?[^>]*>)*\s*([^<&]{2,60})",
            "default_value": "n/a",
        },
        {
            "field_name": "price_in_installments",
            "selectors": [
                "div.ui-pdp-price__subtitles",
                ".ui-pdp-products__list",
            ],
            "regex": r"(?i)(?:cuota promocionada en|hasta)?\s*(\d+\s*(?:x|cuotas)[^\n|]{0,60})",
            "default_value": "n/a",
        },
        {
            "field_name": "image",
            "selectors": ["img.ui-pdp-image"],
            "attribute": "src",
            "default_value": "n/a",
        },
    ],
    "discard": [
        "este producto no está disponible",
        "no encontramos la página",
        "error 404",
    ],
    "block": [
        "protegemos a nuestros usuarios",
        "no eres un robot",
        "completa el siguiente captcha",
        "completa la siguiente verificación",
        "actividad inusual",
        "hemos detectado un comportamiento",
        "hemos detectado actividad",
        "verifica que eres humano",
        "to continue, please complete",
        "checking your browser",
        # login wall: "¡Hola! Para continuar, ingresa a<br/>tu cuenta"
        "para continuar, ingresa a",
        "ingresa a tu cuenta",
    ],
    "block_classes": ["message-card"],
}


def looks_blocked(html: str, rules: dict = None) -> bool:
    """True if the HTML looks like a Mercado Libre shield/login-wall page."""
    if not html:
        return False
    rules = rules or DEFAULT_RULES
    lowered = html[:300_000].lower()
    for cls in rules.get("block_classes", []):
        if cls and cls in lowered[:60_000]:
            return True
    return any(phrase.lower() in lowered for phrase in rules.get("block", []))


def looks_discarded(html: str, rules: dict = None) -> bool:
    """True if the HTML says the product is unavailable."""
    if not html:
        return False
    rules = rules or DEFAULT_RULES
    lowered = html[:300_000].lower()
    return any(phrase.lower() in lowered for phrase in rules.get("discard", []))


def classify_page(html: str, status_code: int, rules: dict = None):
    """
    Classify the raw page before/without full parsing.

    Returns ("discarded", reason) | ("blocked", reason) | ("ok", None)
    """
    if status_code in DEAD_STATUS_CODES:
        return "discarded", f"http_{status_code}"
    if status_code in BLOCKED_STATUS_CODES:
        return "blocked", f"http_{status_code}"
    if looks_discarded(html, rules):
        return "discarded", "product_unavailable"
    if looks_blocked(html, rules):
        return "blocked", "shield_detected"
    return "ok", None


def _selector_matches(el, rule):
    """Apply the exclude_* filters of a rule to a matched element."""
    if rule.get("exclude_class_contains"):
        classes = " ".join(el.get("class") or [])
        if rule["exclude_class_contains"] in classes:
            return False
    if rule.get("exclude_ancestor_class"):
        if el.find_parent(class_=re.compile(re.escape(rule["exclude_ancestor_class"]))):
            return False
    return True


def _apply_regex(text, pattern):
    """Apply an optional post-extraction regex; None means 'no match'."""
    if not pattern:
        return text
    m = re.search(pattern, text)
    if not m:
        return None
    return (m.group(1) if m.lastindex else m.group(0)).strip()


def _extract_one(soup, html, rule) -> str:
    """Extract one field value for a single rule. Never raises."""
    # 1) CSS selectors (in order)
    for selector in rule.get("selectors") or []:
        if not selector:
            continue
        try:
            for el in soup.select(selector):
                if not _selector_matches(el, rule):
                    continue
                if rule.get("attribute"):
                    value = el.get(rule["attribute"])
                    if value:
                        return str(value).strip()
                    continue
                text = el.get_text(" ", strip=True)
                if text:
                    value = _apply_regex(text, rule.get("regex"))
                    if value:
                        return value
        except Exception:
            continue

    # 2) regex on the raw HTML
    if rule.get("regex_on_html"):
        m = re.search(rule["regex_on_html"], html)
        if m:
            value = m.group(1) if m.lastindex else m.group(0)
            return value.strip()

    # 3) default / null marker
    return rule.get("default_value") or ""


def extract_fields(html: str, field_rules: list) -> dict:
    """
    Extract every configured field from the page.

    Returns {field_name: value} for each rule in `field_rules`.
    """
    out = {}
    if not html:
        for rule in field_rules:
            out[rule["field_name"]] = rule.get("default_value") or ""
        return out
    soup = BeautifulSoup(html, "html.parser")
    for rule in field_rules:
        out[rule["field_name"]] = _extract_one(soup, html, rule)
    return out


def parse_page(html: str, rules: dict = None) -> tuple:
    """
    One-stop parsing: (fields_dict, state, reason).

    state is "ok" | "discarded" | "blocked" (status_code-free).
    """
    rules = rules or DEFAULT_RULES
    state, reason = classify_page(html, 200, rules)
    fields = {}
    if state == "ok":
        fields = extract_fields(html, rules["fields"])
    else:
        for rule in rules["fields"]:
            fields[rule["field_name"]] = rule.get("default_value") or ""
    return fields, state, reason
