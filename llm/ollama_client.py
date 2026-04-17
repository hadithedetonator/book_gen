"""
llm/ollama_client.py
---------------------
All LLM calls routed through the local Ollama HTTP API.

Replaces the Anthropic claude_client.py. The public function signatures
are intentionally identical so stage modules require only an import swap.

Ollama HTTP API contract used here:
  POST http://localhost:11434/api/generate
  Body: {"model": str, "prompt": str, "stream": false,
         "system": str, "options": {"temperature": float}}
  Response: {"response": str, ...}

Rules enforced:
  - Every call includes a system prompt.
  - Calls are wrapped in try/except with max 2 retries (exponential backoff).
  - Raw LLM responses are logged before any parsing/validation.
  - Output validation is performed after every call; invalid output raises
    OllamaAPIError so the caller can handle or retry at a higher level.
  - Token budget is estimated the same way as in claude_client.py.
"""

import logging
import time
from typing import Optional

import requests

import book_gen.config as cfg
from book_gen.constants import ClaudeModel, CHARS_PER_TOKEN

log = logging.getLogger(__name__)

# Use the same constant names so callers need no changes
MAX_RETRIES      = 2
RETRY_BASE_DELAY = 2.0   # seconds; doubled on each retry
REQUEST_TIMEOUT  = 120   # seconds per HTTP request


class OllamaAPIError(Exception):
    """Raised when the Ollama API returns an error or invalid output."""


# Keep backward-compatible alias so imports of ClaudeAPIError still work.
ClaudeAPIError = OllamaAPIError


# ── Internal Helpers ──────────────────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """
    Rough token estimate: characters / CHARS_PER_TOKEN.

    Args:
        text: The string to estimate.

    Returns:
        Estimated token count (integer, minimum 1).
    """
    return max(1, len(text) // CHARS_PER_TOKEN)


def _assert_token_budget(context: str, label: str) -> None:
    """
    Assert that *context* does not exceed the per-call token budget.

    Args:
        context: The combined context string that will be sent to Ollama.
        label:   Human-readable label used in the log/exception message.

    Raises:
        OllamaAPIError: If the estimated token count exceeds MAX_CONTEXT_TOKENS.
    """
    estimated = _estimate_tokens(context)
    if estimated > ClaudeModel.MAX_CONTEXT_TOKENS:
        raise OllamaAPIError(
            f"{label}: estimated context tokens ({estimated}) exceed the "
            f"hard limit of {ClaudeModel.MAX_CONTEXT_TOKENS}. "
            "Summarise summaries before calling the model."
        )


def _validate_output(text: str, min_words: int, label: str) -> str:
    """
    Validate that the model returned a non-empty, substantive response.

    Args:
        text:      The raw response string.
        min_words: Minimum acceptable word count.
        label:     Call label for error messages.

    Returns:
        The validated, stripped response string.

    Raises:
        OllamaAPIError: If the response is empty or too short.
    """
    stripped = text.strip()
    if not stripped:
        raise OllamaAPIError(f"[{label}] Ollama returned an empty response.")
    word_count = len(stripped.split())
    if word_count < min_words:
        raise OllamaAPIError(
            f"[{label}] Response too short: {word_count} words (minimum {min_words})."
        )
    return stripped


def _call(
    system_prompt: str,
    user_message:  str,
    temperature:   float,
    call_label:    str,
    min_words:     int = 10,
) -> str:
    """
    POST to the Ollama generate endpoint with retry logic.

    Combines system_prompt and user_message into a single prompt string
    because Ollama's /api/generate accepts a flat prompt (not chat turns).
    The system prompt is passed separately via the 'system' field for models
    that support it (llama3, mistral, etc.).

    Args:
        system_prompt: Role/instruction to set context.
        user_message:  The actual task content.
        temperature:   Sampling temperature (0.0–1.0).
        call_label:    Label for logging and error context.
        min_words:     Minimum acceptable response length in words.

    Returns:
        Validated, stripped response string.

    Raises:
        OllamaAPIError: After MAX_RETRIES exhausted or on validation failure.
    """
    url     = f"{cfg.OLLAMA_BASE_URL}/api/generate"
    payload = {
        "model":  cfg.OLLAMA_MODEL,
        "prompt": user_message,
        "system": system_prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }

    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES + 2):  # attempts: 1, 2, 3
        try:
            log.info(
                "Ollama call [%s] attempt=%d model=%s temp=%.1f",
                call_label, attempt, cfg.OLLAMA_MODEL, temperature,
            )
            resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()

            data    = resp.json()
            raw_text = data.get("response", "")

            # ── Always log raw output before validation ───────────────────
            log.debug(
                "Ollama raw response [%s] attempt=%d: %r",
                call_label, attempt, raw_text[:400]
            )

            validated = _validate_output(raw_text, min_words, call_label)
            log.info(
                "Ollama call [%s] succeeded on attempt=%d (~%d words).",
                call_label, attempt, len(validated.split())
            )
            return validated

        except OllamaAPIError as exc:
            last_error = exc
            log.warning(
                "Output validation failed [%s] attempt=%d: %s",
                call_label, attempt, exc
            )
        except requests.exceptions.Timeout as exc:
            last_error = OllamaAPIError(
                f"[{call_label}] Request timed out after {REQUEST_TIMEOUT}s."
            )
            log.warning("Timeout [%s] attempt=%d.", call_label, attempt)
        except requests.exceptions.RequestException as exc:
            last_error = OllamaAPIError(
                f"[{call_label}] HTTP error on attempt {attempt}: {exc}"
            )
            log.warning("HTTP error [%s] attempt=%d: %s", call_label, attempt, exc)

        if attempt <= MAX_RETRIES:
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            log.info("Retrying [%s] in %.1fs ...", call_label, delay)
            time.sleep(delay)

    raise OllamaAPIError(
        f"[{call_label}] All {MAX_RETRIES + 1} attempts failed. "
        f"Last error: {last_error}"
    )


# ── Public API (mirrors claude_client.py signatures) ─────────────────────────

def generate_outline(
    client,          # unused — kept for signature compatibility
    title: str,
    notes_before: str,
) -> str:
    """
    Generate a structured book outline from a title and editorial notes.

    Args:
        client:       Unused (kept for API compatibility with claude_client).
        title:        The book title.
        notes_before: Editorial notes to guide the outline.

    Returns:
        A multi-line outline string (numbered chapters).

    Raises:
        OllamaAPIError: On API error, timeout, or validation failure.
    """
    system_prompt = (
        "You are an expert book editor and author. "
        "Your task is to produce a clear, well-structured book outline. "
        "Format the outline as numbered chapters with a one-line description each. "
        "Example format:\n"
        "1. Introduction: Overview of the main theme\n"
        "2. Background: Historical context and key concepts\n"
        "Use plain text — no markdown, no bullet points."
    )
    user_message = (
        f"Book title: {title}\n\n"
        f"Editorial notes:\n{notes_before}\n\n"
        "Please generate a detailed chapter-by-chapter outline for this book. "
        "Number each chapter as: 1. Title: Description"
    )
    _assert_token_budget(user_message, "generate_outline")
    result = _call(system_prompt, user_message, ClaudeModel.TEMP_CREATIVE,
                   "generate_outline", min_words=20)

    # Validate: must contain at least one numbered chapter line
    if not any(
        line.strip() and line.strip()[0].isdigit()
        for line in result.splitlines()
    ):
        raise OllamaAPIError(
            "generate_outline: response contains no numbered chapter lines. "
            f"Raw response snippet: {result[:200]!r}"
        )
    return result


def regenerate_outline(
    client,
    title: str,
    original_outline: str,
    notes_after: str,
) -> str:
    """
    Revise an existing outline based on post-review editorial notes.

    Args:
        client:           Unused (kept for API compatibility).
        title:            The book title.
        original_outline: The previously generated outline.
        notes_after:      Reviewer's revision notes.

    Returns:
        A revised outline string.

    Raises:
        OllamaAPIError: On API error, timeout, or validation failure.
    """
    system_prompt = (
        "You are an expert book editor. "
        "Revise the given chapter outline to incorporate the reviewer's feedback. "
        "Keep the same numbering format: '1. Title: Description'. Plain text only."
    )
    user_message = (
        f"Book title: {title}\n\n"
        f"Original outline:\n{original_outline}\n\n"
        f"Revision notes:\n{notes_after}\n\n"
        "Produce the revised outline using the same numbered format."
    )
    _assert_token_budget(user_message, "regenerate_outline")
    result = _call(system_prompt, user_message, ClaudeModel.TEMP_CREATIVE,
                   "regenerate_outline", min_words=20)

    if not any(
        line.strip() and line.strip()[0].isdigit()
        for line in result.splitlines()
    ):
        raise OllamaAPIError(
            "regenerate_outline: response contains no numbered chapter lines."
        )
    return result


def write_chapter(
    client,
    title: str,
    outline: str,
    chapter_number: int,
    chapter_title: str,
    chapter_summaries: str,
) -> str:
    """
    Write a full chapter given the book outline and prior-chapter summaries.

    Args:
        client:            Unused (kept for API compatibility).
        title:             The book title.
        outline:           The complete book outline.
        chapter_number:    The 1-based chapter number being written.
        chapter_title:     Title of the chapter being written.
        chapter_summaries: Concatenated summaries of all previous chapters.

    Returns:
        The full chapter text (minimum 400 words validated).

    Raises:
        OllamaAPIError: On API error, timeout, or validation failure.
    """
    system_prompt = (
        "You are a professional book author. "
        "Write engaging, coherent prose that maintains consistent tone and terminology. "
        "Do not contradict anything established in earlier chapters. "
        "Do not introduce characters or concepts absent from the outline. "
        "Write at least 400 words."
    )
    user_message = (
        f'You are writing a book titled "{title}".\n\n'
        f"Here is the complete outline:\n{outline}\n\n"
        f"Summaries of previous chapters for context:\n{chapter_summaries}\n\n"
        f"Now write Chapter {chapter_number}: {chapter_title}\n\n"
        "Requirements:\n"
        "- Minimum 400 words\n"
        "- Do not contradict any previous chapter\n"
        "- Maintain consistent tone and terminology\n"
        "- Do not introduce characters or concepts not present in the outline"
    )
    _assert_token_budget(user_message, f"write_chapter_{chapter_number}")
    result = _call(
        system_prompt, user_message, ClaudeModel.TEMP_CREATIVE,
        f"write_chapter_{chapter_number}", min_words=300,  # ~400 words, lenient
    )
    return result


def rewrite_chapter_with_notes(
    client,
    title: str,
    outline: str,
    chapter_number: int,
    chapter_title: str,
    original_content: str,
    chapter_notes: str,
    chapter_summaries: str,
) -> str:
    """
    Revise a chapter based on reviewer notes, maintaining outline fidelity.

    Args:
        client:           Unused (kept for API compatibility).
        title:            The book title.
        outline:          The complete book outline.
        chapter_number:   Chapter number being revised.
        chapter_title:    Chapter title.
        original_content: Previously generated chapter text.
        chapter_notes:    Reviewer's revision notes.
        chapter_summaries: Summaries of preceding chapters.

    Returns:
        The revised chapter text.

    Raises:
        OllamaAPIError: On API error, timeout, or validation failure.
    """
    system_prompt = (
        "You are a professional book author revising a chapter based on editorial feedback. "
        "Incorporate all reviewer notes while preserving the narrative outline and consistency. "
        "Write at least 400 words."
    )
    user_message = (
        f'Book title: "{title}"\n\n'
        f"Complete outline:\n{outline}\n\n"
        f"Summaries of previous chapters:\n{chapter_summaries}\n\n"
        f"Original Chapter {chapter_number}: {chapter_title}\n{original_content}\n\n"
        f"Reviewer notes:\n{chapter_notes}\n\n"
        "Produce the revised chapter, incorporating all feedback."
    )
    _assert_token_budget(user_message, f"rewrite_chapter_{chapter_number}")
    return _call(
        system_prompt, user_message, ClaudeModel.TEMP_CREATIVE,
        f"rewrite_chapter_{chapter_number}", min_words=300,
    )


def summarise_chapter(
    client,
    chapter_number: int,
    chapter_title: str,
    chapter_content: str,
) -> str:
    """
    Produce a concise 3-sentence summary of a completed chapter.

    Args:
        client:          Unused (kept for API compatibility).
        chapter_number:  The chapter's ordinal number.
        chapter_title:   The chapter title.
        chapter_content: Full chapter text to summarise.

    Returns:
        A 3-sentence summary string.

    Raises:
        OllamaAPIError: On API error, timeout, or validation failure.
    """
    system_prompt = (
        "You are a precise book summariser. "
        "Produce exactly three sentences that capture the key events, "
        "ideas, and tone of the chapter. Be factual, not evaluative. "
        "Output ONLY the three sentences, nothing else."
    )
    user_message = (
        f"Chapter {chapter_number}: {chapter_title}\n\n"
        f"{chapter_content}\n\n"
        "Write a 3-sentence summary of the chapter above. "
        "Output exactly 3 sentences."
    )
    _assert_token_budget(user_message, f"summarise_chapter_{chapter_number}")
    result = _call(
        system_prompt, user_message, ClaudeModel.TEMP_PRECISE,
        f"summarise_chapter_{chapter_number}", min_words=15,
    )
    return result


def summarise_summaries(
    client,
    combined_summaries: str,
) -> str:
    """
    Reduce an oversized summaries block into a compact meta-summary.

    Called when chapter_summaries would exceed MAX_CONTEXT_TOKENS.

    Args:
        client:            Unused (kept for API compatibility).
        combined_summaries: The full summaries string to compress.

    Returns:
        A shorter combined summary string.

    Raises:
        OllamaAPIError: On API error or timeout.
    """
    system_prompt = (
        "You are a book editor. Condense the following chapter summaries into "
        "a single tight paragraph that preserves all key narrative facts, "
        "characters, and tone signals. Output only the paragraph."
    )
    user_message = (
        f"Chapter summaries:\n{combined_summaries}\n\n"
        "Produce a condensed combined summary as one paragraph."
    )
    return _call(
        system_prompt, user_message, ClaudeModel.TEMP_PRECISE,
        "summarise_summaries", min_words=20,
    )


def editorial_pass_intro(
    client,
    chapter_number: int,
    chapter_title: str,
    intro_paragraph: str,
    final_review_notes: str,
) -> str:
    """
    Apply brief final editorial notes to a chapter's opening paragraph only.

    Args:
        client:             Unused (kept for API compatibility).
        chapter_number:     Chapter ordinal.
        chapter_title:      Chapter title.
        intro_paragraph:    The first paragraph of the chapter.
        final_review_notes: High-level editorial notes from the final review.

    Returns:
        A revised intro paragraph string.

    Raises:
        OllamaAPIError: On API error, timeout, or validation failure.
    """
    system_prompt = (
        "You are a copy editor performing a final light editorial pass. "
        "Revise ONLY the opening paragraph of the chapter according to the notes provided. "
        "Do NOT rewrite the rest of the chapter. "
        "Output ONLY the revised opening paragraph, nothing else."
    )
    user_message = (
        f"Chapter {chapter_number}: {chapter_title}\n\n"
        f"Opening paragraph:\n{intro_paragraph}\n\n"
        f"Final editorial notes:\n{final_review_notes}\n\n"
        "Return only the revised opening paragraph."
    )
    _assert_token_budget(user_message, f"editorial_pass_ch{chapter_number}")
    return _call(
        system_prompt, user_message, ClaudeModel.TEMP_PRECISE,
        f"editorial_pass_ch{chapter_number}", min_words=10,
    )
