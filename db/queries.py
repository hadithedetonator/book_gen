"""
db/queries.py
-------------
All database read/write functions for the book generation system.
Uses Python's built-in sqlite3 only — no ORM, no Supabase.

All queries use parameterised placeholders (?) to prevent SQL injection.
Every function opens a fresh connection, performs its work, commits, and closes.
This keeps the module stateless and thread-safe for single-process use.

Row dicts are produced by sqlite3.Row (set in get_connection()) and converted
to plain dicts where the caller needs .get() behaviour.
"""

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from book_gen.constants import BookStatus, ChapterStatus
from book_gen.db.client import get_connection

log = logging.getLogger(__name__)


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    """Generate a new UUID4 string for use as a primary key."""
    return str(uuid.uuid4())


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[dict]:
    """
    Convert a sqlite3.Row to a plain dict, or return None if row is None.

    Args:
        row: sqlite3.Row or None.

    Returns:
        dict or None.
    """
    return dict(row) if row is not None else None


def _rows_to_dicts(rows: list) -> list[dict]:
    """
    Convert a list of sqlite3.Row objects to plain dicts.

    Args:
        rows: List of sqlite3.Row instances.

    Returns:
        List of dicts.
    """
    return [dict(r) for r in rows]


# ── Books ─────────────────────────────────────────────────────────────────────

def get_book_by_id(conn: sqlite3.Connection, book_id: str) -> Optional[dict]:
    """
    Fetch a single book record by its UUID.

    Args:
        conn:    SQLite connection.
        book_id: UUID of the books row.

    Returns:
        A dict of the book row, or None if not found.

    Raises:
        sqlite3.Error: On query failure.
    """
    cur = conn.execute(
        "SELECT * FROM books WHERE id = ?",
        (book_id,)
    )
    return _row_to_dict(cur.fetchone())


def get_or_create_book(conn: sqlite3.Connection, title: str, notes_before: str) -> dict:
    """
    Fetch a book by title, or insert it if it doesn't exist.

    Args:
        conn:         SQLite connection.
        title:        Book title.
        notes_before: Editorial notes (only used if creating new).

    Returns:
        The book row as a dict.
    """
    # Check for existing book by title
    cur = conn.execute("SELECT * FROM books WHERE title = ?", (title,))
    existing = cur.fetchone()
    if existing:
        log.info("Using existing book: title='%s'", title)
        return _row_to_dict(existing)

    # Otherwise insert new
    book_id  = _new_id()
    now      = _now_iso()
    conn.execute(
        """
        INSERT INTO books (id, title, notes_on_outline_before, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (book_id, title, notes_before, BookStatus.PENDING, now, now),
    )
    conn.commit()
    log.info("Inserted new book: id=%s title='%s'", book_id, title)
    return get_book_by_id(conn, book_id)


def update_book_outline(
    conn: sqlite3.Connection, book_id: str, outline: str, status: str
) -> None:
    """
    Store the generated outline and update the book's status field.

    Args:
        conn:    SQLite connection.
        book_id: UUID of the books row.
        outline: The generated outline text.
        status:  New status value.

    Raises:
        sqlite3.Error: On update failure.
    """
    conn.execute(
        "UPDATE books SET outline = ?, status = ?, updated_at = ? WHERE id = ?",
        (outline, status, _now_iso(), book_id),
    )
    conn.commit()
    log.info("Updated outline for book_id=%s status=%s", book_id, status)


def update_book_status(conn: sqlite3.Connection, book_id: str, status: str) -> None:
    """
    Update only the status field of a book row.

    Args:
        conn:    SQLite connection.
        book_id: UUID of the books row.
        status:  New status string.

    Raises:
        sqlite3.Error: On update failure.
    """
    conn.execute(
        "UPDATE books SET status = ?, updated_at = ? WHERE id = ?",
        (status, _now_iso(), book_id),
    )
    conn.commit()
    log.info("Book status updated: book_id=%s → %s", book_id, status)


def update_book_notes_after(
    conn: sqlite3.Connection,
    book_id: str,
    notes_after: str,
    status_outline_notes: str,
) -> None:
    """
    Persist notes_on_outline_after and status_outline_notes on the book row.

    Args:
        conn:                 SQLite connection.
        book_id:              UUID of the books row.
        notes_after:          Reviewer's post-outline notes.
        status_outline_notes: New notes status value.

    Raises:
        sqlite3.Error: On update failure.
    """
    conn.execute(
        """
        UPDATE books
        SET notes_on_outline_after = ?, status_outline_notes = ?, updated_at = ?
        WHERE id = ?
        """,
        (notes_after, status_outline_notes, _now_iso(), book_id),
    )
    conn.commit()
    log.info("Updated notes_after for book_id=%s", book_id)


# ── Chapters ──────────────────────────────────────────────────────────────────

def get_chapters_for_book(conn: sqlite3.Connection, book_id: str) -> list[dict]:
    """
    Fetch all chapter rows for a book, ordered by chapter_number ascending.

    Args:
        conn:    SQLite connection.
        book_id: UUID of the parent book.

    Returns:
        List of chapter row dicts.

    Raises:
        sqlite3.Error: On query failure.
    """
    cur = conn.execute(
        "SELECT * FROM chapters WHERE book_id = ? ORDER BY chapter_number ASC",
        (book_id,)
    )
    return _rows_to_dicts(cur.fetchall())


def get_chapter(
    conn: sqlite3.Connection, book_id: str, chapter_number: int
) -> Optional[dict]:
    """
    Fetch a single chapter row by book_id and chapter_number.

    Args:
        conn:           SQLite connection.
        book_id:        UUID of the parent book.
        chapter_number: The 1-based chapter ordinal.

    Returns:
        A dict of the chapter row, or None if not found.

    Raises:
        sqlite3.Error: On query failure.
    """
    cur = conn.execute(
        "SELECT * FROM chapters WHERE book_id = ? AND chapter_number = ?",
        (book_id, chapter_number)
    )
    return _row_to_dict(cur.fetchone())


def get_previous_summaries(
    conn: sqlite3.Connection, book_id: str, before_chapter: int
) -> list[dict]:
    """
    Fetch summaries of all chapters whose number is strictly less than *before_chapter*.

    Args:
        conn:           SQLite connection.
        book_id:        UUID of the parent book.
        before_chapter: Fetch summaries for chapters 1 … before_chapter-1.

    Returns:
        List of dicts with keys: chapter_number, title, summary.

    Raises:
        sqlite3.Error: On query failure.
    """
    cur = conn.execute(
        """
        SELECT chapter_number, title, summary
        FROM chapters
        WHERE book_id = ? AND chapter_number < ?
        ORDER BY chapter_number ASC
        """,
        (book_id, before_chapter)
    )
    return _rows_to_dicts(cur.fetchall())


def insert_chapter(
    conn: sqlite3.Connection,
    book_id: str,
    chapter_number: int,
    title: str,
) -> dict:
    """
    Insert a chapter row with status='pending' and return the new row.

    Args:
        conn:           SQLite connection.
        book_id:        UUID of the parent book.
        chapter_number: 1-based ordinal.
        title:          Chapter title.

    Returns:
        The inserted chapter row dict.

    Raises:
        sqlite3.Error: On insertion failure.
    """
    chapter_id = _new_id()
    now        = _now_iso()
    conn.execute(
        """
        INSERT INTO chapters (id, book_id, chapter_number, title, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (chapter_id, book_id, chapter_number, title, ChapterStatus.PENDING, now),
    )
    conn.commit()
    log.info("Inserted chapter %d for book_id=%s", chapter_number, book_id)
    return get_chapter(conn, book_id, chapter_number)


def update_chapter_content(
    conn: sqlite3.Connection,
    chapter_id: str,
    content: str,
    summary: str,
    status: str,
) -> None:
    """
    Persist generated content and its summary on a chapter row.

    Args:
        conn:       SQLite connection.
        chapter_id: UUID of the chapters row.
        content:    Full chapter prose.
        summary:    3-sentence summary.
        status:     New status.

    Raises:
        sqlite3.Error: On update failure.
    """
    conn.execute(
        "UPDATE chapters SET content = ?, summary = ?, status = ? WHERE id = ?",
        (content, summary, status, chapter_id),
    )
    conn.commit()
    log.info("Chapter content saved: chapter_id=%s status=%s", chapter_id, status)


def update_chapter_status(
    conn: sqlite3.Connection, chapter_id: str, status: str
) -> None:
    """
    Update only the status field of a chapter row.

    Args:
        conn:       SQLite connection.
        chapter_id: UUID of the chapters row.
        status:     New status string.

    Raises:
        sqlite3.Error: On update failure.
    """
    conn.execute(
        "UPDATE chapters SET status = ? WHERE id = ?",
        (status, chapter_id),
    )
    conn.commit()
    log.info("Chapter status updated: chapter_id=%s → %s", chapter_id, status)


def update_chapter_intro(
    conn: sqlite3.Connection, chapter_id: str, new_content: str
) -> None:
    """
    Replace the chapter's stored content with an editorially revised version.

    Args:
        conn:        SQLite connection.
        chapter_id:  UUID of the chapters row.
        new_content: Full revised chapter text.

    Raises:
        sqlite3.Error: On update failure.
    """
    conn.execute(
        "UPDATE chapters SET content = ? WHERE id = ?",
        (new_content, chapter_id),
    )
    conn.commit()
    log.info("Chapter intro updated: chapter_id=%s", chapter_id)


# ── Event Log ─────────────────────────────────────────────────────────────────

def log_event(
    conn: sqlite3.Connection, book_id: str, event: str, detail: str = ""
) -> None:
    """
    Append an entry to the event_log table.

    Args:
        conn:    SQLite connection.
        book_id: UUID of the associated book (or empty string for global events).
        event:   Event name constant from EventName.
        detail:  Optional free-text detail.

    Raises:
        sqlite3.Error: On insert failure.
    """
    event_id  = _new_id()
    now       = _now_iso()
    # Allow NULL book_id for global events
    book_id_val = book_id if book_id else None
    conn.execute(
        "INSERT INTO event_log (id, book_id, event, detail, created_at) VALUES (?,?,?,?,?)",
        (event_id, book_id_val, event, detail, now),
    )
    conn.commit()
    log.debug("Event logged: book_id=%s event=%s", book_id, event)


# ── Local Storage (replaces Supabase Storage) ─────────────────────────────────

def save_file_locally(
    book_id: str,
    filename: str,
    data: bytes,
) -> str:
    """
    Save *data* to ./outputs/{book_id}/{filename} and return the local path.

    Directories are created if they do not exist.

    Args:
        book_id:  UUID of the book (used as subdirectory name).
        filename: Output filename (e.g. 'book.docx').
        data:     File contents as bytes.

    Returns:
        Absolute path string of the saved file.

    Raises:
        OSError: If the file cannot be written.
    """
    import os
    from pathlib import Path
    import book_gen.config as cfg

    output_dir = Path(cfg.OUTPUT_DIR) / book_id
    output_dir.mkdir(parents=True, exist_ok=True)

    file_path = output_dir / filename
    file_path.write_bytes(data)
    abs_path = str(file_path.resolve())
    log.info("File saved locally: %s", abs_path)
    return abs_path
