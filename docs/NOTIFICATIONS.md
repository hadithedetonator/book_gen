# Notifications

## Overview

All notifications are sent via Gmail SMTP using Python's built-in `smtplib` with STARTTLS on port 587. No third-party mailer libraries are used. Every email includes: book title, event name, UTC timestamp, and a detailed action message. The `NOTIFY_EMAIL` environment variable is the recipient for all event types.

---

## Trigger Events

| Event Constant | Trigger Condition | Stage |
|---|---|---|
| `missing_notes_before` | `notes_on_outline_before` is empty for a book | Stage 1 |
| `outline_ready_for_review` | Outline generated or review gate fails (`no`/empty status) | Stage 1 |
| `chapter_ready_for_review` | Chapter gate fails (`no`/empty status) | Stage 2 |
| `compilation_blocked` | One or more chapters not approved at Stage 3 start | Stage 3 |
| `book_compiled` | Book successfully compiled and saved locally | Stage 3 |

---

## Email Templates

### 1. `missing_notes_before`

**Subject:**
```
Action needed: outline notes missing for {title}
```

**Body:**
```
Book Title : {title}
Event      : missing_notes_before
Timestamp  : {YYYY-MM-DD HH:MM:SS UTC}

The book '{title}' (ID: {book_id}) cannot have its outline generated
because the 'notes_on_outline_before' field is empty in the Excel file.

Action required:
  1. Open the Excel file.
  2. Fill in the 'notes_on_outline_before' column for this book.
  3. Re-run the pipeline for this book.

— Automated Book Generation System
```

---

### 2. `outline_ready_for_review`

**Subject:**
```
Outline ready: please review {title}
```

**Body:**
```
Book Title : {title}
Event      : outline_ready_for_review
Timestamp  : {YYYY-MM-DD HH:MM:SS UTC}

An outline for '{title}' (ID: {book_id}) has been generated and stored
in the local database. It is now waiting for your review.

Action required:
  1. Open the local SQLite database (./data/books.db).
  2. Locate the row in the 'books' table for this book.
  3. Set 'status_outline_notes' to one of:
       'yes'             — you have added notes in 'notes_on_outline_after'
       'no_notes_needed' — the outline is approved as-is
       'no'              — you are still reviewing (pipeline will remain paused)
  4. Re-run the pipeline for this book once your decision is recorded.

— Automated Book Generation System
```

---

### 3. `chapter_ready_for_review`

**Subject:**
```
Chapter {N} ready for review: {title}
```

**Body:**
```
Book Title : {title}
Event      : chapter_ready_for_review
Timestamp  : {YYYY-MM-DD HH:MM:SS UTC}

Chapter {N} of '{title}' (Book ID: {book_id}) has been written
and is waiting for your review.

Action required:
  1. Open the local SQLite database (./data/books.db) → 'chapters' table.
  2. Find the row where book_id = {book_id} and chapter_number = {N}.
  3. Review the content and set 'chapter_notes_status' to:
       'yes'             — add revision notes in 'chapter_notes', then re-run
       'no_notes_needed' — chapter is approved, pipeline continues automatically
       'no'              — still reviewing (pipeline stays paused)
  4. Re-run: python main.py --book-id <uuid> --stage 2

— Automated Book Generation System
```

---

### 4. `compilation_blocked`

**Subject:**
```
Compilation paused: unapproved chapters in {title}
```

**Body:**
```
Book Title : {title}
Event      : compilation_blocked
Timestamp  : {YYYY-MM-DD HH:MM:SS UTC}

Final compilation of '{title}' (ID: {book_id}) has been blocked.

The following chapters do not yet have status='approved': {chapter_list}

Action required:
  1. Review and approve all listed chapters in the 'chapters' table.
  2. Re-run: python main.py --book-id <uuid> --stage 3

— Automated Book Generation System
```

---

### 5. `book_compiled`

**Subject:**
```
Book complete: {title} — download links inside
```

**Body:**
```
Book Title : {title}
Event      : book_compiled
Timestamp  : {YYYY-MM-DD HH:MM:SS UTC}

'{title}' (ID: {book_id}) has been successfully compiled.

Output files (local paths):
  .docx : /absolute/path/to/outputs/{book_id}/book.docx
  .txt  : /absolute/path/to/outputs/{book_id}/book.txt

Both files are saved in the ./outputs/{book_id}/ directory.

— Automated Book Generation System
```

---

## Gmail SMTP Setup

### Required Environment Variables

```env
SMTP_EMAIL=you@gmail.com
SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Gmail App Password — NOT your real password
NOTIFY_EMAIL=recipient@example.com
```

### Generating a Gmail App Password

1. Enable **2-Step Verification** on your Google account: [myaccount.google.com](https://myaccount.google.com).
2. Go to **Google Account → Security → App Passwords**.
3. Select app: **Mail**, device: **Other** → name it `book_gen`.
4. Copy the 16-character password into `SMTP_PASSWORD`.

### SMTP Connection Details (hardcoded in config.py)

| Setting | Value |
|---|---|
| Host | `smtp.gmail.com` |
| Port | `587` |
| Security | STARTTLS (`server.starttls()`) |
| Auth | Login with App Password |
| Timeout | 30 seconds |

> **Important:** The system uses `smtplib.SMTP` + `starttls()`. Do **not** use port 465 — that requires `smtplib.SMTP_SSL` which is not used here.

### Testing Email

```bash
python - <<'EOF'
import smtplib
import os
from dotenv import load_dotenv
load_dotenv()
with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
    s.ehlo(); s.starttls(); s.ehlo()
    s.login(os.environ["SMTP_EMAIL"], os.environ["SMTP_PASSWORD"])
    print("SMTP login OK")
EOF
```
