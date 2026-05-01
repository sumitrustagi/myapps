import sqlite3
import os
from datetime import datetime, timedelta


def _db_path() -> str:
    return os.environ.get('CRAWLR_DB', 'crawler.db')


def get_conn():
    conn = sqlite3.connect(_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS crawl_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            base_url        TEXT NOT NULL,
            name            TEXT,
            started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at    TIMESTAMP,
            status          TEXT DEFAULT 'running',
            total_articles  INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS articles (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            base_url        TEXT NOT NULL,
            url             TEXT NOT NULL,
            title           TEXT,
            content_hash    TEXT,
            file_type       TEXT DEFAULT 'page',
            file_size       INTEGER DEFAULT 0,
            download_path   TEXT,
            pdf_path        TEXT,
            status          TEXT DEFAULT 'discovered',
            first_seen      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_checked    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_modified   TIMESTAMP,
            revision        INTEGER DEFAULT 1,
            is_deleted      INTEGER DEFAULT 0,
            UNIQUE(base_url, url)
        );

        CREATE TABLE IF NOT EXISTS site_auth (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            base_url        TEXT UNIQUE NOT NULL,
            auth_type       TEXT DEFAULT 'none',
            username        TEXT,
            password        TEXT,
            login_url       TEXT,
            login_user_field TEXT DEFAULT 'username',
            login_pass_field TEXT DEFAULT 'password',
            extra_fields    TEXT,
            token           TEXT,
            cookies         TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scheduled_sites (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT UNIQUE NOT NULL,
            name        TEXT,
            enabled     INTEGER DEFAULT 1,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_run    TIMESTAMP,
            next_run    TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS activity_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER,
            timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            event_type  TEXT,
            message     TEXT,
            url         TEXT
        );
    ''')
    # Migrate existing DB: add new columns if not present
    _migrate(conn)
    conn.commit()
    conn.close()
    print("[DB] Initialized")


def _migrate(conn):
    """Add new columns to existing tables without dropping data."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    for col, defn in [
        ('file_type',     "TEXT DEFAULT 'page'"),
        ('file_size',     "INTEGER DEFAULT 0"),
        ('download_path', "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {defn}")

    existing_auth = {row[1] for row in conn.execute(
        "SELECT * FROM sqlite_master WHERE type='table' AND name='site_auth'")}
    # table created above if not exists — no action needed


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(base_url, name=None):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO crawl_sessions (base_url, name, status) VALUES (?, ?, 'running')",
        (base_url, name or base_url)
    )
    sid = c.lastrowid
    conn.commit(); conn.close()
    return sid


def get_sessions():
    conn = get_conn()
    rows = conn.execute('''
        SELECT s.*,
            COUNT(DISTINCT a.id)                                               AS article_count,
            SUM(CASE WHEN a.status='downloaded' AND a.is_deleted=0 THEN 1 ELSE 0 END) AS downloaded,
            SUM(CASE WHEN a.status='changed'    AND a.is_deleted=0 THEN 1 ELSE 0 END) AS changed,
            SUM(CASE WHEN a.status='unchanged'  AND a.is_deleted=0 THEN 1 ELSE 0 END) AS unchanged,
            SUM(CASE WHEN a.is_deleted=1                            THEN 1 ELSE 0 END) AS deleted
        FROM crawl_sessions s
        LEFT JOIN articles a ON a.base_url = s.base_url
        GROUP BY s.id
        ORDER BY s.started_at DESC
    ''').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_session(session_id):
    conn = get_conn()
    row = conn.execute('SELECT * FROM crawl_sessions WHERE id=?', (session_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def complete_session(session_id, total):
    conn = get_conn()
    conn.execute(
        "UPDATE crawl_sessions SET status='completed', completed_at=?, total_articles=? WHERE id=?",
        (datetime.now().isoformat(), total, session_id)
    )
    conn.commit(); conn.close()


# ── Articles ──────────────────────────────────────────────────────────────────

def get_articles(base_url, status_filter=None):
    conn = get_conn()
    if status_filter == 'deleted':
        rows = conn.execute(
            'SELECT * FROM articles WHERE base_url=? AND is_deleted=1 ORDER BY last_checked DESC',
            (base_url,)).fetchall()
    elif status_filter:
        rows = conn.execute(
            'SELECT * FROM articles WHERE base_url=? AND status=? AND is_deleted=0 ORDER BY last_checked DESC',
            (base_url, status_filter)).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM articles WHERE base_url=? ORDER BY last_checked DESC',
            (base_url,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(base_url):
    conn = get_conn()
    row = conn.execute('''
        SELECT
            COUNT(*)                                                               AS total,
            SUM(CASE WHEN status='downloaded' AND is_deleted=0 THEN 1 ELSE 0 END) AS downloaded,
            SUM(CASE WHEN status='changed'    AND is_deleted=0 THEN 1 ELSE 0 END) AS changed,
            SUM(CASE WHEN status='unchanged'  AND is_deleted=0 THEN 1 ELSE 0 END) AS unchanged,
            SUM(CASE WHEN status='error'                        THEN 1 ELSE 0 END) AS errors,
            SUM(CASE WHEN is_deleted=1                          THEN 1 ELSE 0 END) AS deleted,
            SUM(CASE WHEN file_type != 'page' AND is_deleted=0  THEN 1 ELSE 0 END) AS assets
        FROM articles WHERE base_url=?
    ''', (base_url,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def get_or_create_article(base_url, url, title, content_hash, file_type='page'):
    conn = get_conn()
    existing = conn.execute(
        'SELECT * FROM articles WHERE base_url=? AND url=?', (base_url, url)
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO articles (base_url,url,title,content_hash,file_type,status,is_deleted)"
            " VALUES (?,?,?,?,?,'discovered',0)",
            (base_url, url, title, content_hash, file_type)
        )
        conn.commit(); conn.close()
        return {'is_new': True, 'hash_changed': False}
    existing_dict = dict(existing)
    hash_changed = existing_dict['content_hash'] != content_hash
    conn.close()
    return {'is_new': False, 'hash_changed': hash_changed}


def update_article_status(base_url, url, status, content_hash, title,
                           pdf_path=None, download_path=None, file_type=None,
                           file_size=0, increment_revision=False):
    conn = get_conn()
    now = datetime.now().isoformat()
    if increment_revision:
        conn.execute('''
            UPDATE articles
            SET status=?, content_hash=?, title=?,
                pdf_path=COALESCE(?,pdf_path),
                download_path=COALESCE(?,download_path),
                file_type=COALESCE(?,file_type),
                file_size=COALESCE(?,file_size),
                last_checked=?, last_modified=?,
                revision=revision+1, is_deleted=0
            WHERE base_url=? AND url=?
        ''', (status, content_hash, title, pdf_path, download_path,
              file_type, file_size if file_size else None,
              now, now, base_url, url))
    else:
        conn.execute('''
            UPDATE articles
            SET status=?, content_hash=?, title=?,
                pdf_path=COALESCE(?,pdf_path),
                download_path=COALESCE(?,download_path),
                file_type=COALESCE(?,file_type),
                file_size=COALESCE(?,file_size),
                last_checked=?, is_deleted=0
            WHERE base_url=? AND url=?
        ''', (status, content_hash, title, pdf_path, download_path,
              file_type, file_size if file_size else None,
              now, base_url, url))
    conn.commit(); conn.close()


def mark_deleted_articles(base_url, current_urls):
    conn = get_conn()
    rows = conn.execute(
        'SELECT url FROM articles WHERE base_url=? AND is_deleted=0', (base_url,)
    ).fetchall()
    existing = {r[0] for r in rows}
    deleted = existing - current_urls
    now = datetime.now().isoformat()
    for u in deleted:
        conn.execute(
            "UPDATE articles SET is_deleted=1, status='deleted', last_checked=?"
            " WHERE base_url=? AND url=?", (now, base_url, u)
        )
    conn.commit(); conn.close()
    return len(deleted)


# ── Site Auth ─────────────────────────────────────────────────────────────────

def save_site_auth(base_url, auth_type, username='', password='',
                   login_url='', login_user_field='username',
                   login_pass_field='password', extra_fields='',
                   token='', cookies=''):
    conn = get_conn()
    conn.execute('''
        INSERT INTO site_auth
            (base_url, auth_type, username, password, login_url,
             login_user_field, login_pass_field, extra_fields, token, cookies, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(base_url) DO UPDATE SET
            auth_type=excluded.auth_type, username=excluded.username,
            password=excluded.password, login_url=excluded.login_url,
            login_user_field=excluded.login_user_field,
            login_pass_field=excluded.login_pass_field,
            extra_fields=excluded.extra_fields,
            token=excluded.token, cookies=excluded.cookies,
            updated_at=excluded.updated_at
    ''', (base_url, auth_type, username, password, login_url,
          login_user_field, login_pass_field, extra_fields,
          token, cookies, datetime.now().isoformat()))
    conn.commit(); conn.close()


def get_site_auth(base_url):
    conn = get_conn()
    row = conn.execute('SELECT * FROM site_auth WHERE base_url=?', (base_url,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_site_auth(base_url):
    conn = get_conn()
    conn.execute('DELETE FROM site_auth WHERE base_url=?', (base_url,))
    conn.commit(); conn.close()


# ── Schedule ──────────────────────────────────────────────────────────────────

def add_scheduled_site(url, name=None):
    conn = get_conn()
    try:
        c = conn.cursor()
        c.execute('INSERT INTO scheduled_sites (url, name) VALUES (?,?)', (url, name or url))
        conn.commit(); sid = c.lastrowid
    except sqlite3.IntegrityError:
        conn.execute('UPDATE scheduled_sites SET enabled=1 WHERE url=?', (url,))
        conn.commit()
        sid = conn.execute('SELECT id FROM scheduled_sites WHERE url=?', (url,)).fetchone()[0]
    conn.close()
    return sid


def get_scheduled_sites():
    conn = get_conn()
    rows = conn.execute('SELECT * FROM scheduled_sites ORDER BY created_at DESC').fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_scheduled_site_run(url):
    conn = get_conn()
    next_run = (datetime.now() + timedelta(weeks=1)).isoformat()
    conn.execute(
        'UPDATE scheduled_sites SET last_run=?, next_run=? WHERE url=?',
        (datetime.now().isoformat(), next_run, url)
    )
    conn.commit(); conn.close()


# ── Activity log ──────────────────────────────────────────────────────────────

def log_activity(session_id, event_type, message, url=None):
    try:
        conn = get_conn()
        conn.execute(
            'INSERT INTO activity_log (session_id,event_type,message,url) VALUES (?,?,?,?)',
            (session_id, event_type, message[:500], url)
        )
        conn.commit(); conn.close()
    except Exception:
        pass


def get_activity_log(session_id, limit=200):
    conn = get_conn()
    rows = conn.execute(
        'SELECT * FROM activity_log WHERE session_id=? ORDER BY timestamp DESC LIMIT ?',
        (session_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
