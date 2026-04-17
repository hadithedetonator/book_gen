import { useState, useEffect } from 'react'
import './App.css'

const API_BASE = "http://localhost:8000"

function App() {
  const [books, setBooks] = useState([])
  const [activeBookIds, setActiveBookIds] = useState([])
  const [loading, setLoading] = useState(false)
  const [processingId, setProcessingId] = useState(null)
  const [logs, setLogs] = useState("")

  const fetchBooks = async () => {
    try {
      const resp = await fetch(`${API_BASE}/books`)
      const data = await resp.json()
      setBooks(data)
    } catch (err) {
      console.error("Failed to fetch books", err)
    }
  }

  const fetchActiveTasks = async () => {
    try {
      const resp = await fetch(`${API_BASE}/active-tasks`)
      const data = await resp.json()
      setActiveBookIds(data.active_book_ids)
    } catch (err) {
      console.error("Failed to fetch active tasks", err)
    }
  }

  const fetchLogs = async () => {
    try {
      const resp = await fetch(`${API_BASE}/logs`)
      const data = await resp.json()
      setLogs(data.logs)
    } catch (err) {
      console.error("Failed to fetch logs", err)
    }
  }

  useEffect(() => {
    fetchBooks()
    fetchLogs()
    fetchActiveTasks()
    const bInterval = setInterval(fetchBooks, 3000)
    const lInterval = setInterval(fetchLogs, 2000)
    const tInterval = setInterval(fetchActiveTasks, 2000)
    return () => {
      clearInterval(bInterval)
      clearInterval(lInterval)
      clearInterval(tInterval)
    }
  }, [])

  const runExcel = async () => {
    setLoading(true)
    try {
      await fetch(`${API_BASE}/run-excel`, { method: 'POST' })
    } catch (err) {
      setLogs(prev => prev + "\nError: " + err.message)
    }
    setTimeout(() => setLoading(false), 2000)
  }

  const runStage = async (bookId, stage) => {
    setProcessingId(`${bookId}-${stage}`)
    try {
      await fetch(`${API_BASE}/run-stage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ book_id: bookId, stage })
      })
    } catch (err) {
      setLogs(prev => prev + "\nError: " + err.message)
    }
    setTimeout(() => setProcessingId(null), 3000)
  }

  const clearLogs = async () => {
    await fetch(`${API_BASE}/logs`, { method: 'DELETE' })
    setLogs("")
  }

  return (
    <div className="container">
      <header>
        <h1>📚 Book Gen <span>Control Center</span></h1>
        <div className="actions">
          <button 
            onClick={runExcel} 
            disabled={loading}
            className="btn-primary"
          >
            {loading ? "Started..." : "Process books.xlsx"}
          </button>
        </div>
      </header>

      <main>
        <div className="card">
          <table className="book-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Status</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {books.map(book => (
                <tr key={book.id}>
                  <td><strong>{book.title}</strong></td>
                  <td>
                    <span className={`badge ${book.status}`}>
                      {book.status.replace(/_/g, ' ')}
                    </span>
                  </td>
                  <td>{new Date(book.created_at).toLocaleDateString()}</td>
                  <td className="row-actions-container">
                    <div className="row-actions">
                      <button 
                        disabled={activeBookIds.includes(book.id) || !['pending', 'error'].includes(book.status)}
                        onClick={() => runStage(book.id, "1")}
                        title="Phase 1: Generate AI Outline"
                      >
                        {activeBookIds.includes(book.id) ? "..." : "Outline"}
                      </button>
                      
                      <button 
                        disabled={activeBookIds.includes(book.id) || book.status !== 'outline_approved'}
                        onClick={() => runStage(book.id, "2")}
                        title="Phase 2: Write all chapters"
                      >
                        {activeBookIds.includes(book.id) ? "..." : "Chapters"}
                      </button>
                      
                      <button 
                        disabled={activeBookIds.includes(book.id) || book.status !== 'chapters_generated'}
                        onClick={() => runStage(book.id, "3")}
                        title="Phase 3: Compile to DOCX/TXT"
                      >
                        {activeBookIds.includes(book.id) ? "..." : "Compile"}
                      </button>
                      
                      <button 
                        disabled={activeBookIds.includes(book.id) || book.status === 'complete'}
                        onClick={() => runStage(book.id, "all")} 
                        className="btn-all"
                        title="Run remaining phases"
                      >
                        {activeBookIds.includes(book.id) ? "..." : "Run All"}
                      </button>
                    </div>

                    {(book.status === 'chapters_in_progress' || activeBookIds.includes(book.id)) && (
                      <div className="progress-container">
                        <div className="progress-bar-indro"></div>
                        <span>{book.status === 'chapters_in_progress' ? "Writing chapters..." : "AI is working..."}</span>
                      </div>
                    )}
                    {book.status === 'compiling' && (
                      <div className="progress-container">
                        <div className="progress-bar-indro"></div>
                        <span>Compiling final document...</span>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
              {books.length === 0 && (
                <tr>
                  <td colSpan="4" className="empty">No books found. Please process an Excel file.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="card log-section">
          <div className="log-header">
            <h3>Terminal logs</h3>
            <button onClick={clearLogs} className="btn-clear">Clear Logs</button>
          </div>
          <pre className="logs">{logs || "No logs yet. Trigger an action to see output..."}</pre>
        </div>
      </main>
    </div>
  )
}

export default App
