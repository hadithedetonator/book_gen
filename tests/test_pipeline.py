"""
tests/test_pipeline.py
-----------------------
End-to-end and unit tests for the local book generation pipeline.

Tests are grouped into three sections:
  1. Unit tests — DB queries, outline parser, file exporter (no Ollama needed)
  2. Integration tests — Stage 1 → 2 pipeline against a real SQLite DB
     with Ollama mocked via unittest.mock.patch
  3. Live smoke test — actually calls Ollama (skipped if Ollama is not running)

Run all tests (mocked LLM):
  python -m pytest tests/test_pipeline.py -v

Run live smoke test only (requires Ollama running with llama3):
  python -m pytest tests/test_pipeline.py -v -k "live"
"""

import os
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Ensure book_gen is importable when run from project root ──────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

# Set fake env vars BEFORE importing config so it doesn't raise on missing vars
os.environ.setdefault("SMTP_EMAIL",    "test@example.com")
os.environ.setdefault("SMTP_PASSWORD", "fake-password")
os.environ.setdefault("NOTIFY_EMAIL",  "notify@example.com")
os.environ.setdefault("OLLAMA_MODEL",  "qwen2.5-coder:3b")

from book_gen.db.client import init_db, get_connection
from book_gen.db import queries
from book_gen.stages.stage2_chapters import parse_outline_into_chapters, Stage2Error
from book_gen.stages.stage1_outline import Stage1Error
from book_gen.utils.file_exporter import build_docx, build_txt
from book_gen.constants import BookStatus, ChapterStatus, NotesStatus


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_db() -> sqlite3.Connection:
    """
    Create an in-memory SQLite database initialised with the full schema.

    Returns:
        Open sqlite3.Connection with schema applied.
    """
    # Temporarily override DB path to in-memory
    original = os.environ.get("SQLITE_DB_PATH", "")
    os.environ["SQLITE_DB_PATH"] = ":memory:"

    # Reload config so SQLITE_DB_PATH takes effect
    import importlib
    import book_gen.config as cfg
    importlib.reload(cfg)

    conn = get_connection()

    # Apply schema manually for in-memory DB
    schema_path = Path(__file__).parent.parent / "schema.sql"
    if schema_path.exists():
        sql = schema_path.read_text()
        script = "\n".join(
            l for l in sql.splitlines()
            if not l.strip().startswith("PRAGMA")
        )
        conn.executescript(script)
        conn.commit()

    os.environ["SQLITE_DB_PATH"] = original
    return conn


def _fake_outline() -> str:
    return (
        "1. The Beginning: How it all started\n"
        "2. Rising Action: Complications emerge\n"
        "3. The Climax: Everything comes to a head\n"
        "4. Resolution: Loose ends tied up\n"
    )


def _fake_chapter_content() -> str:
    return " ".join(["This is a test sentence."] * 80)  # ~400 words


def _fake_summary() -> str:
    return (
        "Chapter one introduces the main character and the central conflict. "
        "The setting is established as a small coastal town in winter. "
        "By the end, the protagonist has made a fateful decision."
    )


# ─────────────────────────────────────────────
# 1. UNIT TESTS
# ─────────────────────────────────────────────

class TestOutlineParser(unittest.TestCase):
    """Tests for the deterministic outline parser in stage2_chapters."""

    def test_parses_dot_format(self):
        outline = "1. Intro\n2. Middle\n3. End\n"
        chapters = parse_outline_into_chapters(outline)
        self.assertEqual(len(chapters), 3)
        self.assertEqual(chapters[0]["chapter_number"], 1)
        self.assertEqual(chapters[0]["title"], "Intro")
        self.assertEqual(chapters[2]["chapter_number"], 3)

    def test_parses_colon_format(self):
        outline = "1: Alpha\n2: Beta\n"
        chapters = parse_outline_into_chapters(outline)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[1]["title"], "Beta")

    def test_parses_paren_format(self):
        outline = "1) First\n2) Second\n"
        chapters = parse_outline_into_chapters(outline)
        self.assertEqual(len(chapters), 2)

    def test_skips_non_chapter_lines(self):
        outline = "Book Overview\n\n1. Chapter One\nSome subtitle\n2. Chapter Two\n"
        chapters = parse_outline_into_chapters(outline)
        self.assertEqual(len(chapters), 2)

    def test_deduplicates_chapter_numbers(self):
        outline = "1. First\n1. Duplicate\n2. Second\n"
        chapters = parse_outline_into_chapters(outline)
        self.assertEqual(len(chapters), 2)

    def test_raises_on_empty_outline(self):
        with self.assertRaises(Stage2Error):
            parse_outline_into_chapters("No numbered lines here at all.")

    def test_sorted_ascending(self):
        outline = "3. Third\n1. First\n2. Second\n"
        chapters = parse_outline_into_chapters(outline)
        self.assertEqual([c["chapter_number"] for c in chapters], [1, 2, 3])


class TestFileExporter(unittest.TestCase):
    """Tests for build_docx() and build_txt()."""

    def _sample_chapters(self):
        return [
            {"chapter_number": 1, "title": "Introduction", "content": "The start.\n\nIt begins."},
            {"chapter_number": 2, "title": "The Middle",   "content": "Things happen."},
        ]

    def test_build_txt_contains_title(self):
        result = build_txt("My Test Book", self._sample_chapters())
        text = result.decode("utf-8")
        self.assertIn("MY TEST BOOK", text)
        self.assertIn("CHAPTER 1", text)
        self.assertIn("CHAPTER 2", text)

    def test_build_txt_returns_bytes(self):
        result = build_txt("Test", self._sample_chapters())
        self.assertIsInstance(result, bytes)

    def test_build_docx_returns_bytes(self):
        result = build_docx("Test Book", self._sample_chapters())
        self.assertIsInstance(result, bytes)
        # DOCX files start with PK (ZIP header)
        self.assertTrue(result[:2] == b'PK')

    def test_build_txt_chapter_order(self):
        chapters = [
            {"chapter_number": 2, "title": "Second", "content": "B"},
            {"chapter_number": 1, "title": "First",  "content": "A"},
        ]
        text = build_txt("Order Test", chapters).decode("utf-8")
        first_pos  = text.find("CHAPTER 1")
        second_pos = text.find("CHAPTER 2")
        self.assertLess(first_pos, second_pos)


class TestDatabaseQueries(unittest.TestCase):
    """Tests for db/queries.py using an in-memory SQLite database."""

    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def test_insert_and_get_book(self):
        row = queries.insert_book(self.conn, "Test Book", "Some notes")
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "Test Book")
        self.assertEqual(row["status"], BookStatus.PENDING)

        fetched = queries.get_book_by_id(self.conn, row["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["title"], "Test Book")

    def test_get_book_not_found(self):
        result = queries.get_book_by_id(self.conn, "nonexistent-uuid")
        self.assertIsNone(result)

    def test_update_book_status(self):
        row = queries.insert_book(self.conn, "Status Test", "notes")
        queries.update_book_status(self.conn, row["id"], BookStatus.OUTLINE_APPROVED)
        updated = queries.get_book_by_id(self.conn, row["id"])
        self.assertEqual(updated["status"], BookStatus.OUTLINE_APPROVED)

    def test_update_book_outline(self):
        row = queries.insert_book(self.conn, "Outline Test", "notes")
        queries.update_book_outline(self.conn, row["id"], "1. Chapter One", BookStatus.OUTLINE_GENERATED)
        updated = queries.get_book_by_id(self.conn, row["id"])
        self.assertEqual(updated["outline"], "1. Chapter One")

    def test_insert_and_get_chapter(self):
        book = queries.insert_book(self.conn, "Chapter Book", "notes")
        ch = queries.insert_chapter(self.conn, book["id"], 1, "Introduction")
        self.assertIsNotNone(ch)
        self.assertEqual(ch["chapter_number"], 1)
        self.assertEqual(ch["status"], ChapterStatus.PENDING)

    def test_get_previous_summaries_empty_for_chapter_1(self):
        book = queries.insert_book(self.conn, "Sum Book", "notes")
        result = queries.get_previous_summaries(self.conn, book["id"], 1)
        self.assertEqual(result, [])

    def test_get_previous_summaries_returns_correct_chapters(self):
        book = queries.insert_book(self.conn, "Sum Book 2", "notes")
        ch1 = queries.insert_chapter(self.conn, book["id"], 1, "Ch1")
        ch2 = queries.insert_chapter(self.conn, book["id"], 2, "Ch2")
        queries.update_chapter_content(self.conn, ch1["id"], "content", "Summary ch1", ChapterStatus.APPROVED)
        queries.update_chapter_content(self.conn, ch2["id"], "content", "Summary ch2", ChapterStatus.APPROVED)

        # For chapter 3, should return chapters 1 and 2
        summaries = queries.get_previous_summaries(self.conn, book["id"], 3)
        self.assertEqual(len(summaries), 2)
        self.assertEqual(summaries[0]["chapter_number"], 1)
        self.assertEqual(summaries[1]["chapter_number"], 2)

        # For chapter 2, should return only chapter 1
        summaries = queries.get_previous_summaries(self.conn, book["id"], 2)
        self.assertEqual(len(summaries), 1)

    def test_log_event(self):
        book = queries.insert_book(self.conn, "Event Book", "notes")
        queries.log_event(self.conn, book["id"], "test_event", "detail here")
        cur = self.conn.execute(
            "SELECT * FROM event_log WHERE book_id = ?", (book["id"],)
        )
        rows = cur.fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "test_event")

    def test_save_file_locally(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["OUTPUT_DIR"] = tmpdir
            import importlib
            import book_gen.config as cfg
            importlib.reload(cfg)

            path = queries.save_file_locally("test-book-id", "book.txt", b"hello")
            self.assertTrue(Path(path).exists())
            self.assertEqual(Path(path).read_bytes(), b"hello")


# ─────────────────────────────────────────────
# 2. INTEGRATION TESTS (Ollama mocked)
# ─────────────────────────────────────────────

class TestStage1Mocked(unittest.TestCase):
    """Stage 1 integration tests with Ollama mocked."""

    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    @patch("book_gen.llm.ollama_client._call", return_value=_fake_outline())
    def test_stage1_happy_path(self, mock_call):
        """Stage 1 should insert book, generate outline, and approve it."""
        from book_gen.stages.stage1_outline import run

        book_record = {
            "title":                   "The Mocked Journey",
            "notes_on_outline_before": "A story about perseverance.",
            "notes_on_outline_after":  "",
            "status_outline_notes":    "no_notes_needed",
        }
        result = run(self.conn, None, book_record)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], BookStatus.OUTLINE_APPROVED)
        self.assertIn("1.", result["outline"])

    def test_stage1_gate_fails_missing_notes(self):
        """Stage 1 should raise Stage1Error when notes_on_outline_before is empty."""
        from book_gen.stages.stage1_outline import run

        book_record = {
            "title":                   "Empty Notes Book",
            "notes_on_outline_before": "",   # ← gate trigger
            "notes_on_outline_after":  "",
            "status_outline_notes":    "no_notes_needed",
        }
        with patch("book_gen.notifications.email_notifier._send_email"):
            with self.assertRaises(Stage1Error):
                run(self.conn, None, book_record)

    @patch("book_gen.llm.ollama_client._call", return_value=_fake_outline())
    def test_stage1_pauses_when_status_is_no(self, mock_call):
        """Stage 1 should pause and raise Stage1Error when status_outline_notes='no'."""
        from book_gen.stages.stage1_outline import run

        book_record = {
            "title":                   "Paused Book",
            "notes_on_outline_before": "Valid notes.",
            "notes_on_outline_after":  "",
            "status_outline_notes":    "no",  # ← gate trigger
        }
        with patch("book_gen.notifications.email_notifier._send_email"):
            with self.assertRaises(Stage1Error):
                run(self.conn, None, book_record)


class TestStage2Mocked(unittest.TestCase):
    """Stage 2 integration tests with Ollama mocked."""

    def setUp(self):
        self.conn = _make_db()

    def tearDown(self):
        self.conn.close()

    def _insert_approved_book(self) -> str:
        """Insert a book with an approved outline, return book_id."""
        book = queries.insert_book(self.conn, "Stage2 Test Book", "Great notes")
        queries.update_book_outline(self.conn, book["id"], _fake_outline(), BookStatus.OUTLINE_APPROVED)
        return book["id"]

    @patch("book_gen.llm.ollama_client._call")
    def test_stage2_generates_all_chapters(self, mock_call):
        """Stage 2 should generate and approve all 4 chapters from the fake outline."""
        mock_call.side_effect = [
            _fake_chapter_content(),  # chapter 1 content
            _fake_summary(),          # chapter 1 summary
            _fake_chapter_content(),  # chapter 2 content
            _fake_summary(),          # chapter 2 summary
            _fake_chapter_content(),  # chapter 3 content
            _fake_summary(),          # chapter 3 summary
            _fake_chapter_content(),  # chapter 4 content
            _fake_summary(),          # chapter 4 summary
        ]

        from book_gen.stages.stage2_chapters import run

        book_id = self._insert_approved_book()

        # Pre-approve all chapters (simulate reviewer action)
        # Done by setting chapter_notes_status after they're inserted.
        # We monkey-patch insert_chapter to set status to no_notes_needed:
        original_insert = queries.insert_chapter

        def auto_approve_insert(conn, book_id, chapter_number, title):
            ch = original_insert(conn, book_id, chapter_number, title)
            conn.execute(
                "UPDATE chapters SET chapter_notes_status=? WHERE id=?",
                (NotesStatus.NO_NOTES_NEEDED, ch["id"])
            )
            conn.commit()
            return queries.get_chapter(conn, book_id, chapter_number)

        with patch("book_gen.db.queries.insert_chapter", side_effect=auto_approve_insert):
            run(self.conn, None, book_id)

        chapters = queries.get_chapters_for_book(self.conn, book_id)
        self.assertEqual(len(chapters), 4)
        for ch in chapters:
            self.assertEqual(ch["status"], ChapterStatus.APPROVED)


# ─────────────────────────────────────────────
# 3. LIVE SMOKE TEST (requires Ollama running)
# ─────────────────────────────────────────────

class TestLiveOllamaSmokeTest(unittest.TestCase):
    """
    Smoke test that hits the real local Ollama instance.
    Skipped automatically if Ollama is not reachable.
    """

    @classmethod
    def setUpClass(cls):
        import requests as req
        try:
            req.get("http://localhost:11434", timeout=3)
        except Exception:
            raise unittest.SkipTest("Ollama is not running at localhost:11434 — skipping live tests.")

    def test_live_generate_outline(self):
        """Live: Ollama should return a non-empty, numbered outline."""
        from book_gen.llm.ollama_client import generate_outline
        result = generate_outline(
            client=None,
            title="A Brief History of Coffee",
            notes_before="Cover the origins, spread, and cultural impact of coffee in 5 chapters.",
        )
        self.assertIsInstance(result, str)
        self.assertGreater(len(result.strip()), 50)
        # Must contain at least one numbered chapter line
        has_numbered = any(
            line.strip() and line.strip()[0].isdigit()
            for line in result.splitlines()
        )
        self.assertTrue(has_numbered, f"No numbered lines found in: {result[:300]!r}")

    def test_live_summarise_chapter(self):
        """Live: Ollama should return a short paragraph as a chapter summary."""
        from book_gen.llm.ollama_client import summarise_chapter
        result = summarise_chapter(
            client=None,
            chapter_number=1,
            chapter_title="The Origins of Coffee",
            chapter_content=(
                "Coffee was first discovered in Ethiopia around the 9th century. "
                "A goat herder named Kaldi noticed his goats were unusually energetic "
                "after eating berries from a certain tree. He brought the berries to a "
                "local monastery, where monks made a drink from them and found it kept "
                "them alert during long evening prayers. From Ethiopia, coffee spread to "
                "the Arabian Peninsula, where it was cultivated and traded widely. "
                "By the 15th century, coffee was being grown in Yemen and consumed "
                "throughout the Middle East, Persia, Turkey, and North Africa. "
                "Coffeehouses, known as qahveh khaneh, became important centers of "
                "social activity and communication."
            ),
        )
        self.assertIsInstance(result, str)
        self.assertGreater(len(result.split()), 15)

    def test_live_full_stage1(self):
        """Live: Full Stage 1 pipeline against a temporary in-memory DB."""
        conn = _make_db()
        try:
            from book_gen.stages.stage1_outline import run

            book_record = {
                "title":                   "The Art of Brewing Coffee",
                "notes_on_outline_before": (
                    "Write a 5-chapter book about coffee brewing methods: "
                    "French press, espresso, pour-over, cold brew, and Aeropress."
                ),
                "notes_on_outline_after":  "",
                "status_outline_notes":    "no_notes_needed",
            }

            with patch("book_gen.notifications.email_notifier._send_email"):
                result = run(conn, None, book_record)

            self.assertEqual(result["status"], BookStatus.OUTLINE_APPROVED)
            self.assertIsNotNone(result["outline"])
            chapters = parse_outline_into_chapters(result["outline"])
            self.assertGreater(len(chapters), 0)
            print(f"\n✅ Live Stage 1 passed. Outline has {len(chapters)} chapters.")
            print(f"   First chapter: {chapters[0]}")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
