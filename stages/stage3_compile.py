"""
stages/stage3_compile.py
-------------------------
Stage 3 — Final Compilation

Responsibilities:
  1. Gate: all chapters must be status='approved'. Abort otherwise.
  2. Optionally apply Ollama's brief editorial pass to each chapter's
     opening paragraph if the book has final_review_notes.
  3. Compile all chapters into .docx and .txt.
  4. Save both files locally to ./outputs/{book_id}/.
  5. Log "book_compiled" event and send final notification with file paths.

This module is independently runnable:
  python -m book_gen.stages.stage3_compile --book-id <uuid>
"""

import logging
import sqlite3
import sys

from book_gen.constants import (
    BookStatus, ChapterStatus, EventName
)
from book_gen.db import queries
from book_gen.llm import ollama_client as claude_client       # drop-in alias
from book_gen.llm.ollama_client import OllamaAPIError as ClaudeAPIError
from book_gen.notifications import email_notifier
from book_gen.utils.file_exporter import build_docx, build_txt, ExportError

log = logging.getLogger(__name__)


class Stage3Error(Exception):
    """Raised when Stage 3 encounters an unrecoverable or gate-blocked error."""


# ── Gate: all chapters approved ───────────────────────────────────────────────

def _gate_all_chapters_approved(
    db: sqlite3.Connection,
    book_id: str,
    title: str,
    chapters: list[dict],
) -> None:
    """
    Verify that every chapter has status='approved'. Abort if any do not.

    Args:
        db:       SQLite connection.
        book_id:  UUID of the book.
        title:    Book title.
        chapters: List of chapter rows from SQLite.

    Raises:
        Stage3Error: If one or more chapters are not approved.
    """
    unapproved = [
        ch["chapter_number"]
        for ch in chapters
        if ch.get("status") != ChapterStatus.APPROVED
    ]
    if unapproved:
        log.warning(
            "GATE FAIL — compilation_blocked: %d unapproved chapter(s) for book_id=%s: %s",
            len(unapproved), book_id, unapproved
        )
        queries.log_event(db, book_id, EventName.COMPILATION_BLOCKED,
                          f"unapproved chapters: {unapproved}")
        queries.update_book_status(db, book_id, BookStatus.ERROR)
        try:
            email_notifier.notify_compilation_blocked(title, book_id, unapproved)
        except Exception as email_exc:
            log.error("Failed to send compilation_blocked email: %s", email_exc)
        raise Stage3Error(
            f"Compilation blocked: chapters {unapproved} are not approved "
            f"for book_id={book_id}."
        )

    log.info("Gate passed: all %d chapters are approved.", len(chapters))


# ── Optional Editorial Pass ───────────────────────────────────────────────────

def _apply_editorial_pass(
    db: sqlite3.Connection,
    book_id: str,
    chapters: list[dict],
    final_review_notes: str,
) -> list[dict]:
    """
    Apply a brief editorial pass to each chapter's opening paragraph.

    Only the first paragraph of each chapter is revised; the rest is unchanged.
    The revised chapter is written back to SQLite before compiling.

    Args:
        db:                 SQLite connection.
        book_id:            UUID of the book.
        chapters:           List of approved chapter row dicts.
        final_review_notes: High-level editorial notes from the book reviewer.

    Returns:
        Updated list of chapter dicts with revised content.

    Raises:
        ClaudeAPIError: If any editorial pass call fails.
    """
    updated_chapters: list[dict] = []

    for ch in chapters:
        chapter_id     = ch["id"]
        chapter_number = ch["chapter_number"]
        chapter_title  = ch["title"] or ""
        content        = ch.get("content") or ""

        # Split into paragraphs; revise only the first non-empty one
        paragraphs = [p.strip() for p in content.split("\n\n")]
        non_empty  = [p for p in paragraphs if p]

        if not non_empty:
            log.warning("Chapter %d has no content to pass editorially.", chapter_number)
            updated_chapters.append(ch)
            continue

        intro = non_empty[0]
        rest  = "\n\n".join(non_empty[1:])

        log.info("Editorial pass on chapter %d intro paragraph.", chapter_number)
        try:
            revised_intro = claude_client.editorial_pass_intro(
                client=None,
                chapter_number=chapter_number,
                chapter_title=chapter_title,
                intro_paragraph=intro,
                final_review_notes=final_review_notes,
            )
        except ClaudeAPIError as exc:
            queries.log_event(db, book_id, EventName.CLAUDE_API_ERROR,
                              f"editorial_pass ch={chapter_number}: {exc}")
            raise

        new_content = revised_intro + ("\n\n" + rest if rest else "")
        queries.update_chapter_intro(db, chapter_id, new_content)

        updated_ch = dict(ch)
        updated_ch["content"] = new_content
        updated_chapters.append(updated_ch)

    return updated_chapters


# ── Main Stage Entry Point ────────────────────────────────────────────────────

def run(db: sqlite3.Connection, _llm, book_id: str) -> None:
    """
    Execute Stage 3 for a book that has completed Stage 2.

    Args:
        db:      SQLite connection.
        _llm:    Unused (kept for call-site compatibility with main.py).
        book_id: UUID of the book (must have all chapters approved).

    Raises:
        Stage3Error:    On gate failure or file-save error.
        ClaudeAPIError: On LLM error during editorial pass.
        RuntimeError:   If the book record is not found.
    """
    book = queries.get_book_by_id(db, book_id)
    if not book:
        raise RuntimeError(f"Stage 3: book not found for id={book_id}")

    title              = book["title"]
    final_review_notes = (book.get("final_review_notes") or "").strip()

    log.info("Stage 3 starting for book_id=%s title='%s'", book_id, title)
    queries.update_book_status(db, book_id, BookStatus.COMPILING)
    queries.log_event(db, book_id, EventName.COMPILATION_STARTED, "")

    # ── Gate ──────────────────────────────────────────────────────────────────
    chapters = queries.get_chapters_for_book(db, book_id)
    if not chapters:
        raise Stage3Error(
            f"Stage 3: no chapters found for book_id={book_id}. "
            "Run Stage 2 first."
        )

    _gate_all_chapters_approved(db, book_id, title, chapters)

    # ── Optional Editorial Pass ───────────────────────────────────────────────
    if final_review_notes:
        log.info("Final review notes present — applying editorial pass.")
        try:
            chapters = _apply_editorial_pass(db, book_id, chapters, final_review_notes)
        except ClaudeAPIError as exc:
            raise Stage3Error(
                f"LLM error during editorial pass for book_id={book_id}: {exc}"
            ) from exc
    else:
        log.info("No final review notes — compiling immediately.")

    # ── Build Output Files ────────────────────────────────────────────────────
    chapter_dicts = [
        {
            "chapter_number": ch["chapter_number"],
            "title":          ch.get("title") or f"Chapter {ch['chapter_number']}",
            "content":        ch.get("content") or "",
        }
        for ch in chapters
    ]

    log.info("Building .docx and .txt files.")
    try:
        docx_bytes = build_docx(title, chapter_dicts)
        txt_bytes  = build_txt(title, chapter_dicts)
    except ExportError as exc:
        raise Stage3Error(f"Failed to build output files: {exc}") from exc

    # ── Save Locally ──────────────────────────────────────────────────────────
    try:
        docx_path = queries.save_file_locally(book_id, "book.docx", docx_bytes)
        txt_path  = queries.save_file_locally(book_id, "book.txt",  txt_bytes)
    except OSError as exc:
        queries.log_event(db, book_id, EventName.CLAUDE_API_ERROR,
                          f"File save failed: {exc}")
        raise Stage3Error(f"File save failed for book_id={book_id}: {exc}") from exc

    # ── Finalise ──────────────────────────────────────────────────────────────
    queries.update_book_status(db, book_id, BookStatus.COMPLETE)
    queries.log_event(db, book_id, EventName.BOOK_COMPILED,
                      f"docx={docx_path} txt={txt_path}")

    try:
        email_notifier.notify_book_compiled(title, book_id, docx_path, txt_path)
    except Exception as email_exc:
        log.error("Failed to send book_compiled email: %s", email_exc)

    log.info(
        "Stage 3 complete for book_id=%s. Files: docx=%s txt=%s",
        book_id, docx_path, txt_path
    )


# ── Standalone CLI ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from book_gen.db.client import get_connection, init_db

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    parser = argparse.ArgumentParser(description="Run Stage 3 for a book.")
    parser.add_argument("--book-id", required=True, help="UUID of the book in the local DB.")
    cli_args = parser.parse_args()

    init_db()
    _db = get_connection()
    try:
        run(_db, None, cli_args.book_id)
        print("Stage 3 complete — book compiled and saved locally.")
    except Stage3Error as e:
        print(f"Stage 3 failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        _db.close()
