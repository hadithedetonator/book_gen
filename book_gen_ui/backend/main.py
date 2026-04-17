import subprocess
import os
import sqlite3
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional

app = FastAPI(title="Book Gen API Wrapper")

# Enable CORS for the React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
# main.py is in book_gen/book_gen_ui/backend/
# We want PROJECT_ROOT to be the root book_gen/ folder
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(BACKEND_DIR))
WORKSPACE_ROOT = os.path.dirname(PROJECT_ROOT) # This is where the 'book_gen' package lives
DB_PATH = os.path.join(PROJECT_ROOT, "data", "books.db")
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "bin", "python3")
LOG_FILE = os.path.join(PROJECT_ROOT, "ui_run.log")

# Track active book tasks to provide accurate UI feedback
active_tasks = set()

class RunStageRequest(BaseModel):
    book_id: str
    stage: str

class BookResponse(BaseModel):
    id: str
    title: str
    status: str
    created_at: str

def run_command_in_background(args: List[str], book_id: Optional[str] = None):
    """Run CLI in background and append to LOG_FILE."""
    if book_id:
        active_tasks.add(book_id)
        
    try:
        cmd = [VENV_PYTHON, "-m", "book_gen.main"] + args
        with open(LOG_FILE, "a") as f:
            f.write(f"\n--- RUNNING: {' '.join(cmd)} ---\n")
            f.flush()
            subprocess.run(
                cmd, 
                cwd=PROJECT_ROOT, 
                stdout=f, 
                stderr=f,
                text=True,
                env={**os.environ, "PYTHONPATH": WORKSPACE_ROOT}
            )
            f.write("\n--- FINISHED ---\n")
    finally:
        if book_id:
            active_tasks.discard(book_id)

@app.get("/books", response_model=List[BookResponse])
def get_books():
    if not os.path.exists(DB_PATH):
        return []
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT id, title, status, created_at FROM books ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

@app.post("/run-excel")
def trigger_excel(background_tasks: BackgroundTasks, file_path: Optional[str] = "books.xlsx"):
    full_path = os.path.join(PROJECT_ROOT, file_path)
    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Excel file not found")
    
    background_tasks.add_task(run_command_in_background, ["--file", full_path])
    return {"message": "Excel processing started in background"}

@app.post("/run-stage")
def trigger_stage(req: RunStageRequest, background_tasks: BackgroundTasks):
    # Check if already running
    if req.book_id in active_tasks:
        return {"message": "Task already running for this book"}
        
    background_tasks.add_task(run_command_in_background, ["--book-id", req.book_id, "--stage", req.stage], req.book_id)
    return {"message": f"Stage {req.stage} started in background"}

@app.get("/active-tasks")
def get_active_tasks():
    return {"active_book_ids": list(active_tasks)}

@app.get("/logs")
def get_logs():
    if not os.path.exists(LOG_FILE):
        return {"logs": ""}
    with open(LOG_FILE, "r") as f:
        # Return last 2000 lines
        lines = f.readlines()
        return {"logs": "".join(lines[-2000:])}

@app.delete("/logs")
def clear_logs():
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)
    return {"message": "Logs cleared"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
