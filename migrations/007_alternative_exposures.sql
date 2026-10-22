CREATE TABLE IF NOT EXISTS alternative_exposures (
    id TEXT PRIMARY KEY,
    answer_turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    question_turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    content TEXT NOT NULL,
    exposed_at TEXT NOT NULL,
    UNIQUE(answer_turn_id)
);
