"""
stages/stage2_chapters.py
--------------------------
Stage 2 — Chapter Generation with Context Chaining

Responsibilities:
  1. Parse the approved outline into a deterministic list of chapters.
  2. For each chapter in order:
       a. Retrieve prior-chapter summaries from SQLite.
       b. Enforce token budget; summarise summaries if needed.
       c. Call Ollama to write the chapter.
       d. Call Ollama to produce a 3-sentence summary.
       e. Enforce chapter_notes_status gate before continuing.
  3. Never generate chapter N+1 before chapter N is approved.

Outline parsing contract:
  Lines that match the pattern:  ^\\d+[.):] ...  are treated as chapter entries.
  This is deterministic and does not rely on the LLM for re-parsing.

This module is independently runnable:
  python -m book_gen.stages.stage2_chapters --book-id <uuid>
"""

import logging
import re
import sqlite3
import sys

from book_gen.constants import (
    BookStatus, ChapterStatus, NotesStatus, EventName, ClaudeModel, CHARS_PER_TOKEN
)
from book_gen.db import queries
from book_gen.llm import ollama_client as claude_client       # drop-in alias
from book_gen.llm.ollama_client import OllamaAPIError as ClaudeAPIError
from book_gen.notifications import email_notifier

log = logging.getLogger(__name__)

# Regex: matches lines like "1. Introduction" / "2) Chapter Two" / "3: The Journey"
_CHAPTER_LINE_RE = re.compile(r"^\s*(\d+)[.):\s]\s*(.+)$")


class Stage2Error(Exception):
    """Raised when Stage 2 encounters an unrecoverable or gate-blocked error."""


# ── Outline Parser ────────────────────────────────────────────────────────────

def parse_outline_into_chapters(outline: str) -> list[dict]:
    """
    Parse a plain-text outline into an ordered list of chapter dicts.

    Matching lines are expected to follow the pattern:
      <number>[.):] <title text>

    Args:
        outline: The multi-line outline string stored in SQLite.

    Returns:
        List of dicts: [{"chapter_number": int, "title": str}, ...]
        Sorted ascending by chapter_number.

    Raises:
        Stage2Error: If no chapter lines are found in the outline.
    """
    chapters: list[dict] = []
    seen_numbers: set[int] = set()

    for line in outline.splitlines():
        match = _CHAPTER_LINE_RE.match(line)
        if not match:
            continue
        number = int(match.group(1))
        title  = match.group(2).strip()
        if number in seen_numbers:
            log.warning("Duplicate chapter number %d in outline — skipping duplicate.", number)
            continue
        seen_numbers.add(number)
        chapters.append({"chapter_number": number, "title": title})

    if not chapters:
        raise Stage2Error(
            "Outline parser found zero chapter lines. "
            "Ensure the outline uses the format: '1. Title', '2) Title', or '3: Title'."
        )

    chapters.sort(key=lambda c: c["chapter_number"])
    log.info("Parsed %d chapters from outline.", len(chapters))
    return chapters


# ── Context Builder ───────────────────────────────────────────────────────────

def _build_summaries_context(
    db: sqlite3.Connection,
    book_id: str,
    chapter_number: int,
) -> str:
    """
    Fetch prior-chapter summaries from SQLite and return a formatted context string.

    If the combined summaries would exceed MAX_CONTEXT_TOKENS, summarise them
    via a second Ollama call before returning.

    Args:
        db:             SQLite connection.
        book_id:        UUID of the parent book.
        chapter_number: The chapter currently being written (summaries for 1..N-1).

    Returns:
        A formatted summaries string (may be empty for chapter 1).

    Raises:
        ClaudeAPIError: If meta-summarisation is needed and fails.
    """
    if chapter_number == 1:
        return "(This is the first chapter — no prior summaries.)"

    rows = queries.get_previous_summaries(db, book_id, chapter_number)
    if not rows:
        return "(No prior chapter summaries available.)"

    parts: list[str] = []
    for row in rows:
        summary = (row.get("summary") or "").strip()
        if summary:
            parts.append(
                f"Chapter {row['chapter_number']} — {row['title']}:\n{summary}"
            )

    combined = "\n\n".join(parts)

    estimated_tokens = len(combined) // CHARS_PER_TOKEN
    if estimated_tokens > ClaudeModel.MAX_CONTEXT_TOKENS:
        log.warning(
            "Chapter summaries exceed token budget (%d est. tokens). "
            "Running meta-summarisation.", estimated_tokens
        )
        combined = claude_client.summarise_summaries(None, combined)
        log.info("Meta-summarisation complete.")

    return combined or "(No usable summaries.)"


# ── Chapter Gate ──────────────────────────────────────────────────────────────

def _gate_chapter_notes_status(
    db: sqlite3.Connection,
    book_id: str,
    title: str,
    outline: str,
    chapter_row: dict,
    summaries_context: str,
) -> None:
    """
    Evaluate chapter_notes_status and either approve the chapter or pause/regenerate.

    Possible paths:
      - no_notes_needed → update status to 'approved', return.
      - yes + notes present → regenerate chapter, update status to 'approved', return.
      - yes + notes missing → pause, notify, raise Stage2Error.
      - no / empty → pause, notify, raise Stage2Error.

    Args:
        db:               SQLite connection.
        book_id:          UUID of the parent book.
        title:            Book title.
        outline:          Full book outline text.
        chapter_row:      The full chapter row dict from SQLite.
        summaries_context: Pre-built context string of prior summaries.

    Raises:
        Stage2Error:    If the gate fails or the chapter must be paused.
        ClaudeAPIError: If chapter regeneration fails.
    """
    chapter_id     = chapter_row["id"]
    chapter_number = chapter_row["chapter_number"]
    chapter_title  = chapter_row["title"]
    notes_status   = (chapter_row.get("chapter_notes_status") or "").strip().lower()
    chapter_notes  = (chapter_row.get("chapter_notes") or "").strip()

    if notes_status == NotesStatus.NO_NOTES_NEEDED:
        log.info("Chapter %d approved (no_notes_needed).", chapter_number)
        queries.update_chapter_status(db, chapter_id, ChapterStatus.APPROVED)
        queries.log_event(db, book_id, EventName.CHAPTER_APPROVED,
                          f"chapter={chapter_number} no notes needed")
        return

    if notes_status == NotesStatus.YES:
        if not chapter_notes:
            log.warning(
                "chapter_notes_status='yes' but chapter_notes is empty. "
                "Pausing chapter %d.", chapter_number
            )
            queries.update_chapter_status(db, chapter_id, ChapterStatus.REVIEW)
            queries.log_event(db, book_id, EventName.CHAPTER_REVIEW_PENDING,
                              f"chapter={chapter_number} notes missing despite status=yes")
            try:
                email_notifier.notify_chapter_ready_for_review(title, book_id, chapter_number)
            except Exception as email_exc:
                log.error("Failed to send chapter review email: %s", email_exc)
            raise Stage2Error(
                f"Gate paused: chapter_notes_status='yes' but chapter_notes is empty "
                f"for chapter {chapter_number} (book_id={book_id})."
            )

        # Regenerate with notes
        log.info("Regenerating chapter %d with reviewer notes.", chapter_number)
        original_content = chapter_row.get("content") or ""
        try:
            revised_content = claude_client.rewrite_chapter_with_notes(
                client=None,
                title=title,
                outline=outline,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                original_content=original_content,
                chapter_notes=chapter_notes,
                chapter_summaries=summaries_context,
            )
            new_summary = claude_client.summarise_chapter(
                client=None,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                chapter_content=revised_content,
            )
        except ClaudeAPIError as exc:
            queries.log_event(db, book_id, EventName.CLAUDE_API_ERROR, str(exc))
            raise Stage2Error(
                f"LLM error during chapter {chapter_number} regeneration: {exc}"
            ) from exc

        queries.update_chapter_content(
            db, chapter_id, revised_content, new_summary, ChapterStatus.APPROVED
        )
        queries.log_event(db, book_id, EventName.CHAPTER_REGENERATED,
                          f"chapter={chapter_number}")
        queries.log_event(db, book_id, EventName.CHAPTER_APPROVED,
                          f"chapter={chapter_number} after regeneration")
        log.info("Chapter %d regenerated and approved.", chapter_number)
        return

    # status == 'no' or empty → pause
    log.warning(
        "GATE FAIL — chapter_review_pending: chapter=%d status='%s'",
        chapter_number, notes_status
    )
    queries.update_chapter_status(db, chapter_id, ChapterStatus.REVIEW)
    queries.log_event(db, book_id, EventName.CHAPTER_REVIEW_PENDING,
                      f"chapter={chapter_number} status='{notes_status}'")
    try:
        email_notifier.notify_chapter_ready_for_review(title, book_id, chapter_number)
    except Exception as email_exc:
        log.error("Failed to send chapter review email: %s", email_exc)
    raise Stage2Error(
        f"Gate paused: chapter_review_pending for chapter {chapter_number} "
        f"(book_id={book_id}). Update chapter_notes_status and re-run."
    )


# ── Single Chapter Processor ──────────────────────────────────────────────────

def _process_chapter(
    db: sqlite3.Connection,
    book_id: str,
    book_title: str,
    outline: str,
    chapter_meta: dict,
) -> None:
    """
    Write (or skip if already approved) one chapter and enforce its gate.

    Args:
        db:           SQLite connection.
        book_id:      UUID of the parent book.
        book_title:   Book title.
        outline:      Full outline text.
        chapter_meta: Dict with chapter_number and title from parse_outline_into_chapters.

    Raises:
        Stage2Error:    On gate failure.
        ClaudeAPIError: On LLM failure.
    """
    chapter_number = chapter_meta["chapter_number"]
    chapter_title  = chapter_meta["title"]

    # Check if an approved chapter row already exists
    existing = queries.get_chapter(db, book_id, chapter_number)
    if existing and existing.get("status") == ChapterStatus.APPROVED:
        log.info("Chapter %d already approved — skipping.", chapter_number)
        return

    # Insert if not present
    if not existing:
        existing = queries.insert_chapter(db, book_id, chapter_number, chapter_title)

    chapter_row = existing
    chapter_id  = chapter_row["id"]

    # Build summaries context (may trigger meta-summarisation)
    summaries_context = _build_summaries_context(db, book_id, chapter_number)

    # Write chapter via Ollama (only if not already generated)
    if chapter_row.get("status") not in (
        ChapterStatus.GENERATED, ChapterStatus.REVIEW, ChapterStatus.APPROVED
    ):
        log.info("Writing chapter %d via Ollama.", chapter_number)
        try:
            content = claude_client.write_chapter(
                client=None,
                title=book_title,
                outline=outline,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                chapter_summaries=summaries_context,
            )
            summary = claude_client.summarise_chapter(
                client=None,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                chapter_content=content,
            )
        except ClaudeAPIError as exc:
            queries.log_event(db, book_id, EventName.CLAUDE_API_ERROR, str(exc))
            queries.update_chapter_status(db, chapter_id, ChapterStatus.ERROR)
            raise Stage2Error(
                f"LLM error while writing chapter {chapter_number}: {exc}"
            ) from exc

        queries.update_chapter_content(db, chapter_id, content, summary, ChapterStatus.GENERATED)
        queries.log_event(db, book_id, EventName.CHAPTER_GENERATED,
                          f"chapter={chapter_number}")

        # Refresh row for gate evaluation
        chapter_row = queries.get_chapter(db, book_id, chapter_number)

    # ── Chapter Gate ──────────────────────────────────────────────────────────
    _gate_chapter_notes_status(
        db=db,
        book_id=book_id,
        title=book_title,
        outline=outline,
        chapter_row=chapter_row,
        summaries_context=summaries_context,
    )

    queries.log_event(db, book_id, EventName.CHAPTER_READY_FOR_REVIEW,
                      f"chapter={chapter_number} ready")


# ── Main Stage Entry Point ────────────────────────────────────────────────────

def run(db: sqlite3.Connection, _llm, book_id: str) -> None:
    """
    Execute Stage 2 for a book that has completed Stage 1.

    Processes chapters sequentially; halts at the first gate failure so that
    chapter N+1 is never started before chapter N is approved.

    Args:
        db:      SQLite connection.
        _llm:    Unused (kept for call-site compatibility with main.py).
        book_id: UUID of the book (must have status='outline_approved').

    Raises:
        Stage2Error: On gate failure or LLM error (after logging).
        RuntimeError: If the book record is not found.
    """
    book = queries.get_book_by_id(db, book_id)
    if not book:
        raise RuntimeError(f"Stage 2: book not found for id={book_id}")

    title   = book["title"]
    outline = book.get("outline") or ""

    if not outline:
        raise Stage2Error(
            f"Stage 2: no outline stored for book_id={book_id}. Run Stage 1 first."
        )

    log.info("Stage 2 starting for book_id=%s title='%s'", book_id, title)
    queries.update_book_status(db, book_id, BookStatus.CHAPTERS_IN_PROGRESS)

    chapters_meta = parse_outline_into_chapters(outline)

    for chapter_meta in chapters_meta:
        log.info(
            "Processing chapter %d: '%s'",
            chapter_meta["chapter_number"], chapter_meta["title"]
        )
        _process_chapter(db, book_id, title, outline, chapter_meta)

    # All chapters approved
    queries.update_book_status(db, book_id, BookStatus.CHAPTERS_COMPLETE)
    log.info("Stage 2 complete — all chapters approved for book_id=%s.", book_id)


# ── Standalone CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from book_gen.db.client import get_connection, init_db

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Run Stage 2 for a book.")
    parser.add_argument("--book-id", required=True, help="UUID of the book in the local DB.")
    cli_args = parser.parse_args()

    init_db()
    _db = get_connection()
    try:
        run(_db, None, cli_args.book_id)
        print("Stage 2 complete.")
    except Stage2Error as e:
        print(f"Stage 2 paused/failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _db.close()
