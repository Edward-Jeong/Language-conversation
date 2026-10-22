ALTER TABLE review_items ADD COLUMN activity_learning_date TEXT;
ALTER TABLE review_items ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3);
