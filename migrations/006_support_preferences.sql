CREATE TABLE IF NOT EXISTS intent_support_preferences (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL,
    current_level TEXT NOT NULL DEFAULT 'A' CHECK(current_level IN ('A','B','C')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, intent_id)
);
