#!/usr/bin/env python3
"""
app.py
Flask + Flask-SocketIO server that accepts:
 - HTTP POST /ingest  -> lightweight device POST of JSON
 - Socket.IO namespace /device -> persistent device socket connections for low-latency streams & ping
 - Dashboard served at GET / -> dashboard that shows live data, devices, pings, and charts

Run:
    pip install -r requirements.txt
    python app.py

Notes:
 - If your Android device can't do websockets, POST JSON to /ingest.
 - For accurate ping measurements use socket connection from device (see README examples).
"""
from datetime import datetime, timezone
from flask import Flask, request, render_template, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import time
import threading

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# In-memory store for connected devices and their latest data
devices = {}  # device_id -> {name, last_seen, last_payload, socket_connected, rtt_ms, meta...}
devices_lock = threading.Lock()

def now_ts_ms():
    return int(time.time() * 1000)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/ingest", methods=["POST"])
def ingest():
    """
    HTTP endpoint for devices to POST JSON streams.
    Expected JSON example:
    {
      "device_id": "phone-01",
      "device_name": "Pixel 6",
      "ts": 169...,          # optional device timestamp in ms epoch
      "payload": {"sensor": 1.23, "msg": "hello"}
    }
    """
    data = request.get_json(force=True, silent=True)
    if not data:
        return jsonify({"error":"invalid json"}), 400
    device_id = str(data.get("device_id", "unknown"))
    device_name = data.get("device_name", "unknown")
    payload = data.get("payload", {})
    ts = data.get("ts")  # optional device timestamp in ms
    recv_ts = now_ts_ms()
    with devices_lock:
        dev = devices.setdefault(device_id, {})
        dev.update({
            "device_id": device_id,
            "name": device_name,
            "last_seen": recv_ts,
            "last_payload": payload,
            "last_device_ts": ts,
            "socket_connected": dev.get("socket_connected", False),
        })
        # If device provided ts, compute one-way apparent latency: server_recv - device_ts
        if ts is not None:
            try:
                dev["apparent_one_way_ms"] = int(recv_ts - int(ts))
            except Exception:
                dev["apparent_one_way_ms"] = None
    # Broadcast to dashboard clients
    socketio.emit("ingest", {"device_id": device_id, "device_name": device_name,
                             "payload": payload, "recv_ts": recv_ts, "device_ts": ts})
    return jsonify({"status":"ok", "recv_ts": recv_ts})

# Socket.IO namespace for persistent device connections (recommended for live ping/rtt)
@socketio.on("connect", namespace="/device")
def device_connect():
    # A device is expected to emit 'register' after connecting with its id/name
    sid = request.sid
    print("Device socket connect", sid)
    emit("connected", {"msg": "hello device", "sid": sid})

@socketio.on("register", namespace="/device")
def device_register(info):
    """
    info: {"device_id": "phone-01", "device_name": "Pixel 6", "meta": {...} }
    """
    sid = request.sid
    device_id = info.get("device_id", sid)
    device_name = info.get("device_name", "unknown")
    with devices_lock:
        dev = devices.setdefault(device_id, {})
        dev.update({
            "device_id": device_id,
            "name": device_name,
            "socket_connected": True,
            "socket_sid": sid,
            "last_seen": now_ts_ms(),
            "meta": info.get("meta", {})
        })
    print(f"Registered device {device_id} ({device_name})")
    # Inform dashboards about device update
    socketio.emit("device_update", {"device_id": device_id, "name": device_name, "socket_connected": True})

@socketio.on("disconnect", namespace="/device")
def device_disconnect():
    sid = request.sid
    # find device by sid and mark disconnected
    with devices_lock:
        for did, dev in devices.items():
            if dev.get("socket_sid") == sid:
                dev["socket_connected"] = False
                dev["socket_sid"] = None
                dev["last_seen"] = now_ts_ms()
                socketio.emit("device_update", {"device_id": did, "name": dev.get("name"), "socket_connected": False})
                print("Device disconnected", did)
                break

# Ping RPC: Dashboard requests server to ping the device; server relays ping and measures RTT.
@socketio.on("ping_device", namespace="/dashboard")
def ping_device(data):
    # data: {"device_id": "...", "ping_id": "..."} ping_id is opaque
    device_id = data.get("device_id")
    ping_id = data.get("ping_id", str(int(time.time()*1000)))
    with devices_lock:
        dev = devices.get(device_id)
        if not dev or not dev.get("socket_connected"):
            emit("ping_result", {"device_id": device_id, "error": "device_not_connected", "ping_id": ping_id})
            return
        sid = dev.get("socket_sid")
        # Send ping to device socket; device should reply with 'pong' event
        socketio.emit("ping", {"ping_id": ping_id, "ts": now_ts_ms()}, room=sid, namespace="/device")
        # We don't block; the device will respond with 'pong' handled below
        emit("ping_sent", {"device_id": device_id, "ping_id": ping_id})

@socketio.on("pong", namespace="/device")
def handle_pong(data):
    # data: {"ping_id": "...", "ts": device_recv_ts, "client_ts": device_sent_ts?}
    sid = request.sid
    recv_ts = now_ts_ms()
    ping_id = data.get("ping_id")
    # find device
    device_id = None
    with devices_lock:
        for did, dev in devices.items():
            if dev.get("socket_sid") == sid:
                device_id = did
                # compute rtt = recv_ts - data.ts
                try:
                    dev['rtt_ms'] = int(recv_ts - int(data.get("ts", recv_ts)))
                except Exception:
                    dev['rtt_ms'] = None
                dev['last_seen'] = recv_ts
                break
    # Broadcast ping result to dashboards
    socketio.emit("ping_result", {"device_id": device_id, "ping_id": ping_id, "rtt_ms": devices.get(device_id,{}).get("rtt_ms"), "recv_ts": recv_ts})

# Dashboard socket connections
@socketio.on("connect", namespace="/dashboard")
def dashboard_connect():
    sid = request.sid
    print("Dashboard connected", sid)
    # send current devices snapshot
    with devices_lock:
        snapshot = list(devices.values())
    emit("snapshot", {"devices": snapshot})

# simple API to list devices
@app.route("/api/devices", methods=["GET"])
def api_devices():
    with devices_lock:
        snapshot = list(devices.values())
    return jsonify({"devices": snapshot})

if __name__ == "__main__":
    # Use eventlet or gevent installed for production. Here we use the Flask-SocketIO default
    print("Starting server on http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000, debug=True)
