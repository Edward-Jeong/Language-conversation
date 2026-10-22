ALTER TABLE review_items ADD COLUMN current_hint_level TEXT NOT NULL DEFAULT 'H0' CHECK(current_hint_level IN ('H0','H1','H2','H3'));

CREATE TABLE IF NOT EXISTS review_hint_events (
    id TEXT PRIMARY KEY,
    review_item_id TEXT NOT NULL REFERENCES review_items(id) ON DELETE CASCADE,
    hint_level TEXT NOT NULL CHECK(hint_level IN ('H1','H2','H3')),
    content TEXT NOT NULL,
    exposed_at TEXT NOT NULL
);
