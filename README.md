# Streaming HTTP Server + Dashboard (Early Stage)

This project provides:
- `app.py`: Flask + Flask-SocketIO server that accepts device data via HTTP POST (`/ingest`) or Socket.IO (`/device` namespace).
- A professional-looking (prototype) dashboard at `/` to view devices, live feed, ping results, and simple charts.

## Quick start

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
# Open http://localhost:5000 in your browser
```

## Device integration examples

### Simple HTTP POST (works from Android using e.g. OkHttp)
POST to `http://<server-ip>:5000/ingest` JSON body:
```json
{
  "device_id": "phone-01",
  "device_name": "Pixel 6",
  "ts": 169...,          // optional device timestamp in ms epoch
  "payload": {"sensor": 1.23, "msg": "hello"}
}
```

### Socket.IO device (preferred for low-latency & ping)
From a Socket.IO client connect to namespace `/device` and emit a `register` event:
```js
// pseudo-code (Java/Android or JS)
socket = io("http://<server-ip>:5000/device");
socket.emit("register", { device_id: "phone-01", device_name: "Pixel 6", meta: {os:"android"} });
socket.on("ping", data => {
  // immediately reply with 'pong' including the ping_id and the ts
  socket.emit("pong", { ping_id: data.ping_id, ts: data.ts });
});
```

## Notes & Next steps
- Currently data is not persisted (in-memory only). Add a DB (e.g. InfluxDB, PostgreSQL) when ready.
- Authentication & TLS are not included — do not expose to the open internet without adding authentication and HTTPS.
- You can extend the dashboard with charts per-device, filtering, and raw JSON inspector.

Enjoy — this is an early-stage, professional-looking starting point.

