"""Google Sheets module tests (no real Google API needed)."""
import pytest

from app.services import sheets


def test_row_values_shape():
    row = {"catalog_link": "u", "title": "t", "subtitle": "Nuevo | +100 vendidos",
           "price": 123, "competitor": "c",
           "price_in_installments": "3x", "image": "i", "timestamp": "ts",
           "status": "successed", "api_cost_total": 30, "remaining_credits": 500}
    values = sheets._row_values(row)
    assert values == ["u", "t", "Nuevo | +100 vendidos", 123, "c", "3x", "i",
                      "ts", "successed", 30, 500]


def test_row_values_nulls_become_empty_strings():
    row = {}
    values = sheets._row_values(row)
    assert values == ["", "", "", "", "", "", "", "", "", "", ""]


def test_update_skipped_without_credentials(monkeypatch):
    import app.settings.config as cfg
    monkeypatch.setattr(cfg, "SERVICE_ACCOUNT_FILE", None)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert sheets.update_catalogo([{"catalog_link": "u"}]) == 0
