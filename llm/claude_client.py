"""
llm/claude_client.py
--------------------
All Anthropic Claude API calls for the book generation system.

Rules enforced here:
  - Every call includes a system prompt.
  - All calls are wrapped in try/except; errors are re-raised as ClaudeAPIError.
  - Temperature and max_tokens are always explicitly set.
  - Context passed per call must not exceed ClaudeModel.MAX_CONTEXT_TOKENS tokens
    (caller is responsible for pre-truncating; this module asserts the budget).
"""

import logging
import anthropic
from book_gen.constants import ClaudeModel, CHARS_PER_TOKEN

log = logging.getLogger(__name__)


class ClaudeAPIError(Exception):
    """Raised when the Anthropic API returns an error or an unexpected response."""


def _estimate_tokens(text: str) -> int:
    """
    Rough token estimate: characters / CHARS_PER_TOKEN.

    Args:
        text: The string to estimate.

    Returns:
        Estimated token count (integer).
    """
    return max(1, len(text) // CHARS_PER_TOKEN)


def _assert_token_budget(context: str, label: str) -> None:
    """
    Assert that *context* does not exceed the per-call token budget.

    Args:
        context: The combined context string that will be sent to Claude.
        label:   Human-readable label used in the log/exception message.

    Raises:
        ClaudeAPIError: If the estimated token count exceeds MAX_CONTEXT_TOKENS.
    """
    estimated = _estimate_tokens(context)
    if estimated > ClaudeModel.MAX_CONTEXT_TOKENS:
        raise ClaudeAPIError(
            f"{label}: estimated context tokens ({estimated}) exceed the "
            f"hard limit of {ClaudeModel.MAX_CONTEXT_TOKENS}. "
            "Summarise summaries before calling Claude."
        )


def _call(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_message: str,
    max_tokens: int,
    temperature: float,
    call_label: str,
) -> str:
    """
    Internal helper that issues one messages.create call and returns the text.

    Args:
        client:       Authenticated anthropic.Anthropic instance.
        system_prompt: Role description / instruction for Claude.
        user_message:  The human-turn message.
        max_tokens:    Hard cap on the response length.
        temperature:   Sampling temperature (0.0–1.0).
        call_label:    Label for log messages and error context.

    Returns:
        The assistant's reply as a stripped string.

    Raises:
        ClaudeAPIError: On any Anthropic API error or unexpected response shape.
    """
    try:
        log.info("Claude call [%s] — model=%s max_tokens=%d temp=%.1f",
                 call_label, ClaudeModel.ID, max_tokens, temperature)

        response = client.messages.create(
            model=ClaudeModel.ID,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        if not response.content or not response.content[0].text:
            raise ClaudeAPIError(
                f"[{call_label}] Claude returned an empty content block."
            )

        text = response.content[0].text.strip()
        log.info("Claude call [%s] completed — ~%d chars returned.",
                 call_label, len(text))
        return text

    except anthropic.APIError as exc:
        raise ClaudeAPIError(
            f"[{call_label}] Anthropic API error: {exc}"
        ) from exc


# ── Public API ────────────────────────────────────────────────────────────────

def generate_outline(
    client: anthropic.Anthropic,
    title: str,
    notes_before: str,
) -> str:
    """
    Generate a structured book outline from a title and editorial notes.

    Args:
        client:       Authenticated anthropic.Anthropic instance.
        title:        The book title.
        notes_before: Editorial notes that should shape the outline.

    Returns:
        A multi-line outline string.

    Raises:
        ClaudeAPIError: On API error or budget overflow.
    """
    system_prompt = (
        "You are an expert book editor and author. "
        "Your task is to produce a clear, well-structured book outline. "
        "Format the outline as numbered chapters with a one-line description each. "
        "Use plain text — no markdown, no bullet points."
    )
    user_message = (
        f"Book title: {title}\n\n"
        f"Editorial notes:\n{notes_before}\n\n"
        "Please generate a detailed chapter-by-chapter outline for this book."
    )
    _assert_token_budget(user_message, "generate_outline")
    return _call(
        client, system_prompt, user_message,
        ClaudeModel.MAX_TOKENS_OUTLINE,
        ClaudeModel.TEMP_CREATIVE,
        "generate_outline",
    )


def regenerate_outline(
    client: anthropic.Anthropic,
    title: str,
    original_outline: str,
    notes_after: str,
) -> str:
    """
    Revise an existing outline based on post-review editorial notes.

    Args:
        client:           Authenticated anthropic.Anthropic instance.
        title:            The book title.
        original_outline: The previously generated outline.
        notes_after:      Reviewer's revision notes.

    Returns:
        A revised outline string.

    Raises:
        ClaudeAPIError: On API error or budget overflow.
    """
    system_prompt = (
        "You are an expert book editor. "
        "You will receive an existing chapter outline and revision notes. "
        "Produce a revised outline that incorporates the reviewer's feedback. "
        "Format as numbered chapters with a one-line description each. Plain text only."
    )
    user_message = (
        f"Book title: {title}\n\n"
        f"Original outline:\n{original_outline}\n\n"
        f"Revision notes:\n{notes_after}\n\n"
        "Please produce the revised outline."
    )
    _assert_token_budget(user_message, "regenerate_outline")
    return _call(
        client, system_prompt, user_message,
        ClaudeModel.MAX_TOKENS_OUTLINE,
        ClaudeModel.TEMP_CREATIVE,
        "regenerate_outline",
    )


def write_chapter(
    client: anthropic.Anthropic,
    title: str,
    outline: str,
    chapter_number: int,
    chapter_title: str,
    chapter_summaries: str,
) -> str:
    """
    Write a full chapter given the book outline and prior-chapter summaries.

    Args:
        client:            Authenticated anthropic.Anthropic instance.
        title:             The book title.
        outline:           The complete book outline.
        chapter_number:    The 1-based chapter number being written.
        chapter_title:     Title of the chapter being written.
        chapter_summaries: Concatenated 3-sentence summaries of all previous chapters.

    Returns:
        The full chapter text (minimum 400 words expected).

    Raises:
        ClaudeAPIError: On API error or budget overflow.
    """
    system_prompt = (
        "You are a professional book author. "
        "Write engaging, coherent prose that maintains consistent tone and terminology. "
        "Do not contradict anything established in earlier chapters. "
        "Do not introduce characters or concepts absent from the outline."
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
    return _call(
        client, system_prompt, user_message,
        ClaudeModel.MAX_TOKENS_CHAPTER,
        ClaudeModel.TEMP_CREATIVE,
        f"write_chapter_{chapter_number}",
    )


def rewrite_chapter_with_notes(
    client: anthropic.Anthropic,
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
        client:           Authenticated anthropic.Anthropic instance.
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
        ClaudeAPIError: On API error or budget overflow.
    """
    system_prompt = (
        "You are a professional book author revising a chapter based on editorial feedback. "
        "Incorporate all reviewer notes while preserving the narrative outline and consistency."
    )
    user_message = (
        f'Book title: "{title}"\n\n'
        f"Complete outline:\n{outline}\n\n"
        f"Summaries of previous chapters:\n{chapter_summaries}\n\n"
        f"Original Chapter {chapter_number}: {chapter_title}\n{original_content}\n\n"
        f"Reviewer notes:\n{chapter_notes}\n\n"
        "Please produce the revised chapter, incorporating all feedback."
    )
    _assert_token_budget(user_message, f"rewrite_chapter_{chapter_number}")
    return _call(
        client, system_prompt, user_message,
        ClaudeModel.MAX_TOKENS_CHAPTER,
        ClaudeModel.TEMP_CREATIVE,
        f"rewrite_chapter_{chapter_number}",
    )


def summarise_chapter(
    client: anthropic.Anthropic,
    chapter_number: int,
    chapter_title: str,
    chapter_content: str,
) -> str:
    """
    Produce a concise 3-sentence summary of a completed chapter.

    Args:
        client:          Authenticated anthropic.Anthropic instance.
        chapter_number:  The chapter's ordinal number.
        chapter_title:   The chapter title.
        chapter_content: Full chapter text to summarise.

    Returns:
        A 3-sentence summary string.

    Raises:
        ClaudeAPIError: On API error or budget overflow.
    """
    system_prompt = (
        "You are a precise book summariser. "
        "Produce exactly three sentences that capture the key events, "
        "ideas, and tone of the chapter. Be factual, not evaluative."
    )
    user_message = (
        f"Chapter {chapter_number}: {chapter_title}\n\n"
        f"{chapter_content}\n\n"
        "Write a 3-sentence summary of the chapter above."
    )
    _assert_token_budget(user_message, f"summarise_chapter_{chapter_number}")
    return _call(
        client, system_prompt, user_message,
        ClaudeModel.MAX_TOKENS_SUMMARY,
        ClaudeModel.TEMP_PRECISE,
        f"summarise_chapter_{chapter_number}",
    )


def summarise_summaries(
    client: anthropic.Anthropic,
    combined_summaries: str,
) -> str:
    """
    Reduce an oversized summaries block into a compact meta-summary.

    Called when chapter_summaries would exceed MAX_CONTEXT_TOKENS.

    Args:
        client:            Authenticated anthropic.Anthropic instance.
        combined_summaries: The full summaries string to compress.

    Returns:
        A shorter combined summary string.

    Raises:
        ClaudeAPIError: On API error.
    """
    system_prompt = (
        "You are a book editor. Condense the following chapter summaries into "
        "a single tight paragraph that preserves all key narrative facts, "
        "characters, and tone signals."
    )
    user_message = (
        f"Chapter summaries:\n{combined_summaries}\n\n"
        "Produce a condensed combined summary."
    )
    return _call(
        client, system_prompt, user_message,
        ClaudeModel.MAX_TOKENS_SUMMARY,
        ClaudeModel.TEMP_PRECISE,
        "summarise_summaries",
    )


def editorial_pass_intro(
    client: anthropic.Anthropic,
    chapter_number: int,
    chapter_title: str,
    intro_paragraph: str,
    final_review_notes: str,
) -> str:
    """
    Apply brief final editorial notes to a chapter's opening paragraph only.

    Args:
        client:             Authenticated anthropic.Anthropic instance.
        chapter_number:     Chapter ordinal.
        chapter_title:      Chapter title.
        intro_paragraph:    The first paragraph of the chapter.
        final_review_notes: High-level editorial notes from the final review.

    Returns:
        A revised intro paragraph string.

    Raises:
        ClaudeAPIError: On API error or budget overflow.
    """
    system_prompt = (
        "You are a copy editor performing a final light editorial pass. "
        "Revise only the opening paragraph of the chapter according to the notes provided. "
        "Do NOT rewrite the rest of the chapter."
    )
    user_message = (
        f"Chapter {chapter_number}: {chapter_title}\n\n"
        f"Opening paragraph:\n{intro_paragraph}\n\n"
        f"Final editorial notes:\n{final_review_notes}\n\n"
        "Return only the revised opening paragraph."
    )
    _assert_token_budget(user_message, f"editorial_pass_ch{chapter_number}")
    return _call(
        client, system_prompt, user_message,
        ClaudeModel.MAX_TOKENS_SUMMARY,
        ClaudeModel.TEMP_PRECISE,
        f"editorial_pass_ch{chapter_number}",
    )
