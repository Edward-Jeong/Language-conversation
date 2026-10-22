ALTER TABLE sessions ADD COLUMN current_retry_turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL;
ALTER TABLE turns ADD COLUMN question_turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_turns_question ON turns(question_turn_id);
