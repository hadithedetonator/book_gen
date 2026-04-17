"""
stages/stage1_outline.py
-------------------------
Stage 1 — Input + Outline

Responsibilities:
  1. Accept a single book record dict (from Excel or direct call).
  2. Enforce the notes_before gate — halt and notify if missing.
  3. Generate an outline via Ollama (local LLM).
  4. Evaluate status_outline_notes and either:
       a. Proceed (no_notes_needed)
       b. Regenerate with reviewer notes (yes + notes_after present)
       c. Pause and notify (no / missing)

This module is independently runnable for testing:
  python -m book_gen.stages.stage1_outline --book-id <uuid>
"""

import logging
import sqlite3
import sys

from book_gen.constants import (
    BookStatus, NotesStatus, EventName, ExcelColumn
)
from book_gen.db import queries
from book_gen.llm import ollama_client as claude_client       # drop-in alias
from book_gen.llm.ollama_client import OllamaAPIError as ClaudeAPIError
from book_gen.notifications import email_notifier

log = logging.getLogger(__name__)


class Stage1Error(Exception):
    """Raised when Stage 1 encounters an unrecoverable error for this book."""


# ── Gate Checks ───────────────────────────────────────────────────────────────

def _gate_notes_before(
    db: sqlite3.Connection,
    book_record: dict,
    book_id: str,
) -> None:
    """
    Gate: notes_on_outline_before must be non-empty.

    Args:
        db:          SQLite connection.
        book_record: Dict from Excel (must contain 'title' and
                     'notes_on_outline_before').
        book_id:     UUID of the book row in SQLite.

    Raises:
        Stage1Error: If the gate fails (notes are empty).
    """
    notes_before = (book_record.get(ExcelColumn.NOTES_ON_OUTLINE_BEFORE) or "").strip()
    if not notes_before:
        title = book_record.get(ExcelColumn.TITLE, "Unknown")
        log.warning("GATE FAIL — missing_notes_before: title='%s' book_id=%s", title, book_id)
        queries.log_event(db, book_id, EventName.MISSING_NOTES_BEFORE,
                          f"title='{title}'")
        queries.update_book_status(db, book_id, BookStatus.ERROR)
        try:
            email_notifier.notify_missing_notes_before(title, book_id)
        except Exception as email_exc:
            log.error("Failed to send missing_notes_before email: %s", email_exc)
        raise Stage1Error(
            f"Gate failed: notes_on_outline_before is empty for '{title}' (id={book_id}). "
            "Notification sent. Skipping this book."
        )


def _gate_outline_notes_status(
    db: sqlite3.Connection,
    book_id: str,
    title: str,
    outline: str,
    status_outline_notes: str,
    notes_after: str,
) -> str:
    """
    Gate: evaluate status_outline_notes and return the final approved outline.

    Possible paths:
      - no_notes_needed → return outline as-is.
      - yes + notes_after present → regenerate outline, return revised.
      - yes + notes_after missing → pause, notify, raise Stage1Error.
      - no / empty → pause, notify, raise Stage1Error.

    Args:
        db:                   SQLite connection.
        book_id:              UUID of the book.
        title:                Book title.
        outline:              Currently stored outline text.
        status_outline_notes: Value of the status_outline_notes field.
        notes_after:          Value of the notes_on_outline_after field.

    Returns:
        The final (possibly revised) outline string.

    Raises:
        Stage1Error:    If the gate fails and the pipeline must pause.
        ClaudeAPIError: If outline regeneration fails.
    """
    status = (status_outline_notes or "").strip().lower()

    if status == NotesStatus.NO_NOTES_NEEDED:
        log.info("Outline approved with no notes needed. Proceeding to Stage 2.")
        queries.update_book_status(db, book_id, BookStatus.OUTLINE_APPROVED)
        queries.log_event(db, book_id, EventName.OUTLINE_APPROVED,
                          "status_outline_notes=no_notes_needed")
        return outline

    if status == NotesStatus.YES:
        notes_after_text = (notes_after or "").strip()
        if not notes_after_text:
            log.warning(
                "status_outline_notes='yes' but notes_on_outline_after is empty. "
                "Pausing book_id=%s.", book_id
            )
            queries.log_event(db, book_id, EventName.OUTLINE_REVIEW_PENDING,
                              "notes_after missing despite status=yes")
            queries.update_book_status(db, book_id, BookStatus.OUTLINE_REVIEW)
            try:
                email_notifier.notify_outline_ready_for_review(title, book_id)
            except Exception as email_exc:
                log.error("Failed to send outline review email: %s", email_exc)
            raise Stage1Error(
                f"Gate paused: status_outline_notes='yes' but notes_on_outline_after "
                f"is empty for book_id={book_id}. Add notes then re-run."
            )

        # Regenerate outline with reviewer notes
        log.info("Regenerating outline with reviewer notes for book_id=%s.", book_id)
        try:
            revised_outline = claude_client.regenerate_outline(
                client=None,
                title=title,
                original_outline=outline,
                notes_after=notes_after_text,
            )
        except ClaudeAPIError as exc:
            queries.log_event(db, book_id, EventName.CLAUDE_API_ERROR, str(exc))
            raise Stage1Error(f"LLM error during outline regeneration: {exc}") from exc

        queries.update_book_outline(db, book_id, revised_outline, BookStatus.OUTLINE_APPROVED)
        queries.log_event(db, book_id, EventName.OUTLINE_REGENERATED,
                          "outline revised with notes_on_outline_after")
        log.info("Outline regenerated and approved for book_id=%s.", book_id)
        return revised_outline

    # status == 'no' or empty → pause
    log.warning(
        "GATE FAIL — outline_review_pending: status='%s' book_id=%s", status, book_id
    )
    queries.log_event(db, book_id, EventName.OUTLINE_REVIEW_PENDING,
                      f"status_outline_notes='{status}'")
    queries.update_book_status(db, book_id, BookStatus.OUTLINE_REVIEW)
    try:
        email_notifier.notify_outline_ready_for_review(title, book_id)
    except Exception as email_exc:
        log.error("Failed to send outline review email: %s", email_exc)
    raise Stage1Error(
        f"Gate paused: outline_review_pending for book_id={book_id}. "
        "Set status_outline_notes and re-run."
    )


# ── Main Stage Entry Point ────────────────────────────────────────────────────

def run(
    db: sqlite3.Connection,
    _llm,                       # unused; kept for call-site compatibility
    book_record: dict,
) -> dict:
    """
    Execute Stage 1 for a single book record sourced from Excel.

    The function:
      1. Inserts the book into SQLite (if not already present).
      2. Enforces the notes_before gate.
      3. Generates the outline via Ollama.
      4. Evaluates the outline notes status gate.
      5. Returns the final book row dict on success.

    Args:
        db:          SQLite connection.
        _llm:        Unused (kept for API compatibility with main.py).
        book_record: Dict with keys from ExcelColumn constants.

    Returns:
        The SQLite book row dict after successful outline approval.

    Raises:
        Stage1Error:    On gate failure (book skipped / paused).
        ClaudeAPIError: On LLM failure (after logging and notifying).
    """
    title        = (book_record.get(ExcelColumn.TITLE) or "").strip()
    notes_before = (book_record.get(ExcelColumn.NOTES_ON_OUTLINE_BEFORE) or "").strip()
    notes_after  = (book_record.get(ExcelColumn.NOTES_ON_OUTLINE_AFTER) or "").strip()
    status_notes = (book_record.get(ExcelColumn.STATUS_OUTLINE_NOTES) or "").strip()

    log.info("Stage 1 starting for title='%s'", title)

    # Get existing book or insert new (notes_before may be empty; gate runs after)
    db_row  = queries.get_or_create_book(db, title, notes_before)
    book_id = db_row["id"]

    # ── Gate 1: notes_before must exist ──────────────────────────────────────
    _gate_notes_before(db, book_record, book_id)

    # ── Generate outline ──────────────────────────────────────────────────────
    log.info("Generating outline via Ollama for book_id=%s", book_id)
    try:
        outline = claude_client.generate_outline(
            client=None,
            title=title,
            notes_before=notes_before,
        )
    except ClaudeAPIError as exc:
        queries.log_event(db, book_id, EventName.CLAUDE_API_ERROR, str(exc))
        queries.update_book_status(db, book_id, BookStatus.ERROR)
        log.error("LLM error during outline generation: %s", exc)
        raise Stage1Error(f"LLM error for book_id={book_id}: {exc}") from exc

    queries.update_book_outline(db, book_id, outline, BookStatus.OUTLINE_GENERATED)
    queries.log_event(db, book_id, EventName.OUTLINE_GENERATED,
                      f"outline length={len(outline)}")

    # ── Gate 2: outline notes status ─────────────────────────────────────────
    _gate_outline_notes_status(
        db=db,
        book_id=book_id,
        title=title,
        outline=outline,
        status_outline_notes=status_notes,
        notes_after=notes_after,
    )

    final_row = queries.get_book_by_id(db, book_id)
    log.info("Stage 1 complete for book_id=%s title='%s'", book_id, title)
    return final_row


def run_for_existing_book(
    db: sqlite3.Connection,
    _llm,
    book_id: str,
) -> dict:
    """
    Re-run Stage 1 gate logic for an already-inserted book (e.g. after reviewer update).

    Useful when a reviewer has updated status_outline_notes in the SQLite DB
    and the pipeline is being resumed.

    Args:
        db:      SQLite connection.
        _llm:    Unused (kept for API compatibility).
        book_id: UUID of the existing books row.

    Returns:
        The updated book row dict.

    Raises:
        Stage1Error:  On gate failure.
        RuntimeError: If book not found.
    """
    row = queries.get_book_by_id(db, book_id)
    if not row:
        raise RuntimeError(f"No book found with id={book_id}")

    title        = row["title"]
    outline      = row.get("outline") or ""
    notes_after  = row.get("notes_on_outline_after") or ""
    status_notes = row.get("status_outline_notes") or ""

    log.info("Resuming Stage 1 for existing book_id=%s title='%s'", book_id, title)

    _gate_outline_notes_status(
        db=db,
        book_id=book_id,
        title=title,
        outline=outline,
        status_outline_notes=status_notes,
        notes_after=notes_after,
    )

    return queries.get_book_by_id(db, book_id)


# ── Standalone CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from book_gen.db.client import get_connection, init_db

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Run Stage 1 for an existing book.")
    parser.add_argument("--book-id", required=True, help="UUID of the book in the local DB.")
    cli_args = parser.parse_args()

    init_db()
    _db = get_connection()
    try:
        result = run_for_existing_book(_db, None, cli_args.book_id)
        print(f"Stage 1 complete: {result}")
    except Stage1Error as e:
        print(f"Stage 1 paused/failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _db.close()
