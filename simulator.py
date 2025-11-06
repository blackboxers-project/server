#!/usr/bin/env python3
"""
simulator.py
------------
Simulates multiple Android-like devices sending data to your Flask-SocketIO server.

Usage:
    python simulator.py

Make sure app.py is running on the same machine (or set SERVER_URL to your remote IP).
"""

import json
import random
import threading
import time
import requests
import socketio

SERVER_URL = "http://localhost:5000"   # Change to your server's LAN IP if needed
DEVICE_COUNT = 3                       # Number of simulated devices
USE_SOCKETIO = True                    # Whether to use Socket.IO in addition to HTTP POST

# Create a base class for fake devices
class FakeDevice:
    def __init__(self, device_id, device_name):
        self.device_id = device_id
        self.device_name = device_name
        self.sio = None
        self.stop_flag = False

    def connect_socketio(self):
        self.sio = socketio.Client()

        @self.sio.event(namespace="/device")
        def connect():
            print(f"[{self.device_name}] Connected via Socket.IO")
            self.sio.emit("register", {
                "device_id": self.device_id,
                "device_name": self.device_name,
                "meta": {"os": "android", "model": "simulator"}
            }, namespace="/device")

        @self.sio.event(namespace="/device")
        def disconnect():
            print(f"[{self.device_name}] Disconnected")

        @self.sio.on("ping", namespace="/device")
        def on_ping(data):
            # Immediately respond with pong
            ping_id = data.get("ping_id")
            ts = data.get("ts", int(time.time() * 1000))
            print(f"[{self.device_name}] → pong ({ping_id})")
            self.sio.emit("pong", {"ping_id": ping_id, "ts": ts}, namespace="/device")

        try:
            self.sio.connect(f"{SERVER_URL}/device", transports=["websocket", "polling"])
        except Exception as e:
            print(f"[{self.device_name}] SocketIO connection failed: {e}")

    def send_http_data(self):
        """Send a simulated JSON payload using HTTP POST /ingest"""
        url = f"{SERVER_URL}/ingest"
        payload = {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "ts": int(time.time() * 1000),
            "payload": {
                "temperature": round(random.uniform(20, 35), 2),
                "humidity": round(random.uniform(30, 70), 2),
                "accel_x": round(random.uniform(-1, 1), 3),
                "msg": random.choice(["hello", "streaming", "update", "ok"])
            }
        }
        try:
            r = requests.post(url, json=payload, timeout=5)
            if r.status_code != 200:
                print(f"[{self.device_name}] HTTP error: {r.status_code}")
        except Exception as e:
            print(f"[{self.device_name}] HTTP error: {e}")

    def start(self):
        """Start continuous data sending"""
        if USE_SOCKETIO:
            self.connect_socketio()

        def loop():
            while not self.stop_flag:
                self.send_http_data()
                time.sleep(random.uniform(1.5, 4.0))
        threading.Thread(target=loop, daemon=True).start()

    def stop(self):
        self.stop_flag = True
        if self.sio:
            self.sio.disconnect()

# Main simulation
def main():
    devices = [FakeDevice(f"device-{i+1}", f"Simulated Phone {i+1}") for i in range(DEVICE_COUNT)]
    for d in devices:
        d.start()

    print(f"✅ Started {DEVICE_COUNT} simulated devices.")
    print("You can now open http://localhost:5000 in your browser to see live data.")
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping...")
        for d in devices:
            d.stop()

if __name__ == "__main__":
    main()
