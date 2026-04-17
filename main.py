"""
main.py
-------
CLI entry point for the Automated Book Generation System (local-first edition).

All LLM calls use local Ollama. All data stored in SQLite. No cloud dependencies.

Usage examples:
  # Initialise the local database (run once)
  python main.py --init-db

  # Process all rows from Excel (Stage 1 only, each book gets a new DB row)
  python main.py --file books.xlsx

  # Resume a specific book at a specific stage
  python main.py --book-id <uuid> --stage 1
  python main.py --book-id <uuid> --stage 2
  python main.py --book-id <uuid> --stage 3

  # Full pipeline for a specific existing book (stages 2 + 3)
  python main.py --book-id <uuid> --stage all
"""

import argparse
import logging
import sys

import book_gen.config as cfg
from book_gen.db.client import get_connection, init_db
from book_gen.utils.excel_reader import read_books, ExcelReadError
from book_gen.stages.stage1_outline import run as stage1_run, run_for_existing_book as stage1_resume
from book_gen.stages.stage2_chapters import run as stage2_run
from book_gen.stages.stage3_compile import run as stage3_run
from book_gen.stages.stage1_outline import Stage1Error
from book_gen.stages.stage2_chapters import Stage2Error
from book_gen.stages.stage3_compile import Stage3Error
from book_gen.constants import ExcelColumn


def _configure_logging() -> None:
    """
    Configure root logger using the LOG_LEVEL env var (default: INFO).

    Returns:
        None
    """
    log_level = getattr(logging, cfg.LOG_LEVEL, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def _build_parser() -> argparse.ArgumentParser:
    """
    Build and return the CLI argument parser.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="book_gen",
        description=(
            "Automated Book Generation System — "
            "local-first, LLM-powered, human-in-the-loop book pipeline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python main.py --init-db\n"
            "  python main.py --file books.xlsx\n"
            "  python main.py --book-id <uuid> --stage 2\n"
            "  python main.py --book-id <uuid> --stage all\n"
        ),
    )
    parser.add_argument(
        "--init-db",
        action="store_true",
        help="Initialise the SQLite database schema and exit.",
    )
    parser.add_argument(
        "--file",
        metavar="PATH",
        help="Path to the .xlsx input file. Triggers Stage 1 for every row.",
    )
    parser.add_argument(
        "--book-id",
        metavar="UUID",
        help="UUID of an existing book in the local DB. Required for --stage 2 / 3 / all.",
    )
    parser.add_argument(
        "--stage",
        choices=["1", "2", "3", "all"],
        default="1",
        help=(
            "Pipeline stage to execute. "
            "'1' = outline, '2' = chapters, '3' = compile, "
            "'all' = stages 2 then 3 for an existing book. "
            "Default: '1'."
        ),
    )
    return parser


def _run_from_excel(file_path: str, db) -> None:
    """
    Read every row from the Excel file and run Stage 1 for each book.

    Books that fail the gate are skipped (Stage1Error is caught and logged).
    Execution continues with the next row.

    Args:
        file_path: Path to the .xlsx file.
        db:        Open SQLite connection.

    Returns:
        None
    """
    log = logging.getLogger(__name__)
    try:
        rows = list(read_books(file_path))
    except ExcelReadError as exc:
        log.error("Cannot read Excel file: %s", exc)
        sys.exit(1)

    if not rows:
        log.warning("No valid rows found in Excel file '%s'. Nothing to do.", file_path)
        return

    log.info("Found %d book row(s) in '%s'.", len(rows), file_path)

    success = 0
    skipped = 0
    for row in rows:
        title = row.get(ExcelColumn.TITLE, "<unknown>")
        try:
            result = stage1_run(db, None, row)
            log.info("Stage 1 succeeded: title='%s' book_id=%s", title, result["id"])
            success += 1
        except Stage1Error as exc:
            log.warning("Stage 1 skipped/paused for '%s': %s", title, exc)
            skipped += 1

    log.info(
        "Excel processing complete. Succeeded=%d Skipped/Paused=%d.",
        success, skipped
    )


def _run_stage_for_book(stage: str, book_id: str, db) -> None:
    """
    Run one or more stages for an existing book identified by its UUID.

    Args:
        stage:   One of "1", "2", "3", "all".
        book_id: UUID from the local SQLite books table.
        db:      Open SQLite connection.

    Returns:
        None
    """
    log = logging.getLogger(__name__)

    try:
        if stage == "1":
            result = stage1_resume(db, None, book_id)
            log.info("Stage 1 complete: %s", result)

        elif stage == "2":
            stage2_run(db, None, book_id)
            log.info("Stage 2 complete.")

        elif stage == "3":
            stage3_run(db, None, book_id)
            log.info("Stage 3 complete.")

        elif stage == "all":
            stage2_run(db, None, book_id)
            log.info("Stage 2 complete — moving to Stage 3.")
            stage3_run(db, None, book_id)
            log.info("Stage 3 complete.")

    except (Stage1Error, Stage2Error, Stage3Error) as exc:
        log.warning("Pipeline paused at stage %s for book_id=%s: %s", stage, book_id, exc)
        sys.exit(2)
    except Exception as exc:
        log.error("Unexpected error at stage %s for book_id=%s: %s", stage, book_id, exc)
        sys.exit(1)


def main() -> None:
    """
    Parse CLI arguments, initialise dependencies, and dispatch to the correct stage.

    Returns:
        None
    """
    _configure_logging()
    log = logging.getLogger(__name__)

    parser = _build_parser()
    args   = parser.parse_args()

    # ── --init-db shortcut ────────────────────────────────────────────────────
    if args.init_db:
        init_db()
        log.info("Database initialised at: %s", cfg.SQLITE_DB_PATH)
        return

    # ── Validate argument combinations ────────────────────────────────────────
    if not args.file and not args.book_id:
        parser.error("Provide --init-db, --file <path.xlsx>, or --book-id <uuid>.")

    if args.file and args.book_id:
        parser.error("--file and --book-id are mutually exclusive.")

    # ── Ensure DB is initialised ──────────────────────────────────────────────
    init_db()
    db = get_connection()

    log.info(
        "Starting Book Generation System — stage=%s file=%s book_id=%s model=%s",
        args.stage, args.file, args.book_id, cfg.OLLAMA_MODEL
    )

    try:
        if args.file:
            _run_from_excel(args.file, db)
        else:
            _run_stage_for_book(args.stage, args.book_id, db)
    finally:
        db.close()

    log.info("Pipeline run complete.")


if __name__ == "__main__":
    main()
