import { useState, useEffect } from 'react'
import './Logs.css'

const CAT_CONFIG = {
  security:   { title: '☠️ SECURITY THREATS (7500)',  order: 1 },
  emergency:  { title: '⚠ GENERAL EMERGENCY (7700)',  order: 2 },
  crash:      { title: '🔥 CONFIRMED CRASHES',        order: 3 },
  radio_fail: { title: '🔇 RADIO FAILURES (7600)',     order: 4 },
  lost:       { title: '❓ SIGNAL LOST',              order: 5 },
  normal:     { title: '✈ STANDARD OPERATIONS',       order: 6 },
}

function parseFilename(filename) {
  const parts = filename.split('_')
  if (parts.length >= 3) {
    const d = parts[0], t = parts[1]
    return {
      time: `${d.slice(0,4)}-${d.slice(4,6)}-${d.slice(6,8)} ${t.slice(0,2)}:${t.slice(2,4)}`,
      id:   parts.slice(2).join('_').replace('.jsonl', ''),
    }
  }
  return { time: 'UNKNOWN DATE', id: filename }
}

function LogEntry({ category, filename, onDelete }) {
  const [result,   setResult]   = useState(null)
  const [tampered, setTampered] = useState(false)
  const { time, id } = parseFilename(filename)

  async function verify() {
    setResult({ type: 'loading', text: 'Running cryptographic check...' })
    try {
      const data = await fetch(`/api/verify/${category}/${filename}`).then(r => r.json())
      if (data.status === 'VALID') {
        setResult({ type: 'valid', text: `✅ INTEGRITY VERIFIED\nHash matches ledger signature.\n${data.timestamp}` })
      } else if (data.status === 'TAMPERED') {
        setResult({ type: 'tampered', text: `🚨 FILE TAMPERED\nHash mismatch detected.\nOrig: ${data.original_hash?.slice(0,8)}...\nCurr: ${data.current_hash?.slice(0,8)}...` })
      } else {
        setResult({ type: 'unknown', text: `⚠ UNKNOWN: ${data.message}` })
      }
    } catch {
      setResult({ type: 'unknown', text: 'Error contacting server.' })
    }
  }

  async function tamperLog() {
    if (!confirm(`⚠ DEMO: Inject fake altitude into "${filename}"?\nThis will corrupt the file so VERIFY detects tampering.`)) return
    try {
      const data = await fetch(`/api/tamper/log/${category}/${filename}`, { method: 'POST' }).then(r => r.json())
      if (data.tampered) {
        setTampered(true)
        setResult({ type: 'tampered', text: `☠ DATA INJECTED\nField: altitude → 99999 (was: ${data.original_value})\nRun VERIFY to detect the tampering.` })
      } else {
        setResult({ type: 'unknown', text: `Error: ${data.error || 'Unknown error'}` })
      }
    } catch (e) {
      setResult({ type: 'unknown', text: `Network error: ${e}` })
    }
  }

  async function deleteLog() {
    if (!confirm(`PERMANENTLY DELETE RECORD?\n${filename}`)) return
    try {
      const res = await fetch(`/api/logs/${category}/${filename}`, { method: 'DELETE' })
      if (res.ok) onDelete(filename)
      else alert('Delete failed.')
    } catch { alert('Network error.') }
  }

  return (
    <li className="log-entry">
      <div className="row-top">
        <div className="file-info">
          <span className="file-plane-id">{id}</span>
          <span className="timestamp">{time}</span>
        </div>
        <div className="btn-group">
          <button className="btn-verify"  onClick={verify}>🛡️ VERIFY</button>
          <button className={`btn-tamper ${tampered ? 'done' : ''}`} onClick={tamperLog} disabled={tampered}>
            {tampered ? '☠ TAMPERED' : '☠ TAMPER'}
          </button>
          <button className="btn-delete" onClick={deleteLog}>✖</button>
        </div>
      </div>
      {result && (
        <div className={`verify-box ${result.type}`}>
          <pre>{result.text}</pre>
        </div>
      )}
    </li>
  )
}

function CategoryPanel({ catKey, files: initial }) {
  const [files, setFiles] = useState(initial)
  const config = CAT_CONFIG[catKey] || { title: catKey.toUpperCase() }
  return (
    <div className={`section cat-${catKey}`}>
      <div className="section-header">
        <span>{config.title}</span>
        <span className="count-badge">{files.length}</span>
      </div>
      <ul>
        {files.length === 0
          ? <li className="empty-msg">No records found.</li>
          : files.map(f => (
              <LogEntry
                key={f}
                category={catKey}
                filename={f}
                onDelete={name => setFiles(prev => prev.filter(x => x !== name))}
              />
            ))
        }
      </ul>
    </div>
  )
}

export default function Logs() {
  const [categories, setCategories] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetch('/api/logs')
      .then(r => r.json())
      .then(data => {
        const sorted = Object.entries(data)
          .sort(([a], [b]) => (CAT_CONFIG[a]?.order ?? 99) - (CAT_CONFIG[b]?.order ?? 99))
        setCategories(sorted)
      })
      .catch(e => setError(String(e)))
  }, [])

  if (error)      return <div className="logs-page"><p className="status-msg">CONNECTION ERROR: {error}</p></div>
  if (!categories) return <div className="logs-page"><p className="status-msg">ACCESSING SECURE LEDGER...</p></div>

  return (
    <div className="logs-page">
      <div className="archive-grid">
        {categories.map(([catKey, files]) => (
          <CategoryPanel key={catKey} catKey={catKey} files={files} />
        ))}
      </div>
    </div>
  )
}
