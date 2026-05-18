import { useState, useEffect, useRef } from 'react'
import './Plane.css'

export default function Plane() {
  const [planeId]   = useState(() => 'FLIGHT-' + Math.floor(Math.random() * 899 + 100))
  const [flying,     setFlying]     = useState(false)
  const [connected,  setConnected]  = useState(false)
  const [squawk,     setSquawk]     = useState('1200')
  const [pitch,      setPitch]      = useState(0)
  const [roll,       setRoll]       = useState(0)
  const [noise,      setNoise]      = useState(0)
  const wsRef       = useRef(null)
  const liveRef     = useRef({ heading: 0, pitch: 0, roll: 0, noise: 0, squawk: '1200' })

  function handleMotion(event) {
    liveRef.current.pitch   = event.beta  || 0
    liveRef.current.roll    = event.gamma || 0
    liveRef.current.heading = event.alpha || 0
    setPitch(Math.round(event.beta  || 0))
    setRoll( Math.round(event.gamma || 0))
  }

  function updateSquawk(val) {
    const s = String(val)
    liveRef.current.squawk = s
    setSquawk(s)
  }

  function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/plane/${planeId}`)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      const id = setInterval(() => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) { clearInterval(id); return }
        const t = liveRef.current
        wsRef.current.send(JSON.stringify({
          squawk: t.squawk, altitude: 30000, speed: 450,
          heading: t.heading, latitude: 0, longitude: 0,
          pitch: t.pitch, roll: t.roll, audio_level: t.noise,
        }))
      }, 200)
    }
    ws.onclose = () => setConnected(false)
  }

  async function startFlight() {
    if (typeof DeviceOrientationEvent !== 'undefined' && typeof DeviceOrientationEvent.requestPermission === 'function') {
      try {
        const perm = await DeviceOrientationEvent.requestPermission()
        if (perm !== 'granted') { alert('Motion permission required!'); return }
      } catch (e) { console.error(e) }
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const ctx = new (window.AudioContext || window.webkitAudioContext)()
      const analyser = ctx.createAnalyser()
      ctx.createMediaStreamSource(stream).connect(analyser)
      analyser.fftSize = 64
      const buf = new Uint8Array(analyser.frequencyBinCount)
      setInterval(() => {
        analyser.getByteFrequencyData(buf)
        const avg = buf.reduce((a, b) => a + b, 0) / buf.length
        const n = (avg / 128) * 100
        liveRef.current.noise = n
        setNoise(Math.round(n))
      }, 100)
    } catch (e) { console.warn('Audio unavailable', e) }

    window.addEventListener('deviceorientation', handleMotion)
    connectWebSocket()
    setFlying(true)
  }

  function endFlight() {
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'disconnect' }))
      wsRef.current.close()
    }
    window.location.reload()
  }

  useEffect(() => () => wsRef.current?.close(), [])

  return (
    <div className="plane-page">
      <div className="plane-header">
        <h1>FLIGHT DECK</h1>
        <div className="plane-id-label">{planeId}</div>
      </div>

      <div className="plane-panel">
        <div className={`conn-status ${connected ? 'blink' : ''}`} style={{ color: connected ? '#00ff00' : '#666' }}>
          {connected ? 'TRANSMITTING' : flying ? 'LINK LOST' : 'OFFLINE'}
        </div>

        <div className="transponder">
          <label>TRANSPONDER (SQUAWK)</label>
          <input
            type="number"
            value={squawk}
            onChange={e => updateSquawk(e.target.value)}
          />
        </div>

        <div className="data-row"><span>PITCH</span><span>{pitch}°</span></div>
        <div className="data-row"><span>ROLL</span> <span>{roll}°</span></div>
        <div className="data-row"><span>NOISE</span><span>{noise}%</span></div>

        {!flying ? (
          <div className="pre-flight">
            <button className="btn-connect" onClick={startFlight}>🔌 CONNECT SYSTEMS</button>
            <small>Requires Sensor &amp; Mic Permissions</small>
          </div>
        ) : (
          <div className="in-flight">
            <div className="emergency-pad">
              <button className="btn-7600" onClick={() => updateSquawk(7600)}>7600<br />RADIO</button>
              <button className="btn-7700" onClick={() => updateSquawk(7700)}>7700<br />EMERG</button>
              <button className="btn-7500" onClick={() => updateSquawk(7500)}>7500<br />HIJACK</button>
            </div>
            <button className="btn-disconnect" onClick={endFlight}>END FLIGHT</button>
          </div>
        )}
      </div>
    </div>
  )
}
