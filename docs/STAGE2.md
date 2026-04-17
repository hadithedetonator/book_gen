# Stage 2 — Chapter Generation

## Purpose

Stage 2 takes a book that has an approved outline and produces its chapters one by one, in strict sequential order. For each chapter, it retrieves the summaries of all previously written chapters to maintain narrative continuity (context chaining), calls Claude to write the chapter prose, calls Claude again to generate a 3-sentence summary for future context, and then enforces a gate before moving to the next chapter. Chapter N+1 is never initiated until chapter N has been explicitly approved by a human reviewer. This guarantees that no chapter contradicts, ignores, or loses context from its predecessors.

---

## Input → Process → Output

| Phase | Input | Process | Output |
|---|---|---|---|
| Load book | `book_id` | `get_book_by_id()` | Book row with `outline` and `title` |
| Parse outline | Outline text | Deterministic regex parser | `[{chapter_number, title}]` list |
| Build context | Prior chapter rows | `get_previous_summaries()` | Combined summaries string |
| Budget check | Combined summaries | Token estimate (len/4) | Pass or trigger meta-summarise |
| Write chapter | Title + outline + summaries + chapter N info | Claude (2048 tokens) | Full chapter prose |
| Summarise | Chapter prose | Claude (512 tokens) | 3-sentence summary |
| Store | Content + summary | `update_chapter_content()` | `chapters` row updated |
| Gate | `chapter_notes_status` | Three-branch evaluation | Approve / Regenerate / Pause |
| Repeat | Next chapter | Same flow | Until all chapters approved |

---

## Gate Conditions (per chapter)

| `chapter_notes_status` | `chapter_notes` | Result |
|---|---|---|
| `no_notes_needed` | Any | ✅ PASS — status set to `approved`, next chapter starts |
| `yes` | Non-empty | ✅ PASS (after regeneration) — Claude rewrites, approve |
| `yes` | Empty | ❌ PAUSE — log `chapter_review_pending`, email, halt |
| `no` | Any | ❌ PAUSE — log `chapter_review_pending`, email, halt |
| Empty / NULL | Any | ❌ PAUSE — same as `no` |

When the gate halts: no subsequent chapter is generated. The operator must update `chapter_notes_status` in Supabase and re-run Stage 2 for the book.

---

## Claude Prompts Used

### Write Chapter

**System:**
```
You are a professional book author.
Write engaging, coherent prose that maintains consistent tone and terminology.
Do not contradict anything established in earlier chapters.
Do not introduce characters or concepts absent from the outline.
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

Model: `claude-3-5-sonnet-20241022` | max_tokens: `2048` | temperature: `0.7`

---

### Summarise Chapter

**System:**
```
You are a precise book summariser.
Produce exactly three sentences that capture the key events,
ideas, and tone of the chapter. Be factual, not evaluative.
```

**User:**
```
Chapter {N}: {chapter_title}

{chapter_content}

Write a 3-sentence summary of the chapter above.
```

Model: `claude-3-5-sonnet-20241022` | max_tokens: `512` | temperature: `0.3`

---

### Rewrite Chapter with Notes

**System:**
```
You are a professional book author revising a chapter based on editorial feedback.
Incorporate all reviewer notes while preserving the narrative outline and consistency.
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

Please produce the revised chapter, incorporating all feedback.
```

Model: `claude-3-5-sonnet-20241022` | max_tokens: `2048` | temperature: `0.7`

---

### Meta-Summarise Summaries (token budget overflow)

**System:**
```
You are a book editor. Condense the following chapter summaries into
a single tight paragraph that preserves all key narrative facts,
characters, and tone signals.
```

**User:**
```
Chapter summaries:
{combined_summaries}

Produce a condensed combined summary.
```

Model: `claude-3-5-sonnet-20241022` | max_tokens: `512` | temperature: `0.3`

---

## Error Handling

| Failure Point | Action |
|---|---|
| Book not found in Supabase | `RuntimeError` raised, process aborts |
| No outline stored on book | `Stage2Error` raised — run Stage 1 first |
| Outline parser finds zero chapters | `Stage2Error` raised with guidance on outline format |
| Claude error (write chapter) | Log `claude_api_error`, set chapter `status='error'`, raise `Stage2Error` |
| Claude error (summarise chapter) | Log `claude_api_error`, raise `Stage2Error` |
| Claude error (meta-summarise) | `ClaudeAPIError` propagates to caller |
| Claude error (rewrite chapter) | Log `claude_api_error`, raise `Stage2Error` |
| Token budget overflow | Auto-handled: `summarise_summaries()` called before write_chapter |
| Gate fails (no / empty status) | Log `chapter_review_pending`, email, raise `Stage2Error`; halt all subsequent chapters |
| Email send failure | Logged as `ERROR`; does not halt the pipeline |
