"""
Ibis Publisher · db.py
Database initialization and all query helpers for the companion app.
"""

import sqlite3
import os
import sys
import shutil
from pathlib import Path
from datetime import datetime

# ── Data directory ──────────────────────────────────────────────
def get_data_dir() -> Path:
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming')
        return Path(base) / 'IbisPublisher'
    else:
        return Path.home() / 'Library' / 'Application Support' / 'IbisPublisher'

def get_db_path() -> Path:
    return get_data_dir() / 'queue.db'

def get_export_dir() -> Path:
    return get_data_dir() / 'exports'

def get_thumbnail_dir() -> Path:
    return get_data_dir() / 'thumbnails'

def get_schema_path() -> Path:
    """Find schema.sql relative to this file or bundled app."""
    candidates = [
        Path(__file__).parent / 'schema.sql',
        Path(__file__).parent.parent / 'shared' / 'schema.sql',
        Path(sys.executable).parent / 'schema.sql',
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]

# ── Init ────────────────────────────────────────────────────────
def init_db() -> sqlite3.Connection:
    """Create DB and tables if they don't exist. Returns connection."""
    data_dir = get_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    get_export_dir().mkdir(parents=True, exist_ok=True)
    get_thumbnail_dir().mkdir(parents=True, exist_ok=True)

    db_path = get_db_path()
    is_new  = not db_path.exists()

    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")

    if is_new:
        schema_path = get_schema_path()
        if schema_path.exists():
            with open(schema_path, 'r') as f:
                conn.executescript(f.read())
        else:
            _create_tables_inline(conn)
        conn.commit()

    # Migrate: add description column to post_log if missing
    try:
        conn.execute("ALTER TABLE post_log ADD COLUMN description TEXT")
        conn.commit()
    except Exception:
        pass

    # Migrate: add fb_thumbnail_url column to posts if missing
    try:
        conn.execute("ALTER TABLE posts ADD COLUMN fb_thumbnail_url TEXT")
        conn.commit()
    except Exception:
        pass

    # Backup on every launch
    _maybe_backup(db_path)

    return conn

def _maybe_backup(db_path: Path):
    """Keep a rolling backup."""
    backup = db_path.with_suffix('.db.bak')
    try:
        if db_path.exists():
            shutil.copy2(str(db_path), str(backup))
    except Exception:
        pass

def _create_tables_inline(conn: sqlite3.Connection):
    """Fallback schema if schema.sql is not found."""
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            scheduled_time DATETIME NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            caption TEXT NOT NULL DEFAULT '',
            photo_path TEXT NOT NULL,
            photo_path_2 TEXT,
            facebook_post_id TEXT,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at DATETIME,
            next_attempt_at DATETIME,
            last_error TEXT,
            posted_at DATETIME,
            schedule_pattern_id INTEGER,
            sort_order INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS caption_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            body TEXT NOT NULL,
            created_at DATETIME NOT NULL DEFAULT (datetime('now')),
            last_used_at DATETIME
        );
        CREATE TABLE IF NOT EXISTS schedule_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            pattern_type TEXT NOT NULL,
            days_of_week TEXT,
            times_of_day TEXT,
            interval_minutes INTEGER,
            is_active INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS post_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            logged_at DATETIME NOT NULL DEFAULT (datetime('now')),
            event_type TEXT NOT NULL,
            http_status INTEGER,
            response_body TEXT,
            error_message TEXT,
            description TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME NOT NULL DEFAULT (datetime('now'))
        );

        INSERT OR IGNORE INTO schedule_patterns (name, pattern_type, days_of_week, times_of_day, is_active) VALUES
            ('Daily at 8am', 'daily', '["MON","TUE","WED","THU","FRI","SAT","SUN"]', '["08:00"]', 1);

        INSERT OR IGNORE INTO settings (key, value) VALUES
            ('page_id', ''), ('page_name', ''), ('token_expiry', ''),
            ('export_quality', '90'), ('export_max_dimension', '2048'),
            ('notify_on_success', '1'), ('notify_on_failure', '1'),
            ('app_version', '1.0.0'), ('db_version', '1');
    """)

# ── Settings helpers ────────────────────────────────────────────
def get_setting(conn: sqlite3.Connection, key: str, default: str = '') -> str:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    if row and row['value'] is not None:
        return row['value']
    return default

def set_setting(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?,?,datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value)
    )
    conn.commit()

# ── Post queries ────────────────────────────────────────────────
def get_due_posts(conn: sqlite3.Connection):
    """Posts that should fire right now."""
    return conn.execute("""
        SELECT * FROM posts
        WHERE status IN ('scheduled','retrying')
          AND (scheduled_time <= datetime('now', 'localtime') OR next_attempt_at <= datetime('now', 'localtime'))
        ORDER BY scheduled_time ASC
    """).fetchall()

def get_all_posts(conn: sqlite3.Connection, status_filter: str = None):
    if status_filter and status_filter != 'all':
        return conn.execute(
            "SELECT * FROM posts WHERE status=? ORDER BY scheduled_time DESC",
            (status_filter,)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM posts ORDER BY scheduled_time DESC"
    ).fetchall()

def update_post_status(conn: sqlite3.Connection, post_id: int, **kwargs):
    sets = []
    vals = []
    for k, v in kwargs.items():
        sets.append(f"{k}=?")
        vals.append(v)
    vals.append(post_id)
    conn.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()

def delete_post(conn: sqlite3.Connection, post_id: int):
    conn.execute("DELETE FROM posts WHERE id=?", (post_id,))
    conn.commit()

def log_event(conn: sqlite3.Connection, post_id: int, event_type: str,
              http_status: int = None, response_body: str = None, error_message: str = None,
              description: str = None):
    logged_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute("""
        INSERT INTO post_log (post_id, logged_at, event_type, http_status, response_body, error_message, description)
        VALUES (?,?,?,?,?,?,?)
    """, (post_id, logged_at, event_type,
          http_status,
          (response_body or '')[:2000],
          error_message,
          description))
    conn.commit()

def get_log(conn: sqlite3.Connection, limit: int = 200):
    return conn.execute("""
        SELECT l.*, p.caption, p.photo_path
        FROM post_log l
        LEFT JOIN posts p ON l.post_id = p.id
        ORDER BY l.logged_at DESC
        LIMIT ?
    """, (limit,)).fetchall()

def purge_old_logs(conn: sqlite3.Connection):
    conn.execute("DELETE FROM post_log WHERE logged_at < datetime('now', '-90 days')")
    conn.commit()

def handle_missed_posts(conn: sqlite3.Connection):
    """Mark posts older than 24h that were never attempted as 'missed'."""
    conn.execute("""
        UPDATE posts SET status='missed'
        WHERE status='scheduled'
          AND scheduled_time < datetime('now', 'localtime', '-24 hours')
          AND attempt_count = 0
    """)
    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) FROM posts WHERE status='missed' AND created_at > datetime('now','-2 hours')"
    ).fetchone()[0]
