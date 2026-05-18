import { useState, useEffect, useRef } from 'react'
import './Dashboard.css'

function getColumn(squawk, status) {
  if (status === 'LANDED') return 'past'
  if (status === 'CRASHED' || status === 'LOST_SIGNAL') return 'investigation'
  if (['7500', '7600', '7700'].includes(String(squawk))) return 'emergency'
  return 'active'
}

function FlightCard({ id, squawk, status, telemetry }) {
  const col = getColumn(squawk, status)
  const pitch = telemetry?.gyro?.x ?? telemetry?.pitch ?? 0
  const roll  = telemetry?.gyro?.y ?? telemetry?.roll  ?? 0
  return (
    <div className={`card card--${col}`}>
      <div className="card-header">
        <span className="plane-id">{id}</span>
        <span className="squawk-box">{squawk || '1200'}</span>
      </div>
      <div className="card-status">{status || 'ONLINE'}</div>
      <div className="telemetry-grid">
        <div className="data-point">
          <span className="label">PITCH/ROLL</span>
          <span>{telemetry ? `${pitch.toFixed(1)}/${roll.toFixed(1)}` : '--'}</span>
        </div>
        <div className="data-point">
          <span className="label">AUDIO</span>
          <span>{telemetry ? `${(telemetry.audio_level ?? 0).toFixed(0)}dB` : '--'}</span>
        </div>
        <div className="data-point">
          <span className="label">ALT</span>
          <span>{telemetry ? `${(telemetry.altitude ?? 0).toFixed(0)}ft` : '--'}</span>
        </div>
        <div className="data-point">
          <span className="label">STATUS</span>
          <span className="ping">●</span>
        </div>
      </div>
    </div>
  )
}

function Column({ title, color, flights }) {
  return (
    <div className="column">
      <div className="col-title" style={{ color }}>{title}</div>
      {flights.map(([id, data]) => (
        <FlightCard key={id} id={id} {...data} />
      ))}
    </div>
  )
}

export default function Dashboard() {
  const [flights, setFlights]     = useState({})
  const [connected, setConnected] = useState(false)
  const [clock, setClock]         = useState('--:--:-- UTC')
  const wsRef = useRef(null)

  useEffect(() => {
    const id = setInterval(() => setClock(new Date().toUTCString().split(' ')[4] + ' UTC'), 1000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    let cancelled = false

    function connect() {
      if (cancelled) return
      const ws = new WebSocket(`ws://${window.location.host}/ws/dashboard`)
      wsRef.current = ws

      ws.onopen  = () => { if (!cancelled) setConnected(true) }
      ws.onclose = () => {
        if (!cancelled) { setConnected(false); setTimeout(connect, 2000) }
      }
      ws.onmessage = ({ data }) => {
        try {
          const msg = JSON.parse(data)
          setFlights(prev => {
            const next = { ...prev }
            if (msg.type === 'status_update') {
              next[msg.plane_id] = {
                ...next[msg.plane_id],
                squawk: String(msg.state.squawk || '1200'),
                status: msg.state.status || 'ONLINE',
              }
            } else if (msg.type === 'telemetry') {
              next[msg.plane_id] = {
                ...next[msg.plane_id],
                squawk: String(msg.squawk || '1200'),
                status: next[msg.plane_id]?.status || 'ONLINE',
                telemetry: msg.data,
              }
            }
            return next
          })
        } catch (_) {}
      }
    }

    connect()
    return () => { cancelled = true; wsRef.current?.close() }
  }, [])

  const entries = Object.entries(flights)
  const cols = {
    active:        entries.filter(([, d]) => getColumn(d.squawk, d.status) === 'active'),
    emergency:     entries.filter(([, d]) => getColumn(d.squawk, d.status) === 'emergency'),
    investigation: entries.filter(([, d]) => getColumn(d.squawk, d.status) === 'investigation'),
    past:          entries.filter(([, d]) => getColumn(d.squawk, d.status) === 'past'),
  }

  return (
    <div className="dashboard">
      <div className="dash-header">
        <div>
          <h1>SOC // BLACK BOX MONITOR</h1>
          <small className={connected ? 'text-online' : 'text-offline'}>
            {connected ? '● SYSTEM ONLINE' : '● DISCONNECTED'}
          </small>
        </div>
        <h2 className="clock">{clock}</h2>
      </div>
      <div className="dashboard-columns">
        <Column title="Active Flights"    color="#10b981" flights={cols.active} />
        <Column title="⚠ Emergency"       color="#ef4444" flights={cols.emergency} />
        <Column title="🔍 Investigation"  color="#f59e0b" flights={cols.investigation} />
        <Column title="Past Flights"      color="#475569" flights={cols.past} />
      </div>
    </div>
  )
}
