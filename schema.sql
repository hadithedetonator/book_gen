-- schema.sql (SQLite-compatible)
-- Run once to initialise the local database.
-- Usage:  sqlite3 data/books.db < schema.sql
-- Or via Python:  python main.py --init-db

-- Enable foreign key enforcement (must be set per-connection in Python too)
PRAGMA foreign_keys = ON;

-- ── books ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS books (
    id                      TEXT     PRIMARY KEY,   -- UUID stored as TEXT
    title                   TEXT     NOT NULL,
    notes_on_outline_before TEXT,
    outline                 TEXT,
    notes_on_outline_after  TEXT,
    final_review_notes      TEXT,
    status_outline_notes    TEXT     CHECK (
                                status_outline_notes IN ('yes', 'no', 'no_notes_needed')
                            ),
    status                  TEXT     NOT NULL DEFAULT 'pending',
    created_at              TEXT     NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at              TEXT     NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Trigger to auto-update updated_at on every UPDATE
CREATE TRIGGER IF NOT EXISTS books_updated_at
    AFTER UPDATE ON books
    FOR EACH ROW
    BEGIN
        UPDATE books SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        WHERE id = OLD.id;
    END;

-- ── chapters ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chapters (
    id                   TEXT     PRIMARY KEY,   -- UUID stored as TEXT
    book_id              TEXT     NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_number       INTEGER  NOT NULL,
    title                TEXT,
    content              TEXT,
    summary              TEXT,
    chapter_notes        TEXT,
    chapter_notes_status TEXT     CHECK (
                                chapter_notes_status IN ('yes', 'no', 'no_notes_needed')
                            ),
    status               TEXT     NOT NULL DEFAULT 'pending',
    created_at           TEXT     NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    UNIQUE (book_id, chapter_number)
);

CREATE INDEX IF NOT EXISTS idx_chapters_book_id_number
    ON chapters (book_id, chapter_number);

-- ── event_log ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS event_log (
    id         TEXT     PRIMARY KEY,   -- UUID stored as TEXT
    book_id    TEXT     REFERENCES books(id) ON DELETE SET NULL,
    event      TEXT     NOT NULL,
    detail     TEXT,
    created_at TEXT     NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_event_log_book_id ON event_log (book_id);
CREATE INDEX IF NOT EXISTS idx_event_log_event   ON event_log (event);
