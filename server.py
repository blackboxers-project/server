import os
import json
import shutil
import asyncio
import glob
from datetime import datetime
from typing import List, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

# --- CONFIGURATION ---
# Force the server to use the folder where server.py is actually located
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.join(ROOT_DIR, "flight_logs")

print(f"📂 LOADING LOGS FROM: {BASE_DIR}") # This prints the path on startup so you can verify

# Granular Folder Structure
DIRS = {
    "live":       os.path.join(BASE_DIR, "live_cache"),
    "normal":     os.path.join(BASE_DIR, "standard_ops"),
    "radio_fail": os.path.join(BASE_DIR, "investigation", "7600_radio_loss"),
    "emergency":  os.path.join(BASE_DIR, "investigation", "7700_emergency"),
    "security":   os.path.join(BASE_DIR, "investigation", "7500_security"),
    "crash":      os.path.join(BASE_DIR, "investigation", "crashes"),
    "lost":       os.path.join(BASE_DIR, "investigation", "signal_loss"),
}

# Granular Folder Structure
DIRS = {
    "live": os.path.join(BASE_DIR, "live_cache"),
    "normal": os.path.join(BASE_DIR, "standard_ops"),
    # Investigation Piles
    "radio_fail": os.path.join(BASE_DIR, "investigation", "7600_radio_loss"),
    "emergency": os.path.join(BASE_DIR, "investigation", "7700_emergency"),
    "security": os.path.join(BASE_DIR, "investigation", "7500_security"),
    "crash": os.path.join(BASE_DIR, "investigation", "crashes"),
    "lost": os.path.join(BASE_DIR, "investigation", "signal_loss"),
}

for d in DIRS.values():
    os.makedirs(d, exist_ok=True)

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- FILE MANAGER ---
def get_live_path(plane_id):
    return os.path.join(DIRS["live"], f"{plane_id}.jsonl")


def append_log(plane_id, data):
    with open(get_live_path(plane_id), "a") as f:
        data['server_ts'] = datetime.now().isoformat()
        f.write(json.dumps(data) + "\n")


async def archive_flight(plane_id, final_status, max_severity_squawk):
    """
    Moves log to the correct folder based on the WORST thing that happened.
    Prioritizes specific squawk codes over generic crash/landing status.
    """
    src = get_live_path(plane_id)
    if not os.path.exists(src): return

    # LOGIC: Determine Destination Folder
    # 1. Check Squawk History (The "Latch")
    if max_severity_squawk == '7500':
        category = "security"
    elif max_severity_squawk == '7700':
        category = "emergency"
    elif max_severity_squawk == '7600':
        category = "radio_fail"  # <--- Explicitly handling 7600

    # 2. If no squawk issues, check physical outcome
    elif final_status == "CRASHED":
        category = "crash"
    elif final_status == "LOST_SIGNAL":
        category = "lost"
    else:
        category = "normal"  # Only goes here if 100% clean flight

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_name = f"{timestamp}_{plane_id}.jsonl"
    dst = os.path.join(DIRS[category], dst_name)

    shutil.move(src, dst)
    print(f"🗄️ ARCHIVED {plane_id} -> {category.upper()}")


# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.dashboards: List[WebSocket] = []
        # Stores metadata including the "Worst Squawk" seen
        self.plane_meta: Dict[str, dict] = {}

    async def register_plane(self, plane_id):
        self.plane_meta[plane_id] = {
            "status": "ONLINE",
            "squawk": "1200",
            "worst_squawk": "1200",  # Latches the most severe code
            "start_time": datetime.now().isoformat()
        }
        await self.broadcast_state(plane_id)

    async def update_plane(self, plane_id, data):
        meta = self.plane_meta[plane_id]

        current_squawk = str(data.get('squawk', '1200'))
        meta['squawk'] = current_squawk

        # --- THE LATCH LOGIC ---
        # If we see a special code, we record it in 'worst_squawk'
        # We prioritize them: 7500 > 7700 > 7600 > 1200
        priority = {'7500': 3, '7700': 2, '7600': 1, '1200': 0}

        current_p = priority.get(current_squawk, 0)
        stored_p = priority.get(meta['worst_squawk'], 0)

        if current_p > stored_p:
            meta['worst_squawk'] = current_squawk
            print(f"⚠️ {plane_id} FLAGGED FOR INVESTIGATION (Squawk: {current_squawk})")

        await self.broadcast_telemetry(plane_id, data)

    async def connect_dashboard(self, websocket: WebSocket):
        await websocket.accept()
        self.dashboards.append(websocket)
        for pid, meta in self.plane_meta.items():
            if meta['status'] == 'ONLINE':
                await websocket.send_json({'type': 'status_update', 'plane_id': pid, 'state': meta})

    async def broadcast_state(self, plane_id):
        msg = {'type': 'status_update', 'plane_id': plane_id, 'state': self.plane_meta[plane_id]}
        for ws in self.dashboards:
            try:
                await ws.send_json(msg)
            except:
                pass

    async def broadcast_telemetry(self, plane_id, data):
        msg = {'type': 'telemetry', 'plane_id': plane_id, 'data': data, 'squawk': self.plane_meta[plane_id]['squawk']}
        for ws in self.dashboards:
            try:
                await ws.send_json(msg)
            except:
                pass


manager = ConnectionManager()


# --- ROUTES ---
@app.delete("/api/logs/{category}/{filename}")
def delete_log_file(category: str, filename: str):
    """
    Deletes a specific log file from the server.
    """
    if category not in DIRS:
        return JSONResponse({"error": "Invalid category"}, status_code=400)

    # Security: Ensure filename is just a name, not a path traversal
    safe_filename = os.path.basename(filename)
    file_path = os.path.join(DIRS[category], safe_filename)

    if not os.path.exists(file_path):
        return JSONResponse({"error": "File not found"}, status_code=404)

    try:
        os.remove(file_path)
        print(f"🗑️ DELETED LOG: {category}/{safe_filename}")
        return {"status": "success", "file": safe_filename}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.websocket("/ws/plane/{plane_id}")
async def websocket_plane(websocket: WebSocket, plane_id: str):
    await websocket.accept()
    await manager.register_plane(plane_id)

    clean_landing = False

    try:
        while True:
            data = await websocket.receive_json()
            if data.get('type') == 'disconnect':
                clean_landing = True
                break

            append_log(plane_id, data)
            await manager.update_plane(plane_id, data)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Server Error: {e}")

    # --- END OF FLIGHT LOGIC ---
    meta = manager.plane_meta[plane_id]

    if clean_landing:
        final_status = "LANDED"
    elif not clean_landing:
        final_status = "LOST_SIGNAL"
        # If they had a severe squawk, assume crash/forced landing
        if meta['worst_squawk'] in ['7500', '7700']:
            final_status = "CRASHED"

    meta['status'] = final_status
    await manager.broadcast_state(plane_id)

    # Pass the latched 'worst_squawk' to the archiver
    await archive_flight(plane_id, final_status, meta['worst_squawk'])

    del manager.plane_meta[plane_id]


@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    await manager.connect_dashboard(websocket)
    try:
        while True: await websocket.receive_text()
    except:
        manager.dashboards.remove(websocket)


# --- API FOR ARCHIVES ---
@app.get("/api/logs")
def get_logs():
    results = {key: [] for key in DIRS.keys() if key != "live"}

    for category in results.keys():
        path = DIRS[category]
        files = glob.glob(os.path.join(path, "*.jsonl"))
        files.sort(key=os.path.getmtime, reverse=True)
        results[category] = [os.path.basename(f) for f in files]

    return JSONResponse(results)


@app.get("/")
def get_index(): return HTMLResponse(open("static/index.html").read())


@app.get("/logs")
def get_logs_page(): return HTMLResponse(open("static/logs.html").read())


@app.get("/plane")
def get_plane(): return HTMLResponse(open("static/plane.html").read())