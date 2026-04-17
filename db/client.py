"""
db/client.py
------------
SQLite connection manager (replaces Supabase client singleton).

Provides:
  - get_connection() — returns a sqlite3 connection with foreign keys enabled
                       and row_factory set to sqlite3.Row for dict-like access.
  - init_db()        — creates tables by executing schema.sql if they do not exist.

All other modules call get_connection() and close the connection themselves,
or use it as a context manager (sqlite3.Connection supports __enter__/__exit__).
"""

import logging
import os
import sqlite3
from pathlib import Path

import book_gen.config as cfg

log = logging.getLogger(__name__)


def _ensure_dir(db_path: str) -> None:
    """
    Create parent directories for the database file if they do not exist.

    Args:
        db_path: Absolute or relative path to the .db file.
    """
    parent = Path(db_path).parent
    parent.mkdir(parents=True, exist_ok=True)


def get_connection() -> sqlite3.Connection:
    """
    Open and return a new SQLite connection to the configured database file.

    Row factory is set to sqlite3.Row so rows can be accessed as dicts.
    Foreign key enforcement is enabled per-connection.

    Returns:
        sqlite3.Connection: An open, configured connection.

    Raises:
        sqlite3.Error: If the database file cannot be opened or created.
    """
    _ensure_dir(cfg.SQLITE_DB_PATH)
    conn = sqlite3.connect(cfg.SQLITE_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()
    log.debug("SQLite connection opened: %s", cfg.SQLITE_DB_PATH)
    return conn


def init_db() -> None:
    """
    Initialise the SQLite database by executing schema.sql.

    Safe to call repeatedly — all CREATE TABLE / CREATE INDEX statements use
    IF NOT EXISTS. Triggers are also guarded with IF NOT EXISTS.

    Raises:
        FileNotFoundError: If schema.sql cannot be found relative to this file.
        sqlite3.Error:     On any SQL execution error.
    """
    schema_path = Path(__file__).parent.parent / "schema.sql"
    if not schema_path.exists():
        raise FileNotFoundError(f"schema.sql not found at: {schema_path}")

    sql = schema_path.read_text(encoding="utf-8")

    # SQLite does not support PRAGMA inside executescript in all versions;
    # remove it from the script and execute it separately.
    script_lines = [
        line for line in sql.splitlines()
        if not line.strip().startswith("PRAGMA")
    ]
    script = "\n".join(script_lines)

    conn = get_connection()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(script)
        conn.commit()
        log.info("Database initialised: %s", cfg.SQLITE_DB_PATH)
    finally:
        conn.close()
