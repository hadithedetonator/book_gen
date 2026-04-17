# Database Reference

## Full Schema (SQLite)

### Table: `books`

| Column | Type | Nullable | Default | Constraint |
|---|---|---|---|---|
| `id` | `TEXT` | NOT NULL | Python `uuid4()` | PRIMARY KEY |
| `title` | `TEXT` | NOT NULL | — | — |
| `notes_on_outline_before` | `TEXT` | NULL | — | — |
| `outline` | `TEXT` | NULL | — | — |
| `notes_on_outline_after` | `TEXT` | NULL | — | — |
| `final_review_notes` | `TEXT` | NULL | — | — |
| `status_outline_notes` | `TEXT` | NULL | — | CHECK IN (`yes`,`no`,`no_notes_needed`) |
| `status` | `TEXT` | NOT NULL | `'pending'` | — |
| `created_at` | `TEXT` | NOT NULL | `strftime(...)` | ISO-8601 UTC |
| `updated_at` | `TEXT` | NOT NULL | `strftime(...)` | Auto-updated via AFTER UPDATE trigger |

### Table: `chapters`

| Column | Type | Nullable | Default | Constraint |
|---|---|---|---|---|
| `id` | `TEXT` | NOT NULL | Python `uuid4()` | PRIMARY KEY |
| `book_id` | `TEXT` | NOT NULL | — | FK → `books.id` ON DELETE CASCADE |
| `chapter_number` | `INTEGER` | NOT NULL | — | UNIQUE with `book_id` |
| `title` | `TEXT` | NULL | — | — |
| `content` | `TEXT` | NULL | — | — |
| `summary` | `TEXT` | NULL | — | — |
| `chapter_notes` | `TEXT` | NULL | — | — |
| `chapter_notes_status` | `TEXT` | NULL | — | CHECK IN (`yes`,`no`,`no_notes_needed`) |
| `status` | `TEXT` | NOT NULL | `'pending'` | — |
| `created_at` | `TEXT` | NOT NULL | `strftime(...)` | ISO-8601 UTC |

### Table: `event_log`

| Column | Type | Nullable | Default | Constraint |
|---|---|---|---|---|
| `id` | `TEXT` | NOT NULL | Python `uuid4()` | PRIMARY KEY |
| `book_id` | `TEXT` | NULL | — | FK → `books.id` ON DELETE SET NULL |
| `event` | `TEXT` | NOT NULL | — | — |
| `detail` | `TEXT` | NULL | — | — |
| `created_at` | `TEXT` | NOT NULL | `strftime(...)` | ISO-8601 UTC |

> **Note on UUIDs:** SQLite has no native UUID type. All IDs are stored as `TEXT` and generated in Python using `uuid.uuid4()`.

---

## SQLite-Specific Notes

- **Foreign keys** must be enabled per-connection: `PRAGMA foreign_keys = ON` — this is done automatically in `db/client.py → get_connection()`.
- **Timestamps** use `strftime('%Y-%m-%dT%H:%M:%SZ', 'now')` for defaults and an `AFTER UPDATE` trigger on `books` to maintain `updated_at`.
- **Parameterised queries** use `?` placeholders throughout `db/queries.py`. No string interpolation is used.

---

## Entity Relationship

- A **book** has zero or more **chapters**, keyed uniquely by `(book_id, chapter_number)`.
- A **book** generates zero or more **event_log** entries. If a book is deleted, its chapters cascade-delete; event log entries are orphaned (`book_id = NULL`) but retained for audit purposes.
- There is no direct foreign key between `chapters` and `event_log`. Events are always book-scoped.

---

## Book Row Lifecycle

```
pending
  │
  ├── [Gate 1 FAIL] ──────────────────── → error
  │
  ▼
outline_generated
  │
  ├── [Gate 2: status=no/empty] ──────── → outline_review_pending (pause)
  ├── [Gate 2: status=yes+notes] ──────→ outline_generated (regenerate) → outline_approved
  └── [Gate 2: no_notes_needed] ──────→ outline_approved
                                               │
                                               ▼
                                    chapters_in_progress
                                               │
                                    [All chapters approved]
                                               │
                                               ▼
                                    chapters_complete
                                               │
                                               ▼
                                           compiling
                                               │
                                    [Gate 3 FAIL] ──── → error
                                               │
                                               ▼
                                           complete
```

---

## Chapter Row Lifecycle

```
pending → generated → [chapter_notes_status gate]
                            │
                            ├── no_notes_needed → approved
                            ├── yes + notes     → [Ollama rewrite] → approved
                            └── no / empty      → review_pending (pause)
```

---

## Viewing the Database

```bash
# Open with sqlite3 CLI
sqlite3 ./data/books.db

# Useful queries
.headers on
.mode column
SELECT id, title, status FROM books;
SELECT chapter_number, title, status FROM chapters WHERE book_id='<uuid>';
SELECT event, detail, created_at FROM event_log ORDER BY created_at DESC LIMIT 20;
```

---

## Indexing

| Index | Table | Columns | Rationale |
|---|---|---|---|
| `idx_chapters_book_id_number` | `chapters` | `(book_id, chapter_number)` | All chapter queries filter by book_id and order by number |
| `idx_event_log_book_id` | `event_log` | `(book_id)` | Look up all events for a book |
| `idx_event_log_event` | `event_log` | `(event)` | Query events by type across all books |
