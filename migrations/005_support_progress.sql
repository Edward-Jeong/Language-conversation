CREATE TABLE IF NOT EXISTS intent_practice_successes (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL,
    question_turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY(user_id, intent_id, question_turn_id)
);
