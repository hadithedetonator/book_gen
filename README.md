# Automated Book Generation System

## Overview

The Automated Book Generation System is a modular, human-in-the-loop pipeline that accepts book metadata from an Excel spreadsheet, generates structured outlines and chapter-by-chapter prose using a **local Ollama LLM** (fully offline), stores all state in a **local SQLite database**, and compiles approved content into `.docx` and `.txt` output files saved to the local filesystem. Every stage is strictly gated: the pipeline never advances unless all required fields are set and all review approvals are recorded in the database. Gate failures trigger Gmail SMTP email notifications so that human reviewers know exactly what action is required. **No cloud services are required** (except email delivery).

---

## Architecture Diagram

```
┌──────────────────────────────────────────────────────────┐
│                        EXCEL (.xlsx)                      │
│               Sheet: "Books" — one row per book          │
└────────────────────────┬─────────────────────────────────┘
                         │ read_books()
                         ▼
┌──────────────────────────────────────────────────────────┐
│  STAGE 1 — Input + Outline                               │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Gate 1: notes_on_outline_before non-empty?      │    │
│  │   FAIL → log event → email → skip book          │    │
│  │   PASS → call Ollama → store outline in SQLite  │    │
│  │                                                 │    │
│  │ Gate 2: status_outline_notes?                   │    │
│  │   "no_notes_needed" → approve                   │    │
│  │   "yes" + notes     → Ollama regenerate         │    │
│  │   "no" / empty      → pause → email             │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                                │
│              SQLite: books table (./data/books.db)       │
└────────────────────────┬─────────────────────────────────┘
                         │ book.status = outline_approved
                         ▼
┌──────────────────────────────────────────────────────────┐
│  STAGE 2 — Chapter Generation                            │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Parse outline (deterministic regex parser)       │    │
│  │ For each chapter N (in order):                  │    │
│  │   - Fetch summaries 1..N-1 from SQLite          │    │
│  │   - Enforce 6000-token budget                   │    │
│  │   - Ollama: write chapter                       │    │
│  │   - Ollama: summarise chapter (3 sentences)     │    │
│  │                                                 │    │
│  │   Gate: chapter_notes_status?                   │    │
│  │     "no_notes_needed" → approve, next chapter   │    │
│  │     "yes" + notes     → Ollama regenerate       │    │
│  │     "no" / empty      → pause → email           │    │
│  │   *** N+1 NEVER starts before N is approved *** │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                                │
│              SQLite: chapters table                      │
└────────────────────────┬─────────────────────────────────┘
                         │ all chapters.status = approved
                         ▼
┌──────────────────────────────────────────────────────────┐
│  STAGE 3 — Final Compilation                             │
│  ┌─────────────────────────────────────────────────┐    │
│  │ Gate: ALL chapters status = 'approved'?         │    │
│  │   FAIL → log → email → abort                    │    │
│  │                                                 │    │
│  │ final_review_notes present?                     │    │
│  │   YES → Ollama: editorial pass on intros        │    │
│  │   NO  → compile immediately                     │    │
│  │                                                 │    │
│  │ Build .docx (title page + TOC + chapters)       │    │
│  │ Build .txt  (plain concatenation)               │    │
│  │ Save → ./outputs/{book_id}/book.docx            │    │
│  │ Save → ./outputs/{book_id}/book.txt             │    │
│  │ Log event → send email with local file paths    │    │
│  └─────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

---

## Setup Instructions

```bash
# 1. Prerequisites
#    - Python 3.10+
#    - Ollama installed: https://ollama.com
#    - Pull the model:
ollama pull llama3

# 2. Clone the repository
git clone <your-repo-url>
cd book_gen

# 3. Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux

# 4. Install pinned dependencies
pip install -r requirements.txt

# 5. Configure environment variables
cp .env.example .env
# Edit .env — fill in SMTP_EMAIL, SMTP_PASSWORD, NOTIFY_EMAIL
# (OLLAMA_MODEL, SQLITE_DB_PATH, OUTPUT_DIR have sensible defaults)

# 6. Initialise the local SQLite database
python main.py --init-db

# 7. Run the pipeline
python main.py --file books.xlsx
```

---

## Excel Input Format

The workbook must contain a sheet named exactly **`Books`**. The first row is the header row. Column order does not matter — columns are resolved by name.

| Column Name | Required | Description | Valid Values |
|---|---|---|---|
| `title` | ✅ Yes | Book title | Any non-empty string |
| `notes_on_outline_before` | ✅ Yes | Editorial guidance for the AI before outline generation | Any non-empty string |
| `notes_on_outline_after` | ⬜ Optional | Reviewer's revision notes after reviewing the generated outline | Any string |
| `status_outline_notes` | ⬜ Optional | Human review decision on the outline | `yes` · `no` · `no_notes_needed` |

**Status value meanings:**
- `no_notes_needed` — Outline is approved as-is; pipeline proceeds automatically to Stage 2.
- `yes` — Reviewer has added notes in `notes_on_outline_after`; Ollama will regenerate the outline.
- `no` (or empty) — Reviewer has not yet made a decision; pipeline pauses and sends a notification.

---

## How to Run

```bash
# Initialise database (run once, safe to re-run)
python main.py --init-db

# Stage 1: Process all books from an Excel file
python main.py --file books.xlsx

# Stage 1: Resume outline gate for an existing book (e.g. after reviewer update)
python main.py --book-id <uuid> --stage 1

# Stage 2: Generate chapters for an approved book
python main.py --book-id <uuid> --stage 2

# Stage 3: Compile and save a fully approved book
python main.py --book-id <uuid> --stage 3

# Stages 2 + 3 in sequence for an existing book
python main.py --book-id <uuid> --stage all
```

---

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `OLLAMA_MODEL` | ⬜ Optional | `llama3` | Name of the locally pulled Ollama model |
| `OLLAMA_BASE_URL` | ⬜ Optional | `http://localhost:11434` | Ollama HTTP server base URL |
| `SQLITE_DB_PATH` | ⬜ Optional | `./data/books.db` | Path to local SQLite database file |
| `OUTPUT_DIR` | ⬜ Optional | `./outputs` | Directory for compiled .docx and .txt files |
| `SMTP_EMAIL` | ✅ Required | — | Gmail sender address |
| `SMTP_PASSWORD` | ✅ Required | — | Gmail App Password (16-char, not your real password) |
| `NOTIFY_EMAIL` | ✅ Required | — | Recipient for all pipeline notifications |
| `LOG_LEVEL` | ⬜ Optional | `INFO` | Python logging level |

---

## Output Files

| Format | Location | Notes |
|---|---|---|
| `.docx` | `./outputs/<book_id>/book.docx` | Title page + static TOC + chapters as Heading 1 |
| `.txt` | `./outputs/<book_id>/book.txt` | Plain-text concatenation, no formatting |

The absolute path of both files is:
- Logged in the `event_log` table under event `book_compiled`.
- Emailed to `NOTIFY_EMAIL` in the final notification.

---

## Updating the Database After Review

The pipeline pauses at gate points and sends an email. To resume:

1. Open the SQLite database with any SQLite client:
   ```bash
   sqlite3 ./data/books.db
   ```
2. Update the review field. Example:
   ```sql
   -- Approve an outline with no changes needed:
   UPDATE books SET status_outline_notes = 'no_notes_needed' WHERE id = '<book_uuid>';

   -- Approve a chapter:
   UPDATE chapters
   SET chapter_notes_status = 'no_notes_needed'
   WHERE book_id = '<book_uuid>' AND chapter_number = 1;
   ```
3. Re-run the appropriate stage:
   ```bash
   python main.py --book-id <uuid> --stage 2
   ```

---

## Known Limitations

1. **Ollama output is non-deterministic.** Local models do not reliably follow structured output instructions. The system validates minimum word counts and numbered-chapter format, but edge cases may require manual inspection of generated content.

2. **Single-process execution.** The pipeline is synchronous and single-threaded. There is no job queue or parallel processing. Running two instances against the same `book_id` simultaneously will cause race conditions in SQLite.

3. **Token budget is estimated, not exact.** Context token counts are approximated at 4 characters per token. Actual tokenization varies by model and content type; estimates may be off by ±15%.

4. **No automatic retry on gate failures.** When a gate pauses the pipeline, the operator must manually update the database and re-run. No polling or scheduled retry mechanism is built in.

5. **Gmail SMTP only.** The email system is hardcoded to `smtp.gmail.com:587`. Switching providers requires editing `config.py` directly.
