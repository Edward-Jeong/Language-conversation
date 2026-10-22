PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    locale TEXT NOT NULL DEFAULT 'ko-KR',
    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profiles (
    user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    goal TEXT,
    support_preferences TEXT NOT NULL DEFAULT '{}',
    voice_preferences TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scenario_id TEXT NOT NULL,
    scenario_version INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active','paused','ended')),
    learning_state TEXT NOT NULL,
    media_state TEXT NOT NULL,
    current_question_turn_id TEXT,
    pending_next_question TEXT,
    pending_next_intent_id TEXT,
    pending_next_task_variant TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    response_revision INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS turns (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('assistant','user')),
    attempt_kind TEXT NOT NULL,
    parent_turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    final_text TEXT NOT NULL,
    transcript_version INTEGER NOT NULL DEFAULT 1,
    intent_id TEXT,
    task_variant TEXT NOT NULL DEFAULT 'initial',
    client_attempt_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(session_id, client_attempt_id)
);

CREATE TABLE IF NOT EXISTS hint_events (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    hint_level TEXT NOT NULL CHECK(hint_level IN ('H1','H2','H3')),
    content_type TEXT NOT NULL,
    content TEXT NOT NULL,
    exposed_at TEXT NOT NULL,
    UNIQUE(turn_id, hint_level)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id TEXT PRIMARY KEY,
    turn_id TEXT NOT NULL REFERENCES turns(id) ON DELETE CASCADE,
    transcript_version INTEGER NOT NULL,
    response_revision INTEGER NOT NULL,
    schema_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    result_json TEXT NOT NULL,
    assistance_level TEXT NOT NULL,
    outcome TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    invalidated_at TEXT,
    UNIQUE(turn_id, transcript_version)
);

CREATE TABLE IF NOT EXISTS expressions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    intent_id TEXT NOT NULL,
    canonical_form TEXT NOT NULL,
    acceptable_variants TEXT NOT NULL DEFAULT '[]',
    source_turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    user_edited INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(user_id, intent_id, canonical_form)
);

CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY,
    expression_id TEXT NOT NULL UNIQUE REFERENCES expressions(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stage INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL,
    last_advanced_learning_date TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_attempts (
    id TEXT PRIMARY KEY,
    review_item_id TEXT NOT NULL REFERENCES review_items(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    source_turn_id TEXT REFERENCES turns(id) ON DELETE SET NULL,
    client_attempt_id TEXT NOT NULL,
    assistance_level TEXT NOT NULL,
    outcome TEXT NOT NULL,
    task_variant TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    voided_at TEXT,
    UNIQUE(user_id, client_attempt_id)
);

CREATE TABLE IF NOT EXISTS provider_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id TEXT REFERENCES turns(id) ON DELETE CASCADE,
    provider_kind TEXT NOT NULL,
    model_id TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    status TEXT NOT NULL,
    usage_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairing_sessions (
    id TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    authorized_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairing_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fingerprint TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    succeeded INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pairing_attempts_fingerprint_time ON pairing_attempts(fingerprint, attempted_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user_updated ON sessions(user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_review_due ON review_items(user_id, due_at);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn_id TEXT REFERENCES turns(id) ON DELETE CASCADE,
    client_attempt_id TEXT,
    event_type TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    transcript_version INTEGER,
    response_revision INTEGER NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(session_id, sequence)
);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY(user_id, scope, key)
);

CREATE TABLE IF NOT EXISTS import_previews (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    counts_json TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS import_records (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    export_id TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(user_id, export_id)
);
