# Architecture

## Module Dependency Graph

```
main.py
├── config.py                  (env vars — Ollama, SQLite, SMTP)
├── constants.py               (all magic strings)
├── db/
│   ├── client.py              ← config.py  (sqlite3 connection + init_db)
│   └── queries.py             ← db/client.py, constants.py
│                                (raw SQL, parameterized, save_file_locally)
├── llm/
│   └── ollama_client.py       ← config.py, constants.py
│                                (HTTP POST to localhost:11434, 2-retry)
├── notifications/
│   └── email_notifier.py      ← config.py, constants.py
│                                (Gmail STARTTLS, no third-party libs)
├── utils/
│   ├── excel_reader.py        ← constants.py
│   └── file_exporter.py       (python-docx, io — returns bytes)
└── stages/
    ├── stage1_outline.py      ← db/queries.py, llm/ollama_client.py,
    │                             notifications/email_notifier.py, constants.py
    ├── stage2_chapters.py     ← db/queries.py, llm/ollama_client.py,
    │                             notifications/email_notifier.py, constants.py
    └── stage3_compile.py      ← db/queries.py, llm/ollama_client.py,
                                  notifications/email_notifier.py,
                                  utils/file_exporter.py, constants.py
```

No circular imports. All dependencies flow downward: stages → llm/db/notifications/utils → config/constants.

---

## Data Flow

```
Excel (.xlsx)
    │
    │  utils/excel_reader.py → read_books()
    ▼
Row dict {title, notes_on_outline_before, ...}
    │
    │  stages/stage1_outline.py → run()
    ▼
SQLite: books row inserted (status='pending')     [./data/books.db]
    │
    │  Gate 1: notes_before?
    │  Gate 2: status_outline_notes?
    │  Ollama: generate_outline() / regenerate_outline()
    ▼
SQLite: books.outline stored, status='outline_approved'
    │
    │  stages/stage2_chapters.py → run()
    ▼
SQLite: chapters rows inserted one at a time
    │
    │  For each chapter:
    │    - SELECT summaries of N-1 prior chapters (SQLite)
    │    - Ollama: write_chapter()
    │    - Ollama: summarise_chapter()
    │    - Gate: chapter_notes_status?
    ▼
SQLite: all chapters.status='approved'
    │
    │  stages/stage3_compile.py → run()
    ▼
Gate: all chapters approved?
    │
    │  Optional: Ollama editorial_pass_intro() per chapter
    │  utils/file_exporter.py → build_docx(), build_txt()
    ▼
Local filesystem:
  ./outputs/{book_id}/book.docx
  ./outputs/{book_id}/book.txt
    │
    │  SQLite: event_log: book_compiled
    │  Gmail SMTP: email with local file paths
    ▼
COMPLETE
```

---

## Gating Logic Table

| Stage | Gate Field | Pass Condition | Fail Action |
|---|---|---|---|
| 1 | `notes_on_outline_before` | Non-empty string | Log `missing_notes_before`, email, skip row |
| 1 | `status_outline_notes` | `no_notes_needed` OR (`yes` AND `notes_on_outline_after` non-empty) | Log `outline_review_pending`, email, pause |
| 2 | `chapter_notes_status` (per chapter) | `no_notes_needed` OR (`yes` AND `chapter_notes` non-empty) | Log `chapter_review_pending`, email, pause; halt subsequent chapters |
| 3 | All `chapters.status` | Every row = `approved` | Log `compilation_blocked`, email, abort |

---

## Context Chaining Explanation

1. **After writing chapter N**, Ollama is called a second time to produce a **3-sentence summary** stored in `chapters.summary`.
2. **Before writing chapter N+1**, `get_previous_summaries()` fetches all rows where `chapter_number < N+1` selecting only `chapter_number`, `title`, and `summary` from SQLite.
3. These summaries are joined with double-newlines and passed as `chapter_summaries` in the chapter-writing prompt.
4. **Token budget enforcement**: if the combined summaries exceed `MAX_CONTEXT_TOKENS` (6000 estimated tokens), `summarise_summaries()` compresses the block via a second Ollama call before passing it forward.

---

## Token Budget Breakdown

| Call Type | max_tokens (config) | temperature | Context Passed |
|---|---|---|---|
| Generate outline | 1024 | 0.7 | title + notes_before |
| Regenerate outline | 1024 | 0.7 | title + original outline + notes_after |
| Write chapter | 2048 | 0.7 | title + outline + summaries (≤6000 tokens) |
| Rewrite chapter | 2048 | 0.7 | title + outline + summaries + original + notes |
| Summarise chapter | 512 | 0.3 | chapter content |
| Meta-summarise summaries | 512 | 0.3 | all prior summaries |
| Editorial pass (intro) | 512 | 0.3 | intro paragraph + final notes |

Budget is enforced in `ollama_client._assert_token_budget()` before every call.

---

## Ollama Retry Strategy

Each call to the Ollama HTTP API is attempted up to **3 times** (1 initial + 2 retries):
- Retry delay: exponential backoff starting at 2 seconds (2s → 4s).
- Timeout per request: 120 seconds.
- Failures: HTTP errors, timeouts, and validation failures (empty or too-short responses) all trigger retry.
- After all retries exhausted: `OllamaAPIError` is raised and logged; the pipeline halts for this book.
