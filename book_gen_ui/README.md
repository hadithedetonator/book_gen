# Book Gen Dashboard

This is a modern React + FastAPI web interface for the Automated Book Generation System. It allows you to visualize your books, process Excel files, and trigger pipeline stages with a single click.

## Prerequisites

1. **Python 3.10+** (already set up in the root directory)
2. **Node.js 18+** (installed via Homebrew at `/opt/homebrew/bin/node`)

## Setup & Running

### 1. Start the Backend
From the `book_gen_ui/backend` directory:
```bash
# Install FastAPI and Uvicorn in the project venv
source ../../.venv/bin/activate
pip install fastapi uvicorn

# Run the server
python main.py
```
The backend will run at `http://localhost:8000`.

### 2. Start the Frontend
From the `book_gen_ui/frontend` directory:
```bash
# Install dependencies
npm install

# Start the dev server
npm run dev
```
The UI will be available at the URL shown in your terminal (usually `http://localhost:5173`).

## Features

- **Excel Processing**: Upload or trigger `test_books.xlsx` with one click.
- **Book Registry**: See all books stored in your local SQLite database.
- **Stage Management**: Individually run Stage 1 (Outline), Stage 2 (Chapters), or Stage 3 (Compilation).
- **Log Monitor**: Real-time terminal output mapping directly from the CLI runs.
- **Automatic Refresh**: The book list polls every 5 seconds to show progress.
