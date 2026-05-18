import { useState, useEffect, useCallback } from 'react'
import './Blockchain.css'

function squawkBadge(label) {
  const m = label?.match(/squawk=(\d+)/)
  const code = m ? m[1] : '1200'
  return { code, text: { '7500': 'HIJACK', '7700': 'EMERGENCY', '7600': 'RADIO FAIL', '1200': 'NORMAL' }[code] ?? code }
}

function planeName(label) {
  const m = label?.match(/plane=([^\s|]+)/)
  return m ? m[1] : '—'
}

function shortHash(h) {
  return h ? `${h.slice(0,10)}…${h.slice(-6)}` : '—'
}

function TxModal({ anchor, onClose }) {
  const [data, setData] = useState(null)

  useEffect(() => {
    if (!anchor) return
    setData(null)
    Promise.all([
      fetch(`/api/eth/anchor/${anchor.tx_hash}`).then(r => r.json()),
      fetch('/api/eth/rpc', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ method: 'eth_getTransactionByHash', params: [anchor.tx_hash] }),
      }).then(r => r.json()).catch(() => null),
    ]).then(([chain, rpc]) => setData({
      '=== LOCAL RECORD (our log) ===': anchor,
      '=== ON-CHAIN DATA (via Ganache) ===': chain,
      '=== RAW JSON-RPC (eth_getTransactionByHash) ===': rpc ?? '(not available)',
    })).catch(e => setData(`Error: ${e}`))
  }, [anchor])

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <h3>TRANSACTION DETAIL — ON-CHAIN VERIFICATION</h3>
        <div className="modal-body">
          <pre>{data ? JSON.stringify(data, null, 2) : 'Fetching from chain…'}</pre>
        </div>
        <button className="modal-close" onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

export default function Blockchain() {
  const [nodeStatus,   setNodeStatus]   = useState(null)
  const [anchors,      setAnchors]      = useState([])
  const [chainBox,     setChainBox]     = useState(null)
  const [filesBox,     setFilesBox]     = useState(null)
  const [selectedTx,   setSelectedTx]   = useState(null)

  const load = useCallback(async () => {
    try { setNodeStatus(await fetch('/api/eth/status').then(r => r.json())) } catch {}
    try {
      const data = await fetch('/api/eth/anchors').then(r => r.json())
      setAnchors(data.anchors ?? [])
    } catch {}
  }, [])

  useEffect(() => {
    load()
    const id = setInterval(load, 10000)
    return () => clearInterval(id)
  }, [load])

  async function verifyChain() {
    setChainBox({ type: 'unknown', title: 'BLOCKCHAIN CHAIN', text: 'Re-fetching every anchor from Ganache…' })
    setFilesBox({ type: 'unknown', title: 'FILE LOGS vs CHAIN', text: 'Hashing files on disk…' })
    try {
      const data = await fetch('/api/chain/verify').then(r => r.json())

      // -- Box 1: chain itself (anchors on Ganache) ------------------------
      const breaks = (data.breaks ?? []).map(b =>
        `  tx ${(b.tx_hash ?? '').slice(0,18)}…  ${b.reason}\n    expected: ${b.expected ?? ''}\n    found   : ${b.found ?? ''}`
      ).join('\n')
      const errored = (data.errored_anchors ?? []).slice(0, 10).map(e =>
        `  • ${e.label ?? '(no label)'}\n      queued: ${e.queued_at ?? '—'}\n      error : ${e.error ?? '(unknown)'}`
      ).join('\n')
      const erroredMore = (data.errored ?? 0) > 10 ? `\n  …and ${data.errored - 10} more.` : ''
      const chainOk = (data.breaks?.length ?? 0) === 0 && (data.errored ?? 0) === 0
      const chainLines = [
        chainOk ? `✅ CHAIN INTACT` : `🚨 CHAIN ISSUE`,
        `  Anchors on Ganache : ${data.entries}`,
        `  On-chain mismatches: ${data.breaks?.length ?? 0}`,
        `  Failed to anchor   : ${data.errored ?? 0}`,
      ]
      if (breaks)   chainLines.push('', '— ON-CHAIN MISMATCHES —', breaks)
      if (errored)  chainLines.push('', '— EVENTS THAT NEVER REACHED THE CHAIN —', errored + erroredMore)
      if (data.error) chainLines.push('', data.error)
      if (chainOk && !data.error) {
        chainLines.push('', 'Every anchor we know about is still on Ganache with the same SHA-256.')
      }
      setChainBox({
        type:  chainOk ? 'intact' : 'broken',
        title: 'BLOCKCHAIN CHAIN',
        text:  chainLines.join('\n'),
      })

      // -- Box 2: disk files vs their on-chain anchors ---------------------
      const tampered = (data.tampered_files ?? []).map(f =>
        `  • ${f.category}/${f.filename}\n      on-chain : ${(f.expected_hash ?? '').slice(0,16)}…\n      on-disk  : ${(f.current_hash ?? '').slice(0,16)}…`
      ).join('\n')
      const missing = (data.missing_files ?? []).map(f =>
        `  • ${f.category ?? '?'}/${f.filename}\n      on-chain : ${(f.expected_hash ?? '').slice(0,16)}…  (file no longer on disk)`
      ).join('\n')
      const filesOk = (data.tampered_files?.length ?? 0) === 0 && (data.missing_files?.length ?? 0) === 0
      const filesLines = [
        filesOk ? `✅ FILES MATCH CHAIN` : `🚨 FILE INTEGRITY ISSUE`,
        `  Tampered files: ${data.tampered_files?.length ?? 0}`,
        `  Missing files : ${data.missing_files?.length ?? 0}`,
      ]
      if (tampered) filesLines.push('', '— FILES TAMPERED AFTER ARCHIVE —', tampered)
      if (missing)  filesLines.push('', '— ARCHIVED FILES NO LONGER ON DISK —', missing)
      if (filesOk) {
        filesLines.push('', 'Every archived flight file on disk still matches its on-chain hash.')
      }
      setFilesBox({
        type:  filesOk ? 'intact' : 'broken',
        title: 'FILE LOGS vs CHAIN',
        text:  filesLines.join('\n'),
      })
    } catch (e) {
      setChainBox({ type: 'unknown', title: 'BLOCKCHAIN CHAIN',  text: `Network error: ${e}` })
      setFilesBox({ type: 'unknown', title: 'FILE LOGS vs CHAIN', text: `Network error: ${e}` })
    }
  }

  const sorted   = [...anchors].reverse()
  const maxBlock = sorted.length ? Math.max(...sorted.map(a => a.block ?? 0)) : 0

  return (
    <div className="blockchain-page">
      <div className="status-bar">
        <div>Node: <span className="sv">
          {nodeStatus
            ? <><span className={`dot ${nodeStatus.connected ? 'green' : 'red'}`} />{nodeStatus.node}</>
            : '…'}
        </span></div>
        <div>Chain ID: <span className="sv">{nodeStatus?.chain_id ?? '…'}</span></div>
        <div>Total anchors: <span className="sv">{anchors.length}</span></div>
        <div>Latest block: <span className="sv">{maxBlock || '…'}</span></div>
      </div>

      <div className="tamper-panel">
        <h2>🔍 VERIFY ON-CHAIN ANCHORS</h2>
        <div className="tamper-body">
          <p style={{ flex: 1, margin: 0 }}>
            Re-fetches every anchored transaction from Ganache and confirms its SHA-256
            still matches what we recorded. The chain itself is immutable — this check
            catches data loss (e.g. Ganache restarted without its persistent volume).
          </p>
          <button className="tamper-btn" onClick={verifyChain}>🔍 VERIFY CHAIN</button>
        </div>
      </div>

      {(chainBox || filesBox) && (
        <div className="verify-grid">
          {chainBox && (
            <div className={`chain-result ${chainBox.type}`}>
              <div className="chain-result-title">{chainBox.title}</div>
              <pre>{chainBox.text}</pre>
            </div>
          )}
          {filesBox && (
            <div className={`chain-result ${filesBox.type}`}>
              <div className="chain-result-title">{filesBox.title}</div>
              <pre>{filesBox.text}</pre>
            </div>
          )}
        </div>
      )}

      <div className="bc-main">
        <h2>
          ANCHORED TRANSACTIONS
          <button className="refresh-btn" onClick={load}>↻ Refresh</button>
        </h2>
        {sorted.length === 0 ? (
          <p className="empty-msg">No anchors yet — fly some planes first.</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>BLOCK</th><th>TYPE</th><th>FLIGHT</th><th>TX HASH</th>
                <th>SHA-256 (truncated)</th><th>STATUS</th><th>TIMESTAMP</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((a, i) => {
                const badge = squawkBadge(a.label)
                return (
                  <tr key={i} onClick={() => setSelectedTx(a)}>
                    <td className="mono">{a.block ?? '—'}</td>
                    <td><span className={`badge badge-${badge.code}`}>{badge.text}</span></td>
                    <td>{planeName(a.label)}</td>
                    <td className="mono tx-hash">{shortHash(a.tx_hash)}</td>
                    <td className="mono hash-cell">{a.hash ? `${a.hash.slice(0,32)}…` : '—'}</td>
                    <td><span className={`badge badge-${a.status === 'ANCHORED' ? 'ok' : 'err'}`}>{a.status}</span></td>
                    <td className="mono ts">{a.queued_at ? a.queued_at.slice(0,19).replace('T',' ') : '—'}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {selectedTx && <TxModal anchor={selectedTx} onClose={() => setSelectedTx(null)} />}
    </div>
  )
}
