# Stage 1 — Input + Outline

## Purpose

Stage 1 is the entry point of the pipeline. It reads a single book record (sourced from the Excel file), enforces that required editorial notes exist, calls the local **Ollama** LLM to generate a structured chapter-by-chapter outline, then evaluates the human reviewer's decision on that outline before allowing progression to Stage 2. Every decision point is a hard gate: if the gate fails, the system pauses, logs the event to SQLite, and sends a Gmail notification. It never silently skips.

---

## Input → Process → Output

| Phase | Input | Process | Output |
|---|---|---|---|
| Read | Excel row dict | Validate required fields | Structured Python dict |
| Insert | Title + notes_before | `db/queries.insert_book()` | `books` row in SQLite (`status='pending'`) |
| Gate 1 | `notes_on_outline_before` | Check non-empty | Pass/Fail |
| Generate | Title + notes_before | Ollama HTTP API call | Outline text |
| Store | Outline text | `db/queries.update_book_outline()` | `books.outline` updated in SQLite |
| Gate 2 | `status_outline_notes` + `notes_on_outline_after` | Three-branch logic | Approve / Regenerate / Pause |
| Finalise | Approved outline | `update_book_status('outline_approved')` | Book ready for Stage 2 |

---

## Gate Conditions

### Gate 1: notes_on_outline_before

| Condition | Result |
|---|---|
| Field is non-empty | ✅ PASS — proceed to outline generation |
| Field is empty or missing | ❌ FAIL — log `missing_notes_before`, Gmail notification, skip this book |

### Gate 2: status_outline_notes

| Field Value | notes_on_outline_after | Result |
|---|---|---|
| `no_notes_needed` | Any | ✅ PASS — outline approved, proceed to Stage 2 |
| `yes` | Non-empty | ✅ PASS — Ollama regenerates outline with notes |
| `yes` | Empty | ❌ PAUSE — log `outline_review_pending`, Gmail notification |
| `no` | Any | ❌ PAUSE — log `outline_review_pending`, Gmail notification |
| Empty / NULL | Any | ❌ PAUSE — same as `no` |

---

## Claude Prompt Used

> **Note:** "Claude" prompt labels are preserved for documentation clarity. The system uses Ollama (local LLM) with identical prompt text.

### Initial Outline Generation

**System:**
```
You are an expert book editor and author.
Your task is to produce a clear, well-structured book outline.
Format the outline as numbered chapters with a one-line description each.
Example format:
1. Introduction: Overview of the main theme
2. Background: Historical context and key concepts
Use plain text — no markdown, no bullet points.
```

**User:**
```
Book title: {title}

Editorial notes:
{notes_on_outline_before}

Please generate a detailed chapter-by-chapter outline for this book.
Number each chapter as: 1. Title: Description
```

Model: `$OLLAMA_MODEL` (default: `llama3`) | temperature: `0.7`

**Validation:** Response must contain at least one line starting with a digit. If not, the call is retried (max 2 retries).

---

### Outline Regeneration (after reviewer notes)

**System:**
```
You are an expert book editor.
Revise the given chapter outline to incorporate the reviewer's feedback.
Keep the same numbering format: '1. Title: Description'. Plain text only.
```

**User:**
```
Book title: {title}

Original outline:
{original_outline}

Revision notes:
{notes_on_outline_after}

Produce the revised outline using the same numbered format.
```

Model: `$OLLAMA_MODEL` | temperature: `0.7`

---

## Error Handling

| Failure Point | Action |
|---|---|
| Excel file not found or malformed | `ExcelReadError` raised; process aborts |
| `notes_on_outline_before` empty | Log `missing_notes_before`, Gmail email, raise `Stage1Error`; next book continues |
| Ollama API timeout (>120s) | Retry up to 2 times with exponential backoff; then `OllamaAPIError` → `Stage1Error` |
| Ollama returns empty/short response | Retry (validation failure); after 3 attempts raise `OllamaAPIError` |
| Outline has no numbered lines | `OllamaAPIError` raised — structural validation failure |
| SQLite insert/update failure | Python exception propagates; process aborts for this book |
| Gmail send failure | Logged as `ERROR`; does NOT halt the pipeline |
| `status_outline_notes` = `no`/empty | Log `outline_review_pending`, email, raise `Stage1Error` (pause, not crash) |
| `status_outline_notes` = `yes` + empty notes | Log `outline_review_pending`, email, raise `Stage1Error` |

---

## Resuming After Pause

```bash
# 1. Edit the database
sqlite3 ./data/books.db
UPDATE books SET status_outline_notes = 'no_notes_needed' WHERE id = '<uuid>';

# 2. Re-run Stage 1 gate
python main.py --book-id <uuid> --stage 1
```
