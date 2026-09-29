-- =====================================================================
-- Field-extraction config: single source of truth for WHAT is scraped
-- and HOW (selectors, attributes, regex, required/optional).
-- The pipeline reads this table at every run; add/remove a row to
-- add/remove a scraped field. No code changes needed.
-- =====================================================================

CREATE TABLE IF NOT EXISTS scraping_field_config (
  id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  field_name VARCHAR(64) NOT NULL,
  kind ENUM('field','discard_phrase','block_phrase','block_class') NOT NULL DEFAULT 'field',
  selectors JSON NULL,                 -- ordered CSS selector list (kind='field')
  pattern VARCHAR(255) NULL,           -- phrase / class / regex (other kinds)
  is_regex TINYINT(1) NOT NULL DEFAULT 0,
  attribute VARCHAR(64) NULL,          -- take this HTML attribute instead of text (e.g. 'src')
  regex TEXT NULL,                     -- post-extraction regex on the matched text (group 1 wins)
  regex_on_html TEXT NULL,             -- regex applied directly to the raw HTML
  exclude_class_contains VARCHAR(128) NULL,  -- skip elements whose class contains this
  exclude_ancestor_class VARCHAR(128) NULL,  -- skip elements inside such an ancestor
  default_value VARCHAR(255) NULL,     -- null marker when nothing is found
  required TINYINT(1) NOT NULL DEFAULT 0,    -- page FAILS when this field is missing
  priority INT NOT NULL DEFAULT 0,
  enabled TINYINT(1) NOT NULL DEFAULT 1,
  PRIMARY KEY (id),
  KEY idx_scraping_field_config_name (field_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =====================================================================
-- The fields currently scraped (verified against real Mercado Libre
-- catalog + classic pages):
-- =====================================================================
INSERT INTO scraping_field_config
  (field_name, kind, selectors, attribute, regex, regex_on_html,
   exclude_class_contains, exclude_ancestor_class, default_value, required, priority)
VALUES
('title', 'field',
 '["h1.ui-pdp-title"]',
 NULL, NULL, NULL, NULL, NULL, 'n/a', 1, 10),

('price', 'field',
 '["div.ui-pdp-price__second-line span.andes-money-amount__fraction",
   ".ui-pdp-price__part span.andes-money-amount__fraction",
   "span.andes-money-amount__fraction"]',
 NULL, NULL, NULL, '--previous', 'poly-card__content', '', 0, 20),

('competitor', 'field',
 '["h2.ui-seller-data-header__title",
   ".ui-seller-data-header__title"]',
 NULL, NULL, '[Vv]endido por\\s*(?:</?[^>]*>)*\\s*([^<&]{2,60})',
 NULL, NULL, 'n/a', 0, 30),

('price_in_installments', 'field',
 '["#pricing_price_subtitle"]',
 NULL, '(?is)^(.+?cuotas de )(\\$?)\\s*([\\d.]+)\\s*(,)\\s*(\\d+)\\s*$',
 NULL, NULL, NULL, 'n/a', 0, 39),

('price_in_installments', 'field',
 '["div.ui-pdp-price__subtitles",
   ".ui-pdp-products__list"]',
 NULL, '(?i)(?:cuota promocionada en|hasta)?\\s*(\\d+\\s*(?:x|cuotas)[^\\n|]{0,60})',
 NULL, NULL, NULL, 'n/a', 0, 40),

('image', 'field',
 '["img.ui-pdp-image"]',
 'src', NULL, NULL, NULL, NULL, 'n/a', 0, 50);

-- =====================================================================
-- Page classification rules (discard = product unavailable,
-- block = anti-bot shield / login wall):
-- =====================================================================
INSERT INTO scraping_field_config (field_name, kind, pattern, priority) VALUES
('discard', 'discard_phrase', 'este producto no está disponible', 100),
('discard', 'discard_phrase', 'no encontramos la página', 101),
('discard', 'discard_phrase', 'error 404', 102),
('block', 'block_phrase', 'protegemos a nuestros usuarios', 110),
('block', 'block_phrase', 'no eres un robot', 111),
('block', 'block_phrase', 'completa el siguiente captcha', 112),
('block', 'block_phrase', 'actividad inusual', 113),
('block', 'block_phrase', 'para continuar, ingresa a', 114),
('block', 'block_phrase', 'ingresa a tu cuenta', 115),
('block', 'block_class', 'message-card', 116);
