# Stage 3 — Final Compilation

## Purpose

Stage 3 takes a book whose every chapter has been individually approved, optionally applies a light Ollama editorial pass to each chapter's opening paragraph (if final review notes are present), and compiles the full manuscript into two output formats: a `.docx` file (with title page, static table of contents, and formatted chapter blocks) and a `.txt` file (plain concatenation). Both files are saved locally to `./outputs/{book_id}/`. The stage concludes by logging a `book_compiled` event, updating the book status to `complete` in SQLite, and sending a Gmail notification with the local file paths.

---

## Input → Process → Output

| Phase | Input | Process | Output |
|---|---|---|---|
| Load book | `book_id` | `get_book_by_id()` from SQLite | Book row with `final_review_notes` |
| Load chapters | `book_id` | `get_chapters_for_book()` from SQLite | All chapter rows ordered by number |
| Gate | All chapter statuses | Check every row = `approved` | Pass or abort + Gmail email |
| Editorial pass (optional) | Chapter intro paragraphs + final notes | Ollama (temp 0.3) | Revised intro paragraphs stored back in SQLite |
| Build .docx | Chapter dicts | `file_exporter.build_docx()` | In-memory bytes |
| Build .txt | Chapter dicts | `file_exporter.build_txt()` | In-memory bytes |
| Save .docx | Bytes | `queries.save_file_locally()` | `./outputs/{book_id}/book.docx` |
| Save .txt | Bytes | `queries.save_file_locally()` | `./outputs/{book_id}/book.txt` |
| Finalise | File paths | Log `book_compiled`, Gmail email | `books.status='complete'` |

---

## Gate Conditions

### Gate: All Chapters Approved

| Condition | Result |
|---|---|
| All chapters have `status='approved'` | ✅ PASS — proceed to compilation |
| One or more chapters have any other status | ❌ FAIL — log `compilation_blocked`, Gmail email with chapter list, abort |

---

## Prompt Used (Ollama)

### Editorial Pass — Intro Paragraph Only

**System:**
```
You are a copy editor performing a final light editorial pass.
Revise ONLY the opening paragraph of the chapter according to the notes provided.
Do NOT rewrite the rest of the chapter.
Output ONLY the revised opening paragraph, nothing else.
```

**User:**
```
Chapter {N}: {chapter_title}

Opening paragraph:
{intro_paragraph}

Final editorial notes:
{final_review_notes}

Return only the revised opening paragraph.
```

Temperature: `0.3` | Validation: ≥10 words | Called **once per chapter** only if `book.final_review_notes` is non-empty.

---

## .docx Structure

| Section | Implementation |
|---|---|
| **Title Page** | 28pt bold centred title + italic subtitle line |
| **Page Break** | `doc.add_page_break()` |
| **Table of Contents** | `Heading 1` "Table of Contents" + `List Number` paragraph per chapter |
| **Page Break** | `doc.add_page_break()` |
| **Chapter N** | `Heading 1` "Chapter N: Title" + one paragraph per `\n\n`-separated block + page break |

The TOC is **static text** — not a Word TOC field. It will not auto-update in Word.

---

## Output Files

```
./outputs/
└── {book_id}/
    ├── book.docx   ← formatted Word document
    └── book.txt    ← plain-text concatenation
```

---

## Error Handling

| Failure Point | Action |
|---|---|
| Book not found in SQLite | `RuntimeError` raised, process aborts |
| No chapters in SQLite | `Stage3Error` — run Stage 2 first |
| One or more unapproved chapters | Log `compilation_blocked`, Gmail email, raise `Stage3Error` |
| Ollama error during editorial pass | Log `claude_api_error` event, raise `Stage3Error` |
| `build_docx()` or `build_txt()` failure | `ExportError` → raise `Stage3Error` |
| Local file write failure | Log error, raise `Stage3Error` |
| Gmail send failure on `book_compiled` | Logged as `ERROR`; book is still marked `complete` |

---

## Resuming After Gate Failure

```bash
# Approve all remaining chapters
sqlite3 ./data/books.db \
  "UPDATE chapters SET chapter_notes_status='no_notes_needed', status='approved' \
   WHERE book_id='<uuid>' AND status != 'approved';"

# Re-run Stage 3
python main.py --book-id <uuid> --stage 3
```
