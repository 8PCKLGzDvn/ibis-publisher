-- Ibis Publisher · Database Schema v1.0
-- SQLite database shared between Lightroom plugin and companion app
-- Location: ~/Library/Application Support/IbisPublisher/queue.db (macOS)
--           %APPDATA%\IbisPublisher\queue.db (Windows)

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Posts ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          DATETIME NOT NULL DEFAULT (datetime('now')),
    scheduled_time      DATETIME NOT NULL,
    status              TEXT NOT NULL DEFAULT 'scheduled'
                            CHECK(status IN ('scheduled','posting','posted','failed','retrying','missed','cancelled')),
    caption             TEXT NOT NULL DEFAULT '',
    photo_path          TEXT NOT NULL,
    photo_path_2        TEXT,
    facebook_post_id    TEXT,
    attempt_count       INTEGER NOT NULL DEFAULT 0,
    last_attempt_at     DATETIME,
    next_attempt_at     DATETIME,
    last_error          TEXT,
    posted_at           DATETIME,
    schedule_pattern_id INTEGER REFERENCES schedule_patterns(id) ON DELETE SET NULL,
    sort_order          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
CREATE INDEX IF NOT EXISTS idx_posts_scheduled_time ON posts(scheduled_time);
CREATE INDEX IF NOT EXISTS idx_posts_next_attempt ON posts(next_attempt_at);

-- ── Caption Templates ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS caption_templates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    body         TEXT NOT NULL,
    created_at   DATETIME NOT NULL DEFAULT (datetime('now')),
    last_used_at DATETIME
);

-- ── Schedule Patterns ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schedule_patterns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    pattern_type     TEXT NOT NULL CHECK(pattern_type IN ('weekly','daily','interval','custom')),
    days_of_week     TEXT,   -- JSON array e.g. ["MON","WED","FRI"]
    times_of_day     TEXT,   -- JSON array e.g. ["19:00","08:00"]
    interval_minutes INTEGER,
    is_active        INTEGER NOT NULL DEFAULT 0
);

-- ── Post Log ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS post_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    logged_at     DATETIME NOT NULL DEFAULT (datetime('now')),
    event_type    TEXT NOT NULL CHECK(event_type IN ('attempt','success','failure','retry','cancelled','missed')),
    http_status   INTEGER,
    response_body TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_post_log_post_id ON post_log(post_id);
CREATE INDEX IF NOT EXISTS idx_post_log_logged_at ON post_log(logged_at);

-- ── Settings ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ── Seed Default Data ──────────────────────────────────────────
INSERT OR IGNORE INTO caption_templates (name, body) VALUES
    ('Simple date & camera',    '{capture_date} · Shot on {camera}'),
    ('Technical details',       '{capture_date} · {camera} · {lens} · {aperture} · {shutter} · ISO {iso}'),
    ('Location & keywords',     '{location} · {keywords}'),
    ('Minimal filename',        '{filename}');

INSERT OR IGNORE INTO schedule_patterns (name, pattern_type, days_of_week, times_of_day, is_active) VALUES
    ('Daily at 8am',         'daily',   '["MON","TUE","WED","THU","FRI","SAT","SUN"]', '["08:00"]', 1),
    ('Mon/Wed/Fri at 7pm',   'weekly',  '["MON","WED","FRI"]', '["19:00"]', 0),
    ('Weekdays at 9am',      'weekly',  '["MON","TUE","WED","THU","FRI"]', '["09:00"]', 0),
    ('Twice daily',          'daily',   '["MON","TUE","WED","THU","FRI","SAT","SUN"]', '["08:00","18:00"]', 0);

INSERT OR IGNORE INTO settings (key, value) VALUES
    ('page_id', ''),
    ('page_name', ''),
    ('token_expiry', ''),
    ('export_quality', '90'),
    ('export_max_dimension', '2048'),
    ('export_color_space', 'sRGB'),
    ('notify_on_success', '1'),
    ('notify_on_failure', '1'),
    ('app_version', '1.0.0'),
    ('db_version', '1');
