# Stage 2 — Chapter Generation

## Purpose

Stage 2 takes a book that has an approved outline and produces its chapters one by one, in strict sequential order. For each chapter, it retrieves the summaries of all previously written chapters from SQLite (context chaining), calls Ollama to write the chapter prose, calls Ollama again to generate a 3-sentence summary, and then enforces a gate before moving to the next chapter. Chapter N+1 is never initiated until chapter N has been explicitly approved in the database. This guarantees no chapter contradicts or loses context from its predecessors.

---

## Input → Process → Output

| Phase | Input | Process | Output |
|---|---|---|---|
| Load book | `book_id` | `get_book_by_id()` | Book row with `outline` and `title` from SQLite |
| Parse outline | Outline text | Deterministic regex parser `^\d+[.):]` | `[{chapter_number, title}]` list |
| Build context | Prior chapter rows | `get_previous_summaries()` from SQLite | Combined summaries string |
| Budget check | Combined summaries | Token estimate (len/4) | Pass or trigger meta-summarise via Ollama |
| Write chapter | Title + outline + summaries + chapter info | Ollama HTTP API | Full chapter prose (≥300 words validated) |
| Summarise | Chapter prose | Ollama HTTP API | 3-sentence summary |
| Store | Content + summary | `update_chapter_content()` → SQLite | `chapters` row updated |
| Gate | `chapter_notes_status` | Three-branch evaluation | Approve / Regenerate / Pause |
| Repeat | Next chapter | Same flow | Until all chapters approved |

---

## Gate Conditions (per chapter)

| `chapter_notes_status` | `chapter_notes` | Result |
|---|---|---|
| `no_notes_needed` | Any | ✅ PASS — status set to `approved`, next chapter starts |
| `yes` | Non-empty | ✅ PASS (after regeneration) — Ollama rewrites, approve |
| `yes` | Empty | ❌ PAUSE — log `chapter_review_pending`, Gmail email, halt |
| `no` | Any | ❌ PAUSE — log `chapter_review_pending`, Gmail email, halt |
| Empty / NULL | Any | ❌ PAUSE — same as `no` |

When the gate halts: **no subsequent chapter is generated.** Update `chapter_notes_status` in SQLite and re-run Stage 2.

---

## Prompts Used (Ollama)

### Write Chapter

**System:**
```
You are a professional book author.
Write engaging, coherent prose that maintains consistent tone and terminology.
Do not contradict anything established in earlier chapters.
Do not introduce characters or concepts absent from the outline.
Write at least 400 words.
```

**User:**
```
You are writing a book titled "{title}".

Here is the complete outline:
{outline}

Summaries of previous chapters for context:
{chapter_summaries}

Now write Chapter {N}: {chapter_title}

Requirements:
- Minimum 400 words
- Do not contradict any previous chapter
- Maintain consistent tone and terminology
- Do not introduce characters or concepts not present in the outline
```

Temperature: `0.7` | Validation: ≥300 words (lenient threshold) | Retries: up to 2

---

### Summarise Chapter

**System:**
```
You are a precise book summariser.
Produce exactly three sentences that capture the key events,
ideas, and tone of the chapter. Be factual, not evaluative.
Output ONLY the three sentences, nothing else.
```

**User:**
```
Chapter {N}: {chapter_title}

{chapter_content}

Write a 3-sentence summary of the chapter above.
Output exactly 3 sentences.
```

Temperature: `0.3` | Validation: ≥15 words | Retries: up to 2

---

### Rewrite Chapter with Notes

**System:**
```
You are a professional book author revising a chapter based on editorial feedback.
Incorporate all reviewer notes while preserving the narrative outline and consistency.
Write at least 400 words.
```

**User:**
```
Book title: "{title}"

Complete outline:
{outline}

Summaries of previous chapters:
{chapter_summaries}

Original Chapter {N}: {chapter_title}
{original_content}

Reviewer notes:
{chapter_notes}

Produce the revised chapter, incorporating all feedback.
```

Temperature: `0.7` | Validation: ≥300 words | Retries: up to 2

---

### Meta-Summarise Summaries (token budget overflow)

**System:**
```
You are a book editor. Condense the following chapter summaries into
a single tight paragraph that preserves all key narrative facts,
characters, and tone signals. Output only the paragraph.
```

**User:**
```
Chapter summaries:
{combined_summaries}

Produce a condensed combined summary as one paragraph.
```

Temperature: `0.3` | Validation: ≥20 words

---

## Error Handling

| Failure Point | Action |
|---|---|
| Book not found in SQLite | `RuntimeError` raised, process aborts |
| No outline stored | `Stage2Error` raised — run Stage 1 first |
| Outline parser finds zero chapters | `Stage2Error` with guidance on outline format |
| Ollama timeout / HTTP error | Retry up to 2 times; then `OllamaAPIError` → `Stage2Error` |
| Ollama returns empty/short response | Retry (validation failure); `OllamaAPIError` after 3 attempts |
| Token budget overflow | Auto-handled: `summarise_summaries()` called before write_chapter |
| Gate fails (no / empty) | Log `chapter_review_pending`, Gmail email, raise `Stage2Error`; halt all subsequent chapters |
| Gmail send failure | Logged as `ERROR`; pipeline not halted |

---

## Resuming After Pause

```bash
# Approve a chapter with no changes
sqlite3 ./data/books.db \
  "UPDATE chapters SET chapter_notes_status='no_notes_needed' WHERE book_id='<uuid>' AND chapter_number=1;"

# Resume Stage 2
python main.py --book-id <uuid> --stage 2
```
