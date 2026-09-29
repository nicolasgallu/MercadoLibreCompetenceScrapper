"""Unit tests for the rules-driven parser."""
import copy
import os

from app.services.parser import (
    DEFAULT_RULES,
    classify_page,
    extract_fields,
    looks_blocked,
    looks_discarded,
    parse_page,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")

SUCCESS_HTML = """
<html><body>
<h1 class="ui-pdp-title">Celular Samsung Galaxy S23 128gb</h1>
<div class="ui-pdp-price__second-line">
  <span class="andes-money-amount__fraction">599.999</span>
</div>
<h2 class="ui-seller-data-header__title">SAMSUNG OFICIAL</h2>
<div class="ui-pdp-price__subtitles">12x $49.999 sin interés</div>
<img class="ui-pdp-image" src="https://http2.mlstatic.com/D_NQ_123.jpg">
</body></html>
"""

BLOCKED_HTML = """
<html><body>
<h1>Protegemos a nuestros usuarios</h1>
<p>Completa el siguiente captcha para continuar.</p>
</body></html>
"""

WALL_HTML = """
<html><body><p class="message-card">¡Hola! Para continuar, ingresa a<br/>tu cuenta</p></body></html>
"""

DISCARDED_HTML = """
<html><body>
<div class="ui-pdp-shipping-message__text">
Este producto no está disponible por el momento.
</div>
</body></html>
"""

# catalog page with variants UI: "Elige otra variante" is NOT a discard signal
VARIANTS_HTML = """
<html><body>
<h1 class="ui-pdp-title">Zapatillas con variantes</h1>
<p>Elige otra variante para ver el precio</p>
</body></html>
"""


def test_extract_fields_success():
    fields = extract_fields(SUCCESS_HTML, DEFAULT_RULES["fields"])
    assert fields["title"] == "Celular Samsung Galaxy S23 128gb"
    assert fields["price"] == "599.999"
    assert fields["competitor"] == "SAMSUNG OFICIAL"
    assert fields["price_in_installments"] == "12x $49.999 sin interés"
    assert fields["image"].startswith("https://http2.mlstatic.com")


def test_extract_fields_empty():
    fields = extract_fields("", DEFAULT_RULES["fields"])
    assert fields["title"] == "n/a"
    assert fields["price"] == ""
    assert fields["competitor"] == "n/a"


def test_extract_missing_title_uses_default():
    fields = extract_fields("<html><body><h1>Otro</h1></body></html>",
                            DEFAULT_RULES["fields"])
    assert fields["title"] == "n/a"


def test_block_detection():
    assert looks_blocked(BLOCKED_HTML)
    assert not looks_blocked(SUCCESS_HTML)


def test_wall_detection_via_message_card():
    assert looks_blocked(WALL_HTML)


def test_discard_detection():
    assert looks_discarded(DISCARDED_HTML)
    assert not looks_discarded(SUCCESS_HTML)


def test_variants_page_is_not_discarded():
    assert not looks_discarded(VARIANTS_HTML)
    assert classify_page(VARIANTS_HTML, 200) == ("ok", None)


def test_classify_ok():
    assert classify_page(SUCCESS_HTML, 200) == ("ok", None)


def test_classify_blocked_by_status():
    assert classify_page(SUCCESS_HTML, 403)[0] == "blocked"


def test_classify_discarded_by_status():
    assert classify_page(SUCCESS_HTML, 404)[0] == "discarded"


def test_classify_blocked_by_content():
    assert classify_page(BLOCKED_HTML, 200)[0] == "blocked"


def test_classify_discarded_by_content():
    assert classify_page(DISCARDED_HTML, 200)[0] == "discarded"


# ── custom rules: add / remove fields ─────────────────────────
def test_custom_rules_only_extract_configured_fields():
    rules = {"fields": [
        {"field_name": "title", "selectors": ["h1.ui-pdp-title"], "default_value": "n/a"},
        {"field_name": "precio", "selectors": ["span.andes-money-amount__fraction"],
         "default_value": ""},
    ]}
    fields = extract_fields(SUCCESS_HTML, rules["fields"])
    assert set(fields.keys()) == {"title", "precio"}
    assert fields["precio"] == "599.999"


def test_parse_page_uses_custom_discard_phrase():
    rules = copy.deepcopy(DEFAULT_RULES)
    rules["discard"] = ["zapatillas con variantes"]  # arbitrary custom phrase
    fields, state, reason = parse_page(VARIANTS_HTML, rules)
    assert state == "discarded"


# ── real-dom fixture tests ─────────────────────────────────────
def test_real_catalog_available():
    html = open(os.path.join(FIXTURES, "catalog_available.html"),
                encoding="utf-8", errors="ignore").read()
    assert classify_page(html, 200) == ("ok", None)
    fields = extract_fields(html, DEFAULT_RULES["fields"])
    assert fields["title"] == "Consola Novik Neo NVK-I08BT de mezcla"
    assert fields["price"] == "199.999"          # buy box, not a related card
    assert fields["image"].startswith("https://http2.mlstatic.com")
    assert fields["competitor"] == "n/a"         # hidden in catalog SSR
    assert fields["price_in_installments"] == "n/a"


def test_real_catalog_unavailable():
    html = open(os.path.join(FIXTURES, "catalog_unavailable.html"),
                encoding="utf-8", errors="ignore").read()
    assert classify_page(html, 200) == ("discarded", "product_unavailable")
    assert "no está disponible" in html.lower()


def test_real_wall_page():
    html = open(os.path.join(FIXTURES, "wall.html"),
                encoding="utf-8", errors="ignore").read()
    assert classify_page(html, 200) == ("blocked", "shield_detected")


# ── new pricing_price_subtitle element ───────────────────────
PRICING_SUBTITLE_HTML = """
<p class="ui-pdp-color--GREEN ui-pdp-size--XSMALL ui-pdp-family--REGULAR spacing-layout my-0" id="pricing_price_subtitle">
  <span>Mismo precio en 9 cuotas de </span>
  <span class="ui-pdp-price__part__container">
    <span class="andes-money-amount ui-pdp-price__part andes-money-amount--cents-comma andes-money-amount--compact" aria-label="176273 pesos con 33 centavos">
      <span class="andes-money-amount__currency-symbol">$</span>
      <span class="andes-money-amount__fraction">176.273</span>,
      <span class="andes-money-amount__cents">33</span>
    </span>
  </span>
</p>
"""


def test_pricing_subtitle_reassembled_exactly():
    fields = extract_fields(PRICING_SUBTITLE_HTML, DEFAULT_RULES["fields"])
    assert fields["price_in_installments"] == "Mismo precio en 9 cuotas de $176.273,33"


def test_pricing_subtitle_falls_back_to_classic_subtitles():
    html = """
    <html><body>
    <div class="ui-pdp-price__subtitles">12x $49.999 sin interés</div>
    </body></html>
    """
    fields = extract_fields(html, DEFAULT_RULES["fields"])
    assert fields["price_in_installments"] == "12x $49.999 sin interés"


def test_multi_group_regex_join():
    from app.services.parser import _apply_regex
    text = "Mismo precio en 9 cuotas de $ 176.273 , 33"
    pattern = r"(?is)^(.+?cuotas de )(\$?)\s*([\d.]+)\s*(,)\s*(\d+)\s*$"
    assert _apply_regex(text, pattern) == "Mismo precio en 9 cuotas de $176.273,33"
