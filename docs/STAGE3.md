# Stage 3 — Final Compilation

## Purpose

Stage 3 takes a book whose every chapter has been individually approved, optionally applies a light editorial pass to each chapter's opening paragraph using Claude (if final review notes are present), and compiles the full manuscript into two output formats: a `.docx` file (with a title page, static table of contents, and formatted chapter blocks) and a `.txt` file (plain concatenation). Both files are uploaded to the Supabase Storage bucket `book-outputs`. The stage concludes by logging a `book_compiled` event and sending a final email notification containing the download URLs.

---

## Input → Process → Output

| Phase | Input | Process | Output |
|---|---|---|---|
| Load book | `book_id` | `get_book_by_id()` | Book row with `final_review_notes` |
| Load chapters | `book_id` | `get_chapters_for_book()` | All chapter rows ordered by number |
| Gate | All chapter statuses | Check every row = `approved` | Pass or abort + email |
| Editorial pass (optional) | Chapter intro paragraphs + final notes | Claude (512 tokens, temp 0.3) | Revised intro paragraphs stored back in Supabase |
| Build .docx | Chapter dicts | `file_exporter.build_docx()` | In-memory bytes |
| Build .txt | Chapter dicts | `file_exporter.build_txt()` | In-memory bytes |
| Upload .docx | Bytes | `queries.upload_file_to_storage()` | Public/signed URL |
| Upload .txt | Bytes | `queries.upload_file_to_storage()` | Public/signed URL |
| Finalise | URLs | Log `book_compiled`, email | `books.status='complete'` |

---

## Gate Conditions

### Gate: All Chapters Approved

| Condition | Result |
|---|---|
| All chapters have `status='approved'` | ✅ PASS — proceed to compilation |
| One or more chapters have any other status | ❌ FAIL — log `compilation_blocked`, email with chapter list, abort |

---

## Claude Prompt Used

### Editorial Pass — Intro Paragraph Only

**System:**
```
You are a copy editor performing a final light editorial pass.
Revise only the opening paragraph of the chapter according to the notes provided.
Do NOT rewrite the rest of the chapter.
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

Model: `claude-3-5-sonnet-20241022` | max_tokens: `512` | temperature: `0.3`

This call is made **once per chapter** only when `book.final_review_notes` is non-empty. It is entirely skipped otherwise.

---

## .docx Structure

The generated Word document contains:

1. **Title Page** — Book title (28pt bold, centred) + subtitle line (italic, centred).
2. **Page break.**
3. **Table of Contents** (static, not a Word TOC field) — `Heading 1: "Table of Contents"` followed by one `List Number` paragraph per chapter: `Chapter N: Title`.
4. **Page break.**
5. **For each chapter** — `Heading 1: "Chapter N: Title"` followed by one paragraph per double-newline-separated block of content. Chapter ends with a page break.

---

## Error Handling

| Failure Point | Action |
|---|---|
| Book not found in Supabase | `RuntimeError` raised, process aborts |
| No chapters in Supabase | `Stage3Error` — run Stage 2 first |
| One or more unapproved chapters | Log `compilation_blocked`, email, raise `Stage3Error` |
| Claude error during editorial pass | Log `claude_api_error`, raise `Stage3Error` |
| `build_docx()` or `build_txt()` failure | `ExportError` → raise `Stage3Error` |
| Storage upload failure | Log error, raise `Stage3Error` |
| Email send failure on `book_compiled` | Logged as `ERROR`; book is still marked `complete` |
