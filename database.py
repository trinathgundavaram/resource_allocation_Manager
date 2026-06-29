"""
database.py
-----------
SQLite layer for the Resource Allocation Management application.

Responsibilities:
  * Open connections (WAL mode, foreign keys ON).
  * Create the full schema (STRICT tables + CHECK constraints).
  * Provide small query / execute helpers used across the UI modules.
  * Handle automatic + manual backups and restores.

All percentage / lifecycle business rules live in logic.py — this file
only owns persistence and structural constraints.
"""

import os
import shutil
import sqlite3
import datetime as _dt
from contextlib import contextmanager

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "allocations.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")

MAX_BACKUPS = 30

os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)


# --------------------------------------------------------------------------- #
# Connections
# --------------------------------------------------------------------------- #
def get_connection():
    """Return a configured SQLite connection.

    WAL journal mode + foreign keys on, as required.  Row factory is set so
    callers can use dict-style access (``row["name"]``).
    """
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


@contextmanager
def transaction():
    """Context manager that wraps work in a single SQLite transaction.

    Usage:
        with transaction() as conn:
            conn.execute(...)
    Commits on success, ROLLBACKs on any exception (re-raised).
    """
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def query(sql, params=()):
    """Run a SELECT and return a list of sqlite3.Row."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        return cur.fetchall()
    finally:
        conn.close()


def query_one(sql, params=()):
    """Run a SELECT and return the first row (or None)."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params)
        return cur.fetchone()
    finally:
        conn.close()


def execute(sql, params=()):
    """Run a single write statement in its own transaction; return lastrowid."""
    with transaction() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def now_iso():
    """Current timestamp as ISO string (seconds precision)."""
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_iso():
    return _dt.date.today().strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS clients (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS managers (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS resources (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    role_id       INTEGER REFERENCES roles(id),
    manager_id    INTEGER REFERENCES managers(id),
    hours_per_day REAL NOT NULL DEFAULT 8 CHECK (hours_per_day > 0 AND hours_per_day <= 24),
    days_per_week REAL NOT NULL DEFAULT 5 CHECK (days_per_week > 0 AND days_per_week <= 7),
    status        TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','INACTIVE')),
    created_at    TEXT NOT NULL,
    created_by    TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS resource_billing_rates (
    id                  INTEGER PRIMARY KEY,
    resource_id         INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    rate                REAL NOT NULL CHECK (rate >= 0),
    effective_from_date TEXT NOT NULL,
    created_by          TEXT,
    created_at          TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS projects (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    code            TEXT,
    client_id       INTEGER REFERENCES clients(id),
    is_baseline     INTEGER NOT NULL DEFAULT 0 CHECK (is_baseline IN (0,1)),
    start_month     INTEGER NOT NULL CHECK (start_month BETWEEN 1 AND 12),
    start_year      INTEGER NOT NULL CHECK (start_year BETWEEN 2020 AND 2100),
    end_month       INTEGER NOT NULL CHECK (end_month BETWEEN 1 AND 12),
    end_year        INTEGER NOT NULL CHECK (end_year BETWEEN 2020 AND 2100),
    status          TEXT NOT NULL DEFAULT 'ESTIMATE',
    color           TEXT DEFAULT '#4C78A8',
    priority        TEXT DEFAULT 'MEDIUM',
    project_lead_id INTEGER REFERENCES managers(id),
    notes           TEXT,
    created_at      TEXT NOT NULL,
    created_by      TEXT,
    CHECK (end_year * 12 + end_month >= start_year * 12 + start_month)
) STRICT;

CREATE TABLE IF NOT EXISTS project_budgets (
    id                  INTEGER PRIMARY KEY,
    project_id          INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    budget_amount       REAL NOT NULL CHECK (budget_amount >= 0),
    effective_from_date TEXT NOT NULL,
    note                TEXT,
    created_by          TEXT,
    created_at          TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS project_status_history (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    old_status  TEXT,
    new_status  TEXT NOT NULL,
    changed_at  TEXT NOT NULL,
    changed_by  TEXT,
    reason      TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS project_assumptions (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    created_by  TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS project_attachments (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    filepath    TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    uploaded_by TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS holidays (
    id           INTEGER PRIMARY KEY,
    holiday_date TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    created_at   TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS allocations (
    id               INTEGER PRIMARY KEY,
    resource_id      INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    project_id       INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    year             INTEGER NOT NULL CHECK (year BETWEEN 2020 AND 2100),
    month            INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    percentage       REAL NOT NULL CHECK (percentage >= 0 AND percentage <= 100),
    assigned_date    TEXT,
    assigned_by      TEXT,
    last_modified_at TEXT,
    last_modified_by TEXT,
    notes            TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    UNIQUE (resource_id, project_id, year, month)
) STRICT;

CREATE TABLE IF NOT EXISTS allocation_history (
    id             INTEGER PRIMARY KEY,
    allocation_id  INTEGER,
    resource_id    INTEGER NOT NULL,
    project_id     INTEGER NOT NULL,
    year           INTEGER NOT NULL,
    month          INTEGER NOT NULL,
    old_percentage REAL,
    new_percentage REAL,
    change_type    TEXT NOT NULL,
    changed_at     TEXT NOT NULL,
    changed_by     TEXT,
    reason         TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS allocations_archive (
    id               INTEGER PRIMARY KEY,
    archived_year    INTEGER NOT NULL,
    resource_id      INTEGER NOT NULL,
    project_id       INTEGER NOT NULL,
    year             INTEGER NOT NULL,
    month            INTEGER NOT NULL,
    percentage       REAL NOT NULL,
    assigned_date    TEXT,
    assigned_by      TEXT,
    last_modified_at TEXT,
    last_modified_by TEXT,
    notes            TEXT,
    is_active        INTEGER NOT NULL DEFAULT 1
) STRICT;

CREATE TABLE IF NOT EXISTS resource_availability (
    id          INTEGER PRIMARY KEY,
    resource_id INTEGER NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    start_date  TEXT,
    end_date    TEXT,
    kind        TEXT,
    note        TEXT,
    created_at  TEXT
) STRICT;

CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS idx_alloc_rpym ON allocations (resource_id, project_id, year, month);
CREATE INDEX IF NOT EXISTS idx_alloc_ym ON allocations (year, month);
CREATE INDEX IF NOT EXISTS idx_rate_resource ON resource_billing_rates (resource_id, effective_from_date);
CREATE INDEX IF NOT EXISTS idx_budget_project ON project_budgets (project_id, effective_from_date);
CREATE INDEX IF NOT EXISTS idx_hist_changed ON allocation_history (changed_at);
"""


def init_db():
    """Create all tables / indexes if they do not yet exist, then run any
    lightweight column migrations for databases created by older versions."""
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
    finally:
        conn.close()


def _migrate(conn):
    """Idempotent additive migrations (ADD COLUMN only)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(resources)")}
    if "manager_id" not in cols:
        conn.execute(
            "ALTER TABLE resources ADD COLUMN manager_id INTEGER REFERENCES managers(id)")
    pcols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "code" not in pcols:
        conn.execute("ALTER TABLE projects ADD COLUMN code TEXT")


def get_setting(key, default=None):
    row = query_one("SELECT value FROM app_settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key, value):
    execute(
        """INSERT INTO app_settings (key, value, updated_at) VALUES (?,?,?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, str(value), now_iso()),
    )


# --------------------------------------------------------------------------- #
# Backups
# --------------------------------------------------------------------------- #
def list_backups():
    """Return list of (filename, full_path, timestamp_str) newest first."""
    if not os.path.isdir(BACKUP_DIR):
        return []
    items = []
    for fn in os.listdir(BACKUP_DIR):
        if fn.startswith("allocations_") and fn.endswith(".db"):
            full = os.path.join(BACKUP_DIR, fn)
            mtime = os.path.getmtime(full)
            ts = _dt.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
            items.append((fn, full, ts))
    items.sort(key=lambda x: x[1], reverse=True)
    return items


def make_backup():
    """Copy the live DB to a timestamped backup file; prune to MAX_BACKUPS.

    Returns the backup path, or None if there is nothing to back up yet.
    """
    if not os.path.exists(DB_PATH):
        return None
    # Checkpoint WAL so the copied file is fully consistent.
    try:
        conn = get_connection()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        conn.close()
    except Exception:
        pass
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = os.path.join(BACKUP_DIR, f"allocations_{stamp}.db")
    shutil.copy2(DB_PATH, dest)
    _prune_backups()
    set_setting("last_backup_at", now_iso())
    return dest


def _prune_backups():
    backups = list_backups()
    for fn, full, _ in backups[MAX_BACKUPS:]:
        try:
            os.remove(full)
        except OSError:
            pass


def restore_backup(backup_path):
    """Restore the database from a backup file.

    A safety backup of the current DB is taken first.  WAL/SHM side files are
    removed so SQLite re-derives them from the restored main file.
    """
    if not os.path.exists(backup_path):
        raise FileNotFoundError(backup_path)
    make_backup()  # safety copy of current state
    for ext in ("-wal", "-shm"):
        side = DB_PATH + ext
        if os.path.exists(side):
            try:
                os.remove(side)
            except OSError:
                pass
    shutil.copy2(backup_path, DB_PATH)
    # Re-enable WAL on the restored file.
    conn = get_connection()
    conn.close()


def auto_backup_on_startup():
    """Run one backup per process start (guarded so reruns don't spam)."""
    make_backup()


if __name__ == "__main__":
    # Phase 1 smoke test: create schema and report tables.
    init_db()
    rows = query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    print("Tables created:")
    for r in rows:
        print("  -", r["name"])
