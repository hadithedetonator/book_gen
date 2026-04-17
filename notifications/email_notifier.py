"""
notifications/email_notifier.py
--------------------------------
SMTP email notification system using Python's built-in smtplib + email.mime.
No third-party mailer libraries are used.

All five notification events are handled here with individual helper functions.
Every email includes: book title, event name, timestamp, and a detail message.
STARTTLS is used for secure transport.
"""

import logging
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from book_gen import config
from book_gen.constants import EmailSubject

log = logging.getLogger(__name__)


# ── Core Transport ────────────────────────────────────────────────────────────

def _send_email(subject: str, body: str) -> None:
    """
    Compose and send a plain-text email via SMTP with STARTTLS.

    Args:
        subject: Email subject line.
        body:    Plain-text email body.

    Raises:
        smtplib.SMTPException: If the SMTP connection or send operation fails.
        Exception:             On any other transport error.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = config.SMTP_USER
    msg["To"]      = config.NOTIFY_EMAIL

    msg.attach(MIMEText(body, "plain"))

    try:
        log.info("Sending email: '%s' → %s", subject, config.NOTIFY_EMAIL)
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(config.SMTP_USER, config.SMTP_PASSWORD)
            server.sendmail(config.SMTP_USER, config.NOTIFY_EMAIL, msg.as_string())
        log.info("Email sent successfully.")
    except smtplib.SMTPException as exc:
        log.error("SMTP error while sending '%s': %s", subject, exc)
        raise
    except Exception as exc:
        log.error("Unexpected error while sending email '%s': %s", subject, exc)
        raise


def _timestamp() -> str:
    """Return a UTC ISO-format timestamp string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _base_body(title: str, event: str, detail: str) -> str:
    """
    Build the standard email body shared by all event types.

    Args:
        title:  Book title.
        event:  Internal event name (from EventName constants).
        detail: Human-readable description of what happened and what action is needed.

    Returns:
        Formatted plain-text email body string.
    """
    return (
        f"Book Title : {title}\n"
        f"Event      : {event}\n"
        f"Timestamp  : {_timestamp()}\n"
        f"\n"
        f"{detail}\n"
        f"\n"
        f"— Automated Book Generation System"
    )


# ── Event-Specific Senders ────────────────────────────────────────────────────

def notify_missing_notes_before(title: str, book_id: str) -> None:
    """
    Notify that a book row cannot proceed because notes_on_outline_before is empty.

    Args:
        title:   Book title from Excel.
        book_id: UUID of the books row (may be placeholder if not yet inserted).

    Raises:
        smtplib.SMTPException: On transport failure.
    """
    subject = EmailSubject.MISSING_NOTES_BEFORE.format(title=title)
    detail = (
        f"The book '{title}' (ID: {book_id}) cannot have its outline generated\n"
        "because the 'notes_on_outline_before' field is empty in the Excel file.\n\n"
        "Action required:\n"
        "  1. Open the Excel file.\n"
        "  2. Fill in the 'notes_on_outline_before' column for this book.\n"
        "  3. Re-run the pipeline for this book."
    )
    _send_email(subject, _base_body(title, "missing_notes_before", detail))


def notify_outline_ready_for_review(title: str, book_id: str) -> None:
    """
    Notify that an outline has been generated and requires human review.

    Args:
        title:   Book title.
        book_id: Supabase UUID of the book record.

    Raises:
        smtplib.SMTPException: On transport failure.
    """
    subject = EmailSubject.OUTLINE_READY_FOR_REVIEW.format(title=title)
    detail = (
        f"An outline for '{title}' (ID: {book_id}) has been generated and stored\n"
        "in the local database. It is now waiting for your review.\n\n"
        "Action required:\n"
        "  1. Open the local SQLite database (./data/books.db).\n"
        "  2. Locate the row in the 'books' table for this book.\n"
        "  3. Set 'status_outline_notes' to one of:\n"
        "       'yes'             — you have added notes in 'notes_on_outline_after'\n"
        "       'no_notes_needed' — the outline is approved as-is\n"
        "       'no'              — you are still reviewing (pipeline will remain paused)\n"
        "  4. Re-run the pipeline for this book once your decision is recorded."
    )
    _send_email(subject, _base_body(title, "outline_ready_for_review", detail))


def notify_chapter_ready_for_review(title: str, book_id: str, chapter_number: int) -> None:
    """
    Notify that a chapter has been generated and requires human review.

    Args:
        title:          Book title.
        book_id:        Supabase UUID of the book record.
        chapter_number: The chapter number (1-based) awaiting review.

    Raises:
        smtplib.SMTPException: On transport failure.
    """
    subject = EmailSubject.CHAPTER_READY_FOR_REVIEW.format(
        chapter_number=chapter_number, title=title
    )
    detail = (
        f"Chapter {chapter_number} of '{title}' (Book ID: {book_id}) has been written\n"
        "and is waiting for your review.\n\n"
        "Action required:\n"
        "  1. Open the local SQLite database (./data/books.db) → 'chapters' table.\n"
        f"  2. Find the row where book_id = {book_id} and chapter_number = {chapter_number}.\n"
        "  3. Review the content and set 'chapter_notes_status' to:\n"
        "       'yes'             — add revision notes in 'chapter_notes', then re-run\n"
        "       'no_notes_needed' — chapter is approved, pipeline continues automatically\n"
        "       'no'              — still reviewing (pipeline stays paused)\n"
        "  4. Re-run: python main.py --book-id <uuid> --stage 2"
    ).format(book_id=book_id, chapter_number=chapter_number)
    _send_email(subject, _base_body(title, "chapter_ready_for_review", detail))


def notify_compilation_blocked(title: str, book_id: str, unapproved: list[int]) -> None:
    """
    Notify that final compilation cannot start due to unapproved chapters.

    Args:
        title:      Book title.
        book_id:    Supabase UUID of the book record.
        unapproved: List of chapter numbers that are not yet approved.

    Raises:
        smtplib.SMTPException: On transport failure.
    """
    subject = EmailSubject.COMPILATION_BLOCKED.format(title=title)
    chapter_list = ", ".join(str(n) for n in unapproved)
    detail = (
        f"Final compilation of '{title}' (ID: {book_id}) has been blocked.\n\n"
        f"The following chapters do not yet have status='approved': {chapter_list}\n\n"
        "Action required:\n"
        "  1. Review and approve all listed chapters in the 'chapters' table.\n"
        "  2. Re-run Stage 3 once all chapters are approved."
    )
    _send_email(subject, _base_body(title, "compilation_blocked", detail))


def notify_book_compiled(title: str, book_id: str, docx_url: str, txt_url: str) -> None:
    """
    Notify that the book has been compiled and files are available for download.

    Args:
        title:    Book title.
        book_id:  Supabase UUID of the book record.
        docx_url: Public or signed URL to the .docx file in Supabase Storage.
        txt_url:  Public or signed URL to the .txt file in Supabase Storage.

    Raises:
        smtplib.SMTPException: On transport failure.
    """
    subject = EmailSubject.BOOK_COMPILED.format(title=title)
    detail = (
        f"'{title}' (ID: {book_id}) has been successfully compiled.\n\n"
        f"Output files (local paths):\n"
        f"  .docx : {docx_url}\n"
        f"  .txt  : {txt_url}\n\n"
        "Both files are saved in the ./outputs/{book_id}/ directory."
    )
    _send_email(subject, _base_body(title, "book_compiled", detail))
