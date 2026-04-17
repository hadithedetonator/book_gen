"""
constants.py
------------
Single source of truth for all magic strings used across the system.
No other module may hardcode status values, event names, or model identifiers.
"""

# ── Book / Chapter Status Values ─────────────────────────────────────────────

class BookStatus:
    PENDING             = "pending"
    OUTLINE_GENERATED   = "outline_generated"
    OUTLINE_REVIEW      = "outline_review_pending"
    OUTLINE_APPROVED    = "outline_approved"
    CHAPTERS_IN_PROGRESS = "chapters_in_progress"
    CHAPTERS_COMPLETE   = "chapters_complete"
    COMPILING           = "compiling"
    COMPLETE            = "complete"
    ERROR               = "error"


class ChapterStatus:
    PENDING    = "pending"
    GENERATED  = "generated"
    REVIEW     = "review_pending"
    APPROVED   = "approved"
    ERROR      = "error"


# ── Outline / Chapter Notes Status Values ────────────────────────────────────

class NotesStatus:
    YES            = "yes"
    NO             = "no"
    NO_NOTES_NEEDED = "no_notes_needed"


# ── Event Names (written to event_log) ───────────────────────────────────────

class EventName:
    MISSING_NOTES_BEFORE       = "missing_notes_before"
    OUTLINE_GENERATED          = "outline_generated"
    OUTLINE_REVIEW_PENDING     = "outline_review_pending"
    OUTLINE_READY_FOR_REVIEW   = "outline_ready_for_review"
    OUTLINE_REGENERATED        = "outline_regenerated"
    OUTLINE_APPROVED           = "outline_approved"
    CHAPTER_GENERATED          = "chapter_generated"
    CHAPTER_REVIEW_PENDING     = "chapter_review_pending"
    CHAPTER_READY_FOR_REVIEW   = "chapter_ready_for_review"
    CHAPTER_REGENERATED        = "chapter_regenerated"
    CHAPTER_APPROVED           = "chapter_approved"
    COMPILATION_BLOCKED        = "compilation_blocked"
    COMPILATION_STARTED        = "compilation_started"
    BOOK_COMPILED              = "book_compiled"
    CLAUDE_API_ERROR           = "claude_api_error"
    GATE_FAILURE               = "gate_failure"


# ── Email Notification Subject Templates ─────────────────────────────────────

class EmailSubject:
    MISSING_NOTES_BEFORE     = "Action needed: outline notes missing for {title}"
    OUTLINE_READY_FOR_REVIEW = "Outline ready: please review {title}"
    CHAPTER_READY_FOR_REVIEW = "Chapter {chapter_number} ready for review: {title}"
    COMPILATION_BLOCKED      = "Compilation paused: unapproved chapters in {title}"
    BOOK_COMPILED            = "Book complete: {title} — download links inside"


# ── Claude Model Configuration ───────────────────────────────────────────────

class ClaudeModel:
    ID = "claude-3-5-sonnet-20241022"

    # max_tokens per call type
    MAX_TOKENS_OUTLINE  = 1024
    MAX_TOKENS_CHAPTER  = 2048
    MAX_TOKENS_SUMMARY  = 512

    # temperature per call type
    TEMP_CREATIVE = 0.7   # outlines, chapter writing
    TEMP_PRECISE  = 0.3   # summaries, parsing

    # Hard token limit — never exceed this for context passed to a single call
    MAX_CONTEXT_TOKENS = 6000


# ── Storage ───────────────────────────────────────────────────────────────────

class Storage:
    BUCKET_NAME = "book-outputs"


# ── Excel Sheet Name ──────────────────────────────────────────────────────────

EXCEL_SHEET_NAME = "Books"


# ── Excel Column Names ────────────────────────────────────────────────────────

class ExcelColumn:
    TITLE                   = "title"
    NOTES_ON_OUTLINE_BEFORE = "notes_on_outline_before"
    NOTES_ON_OUTLINE_AFTER  = "notes_on_outline_after"
    STATUS_OUTLINE_NOTES    = "status_outline_notes"


# ── Approximate token-per-character ratio used for budget estimation ──────────
# Conservative estimate: 1 token ≈ 4 characters
CHARS_PER_TOKEN = 4
