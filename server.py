import asyncio
from datetime import datetime
from typing import List, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

# IMPORT YOUR MODULES
import log_manager
import ledger
import blockchain_anchor

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize folders on startup
log_manager.setup_directories()


# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.dashboards: List[WebSocket] = []
        self.plane_meta: Dict[str, dict] = {}

    async def register_plane(self, plane_id):
        self.plane_meta[plane_id] = {
            "status": "ONLINE",
            "squawk": "1200",
            "worst_squawk": "1200",
            "start_time": datetime.now().isoformat()
        }
        await self.broadcast_state(plane_id)

    async def update_plane(self, plane_id, data):
        meta = self.plane_meta[plane_id]
        current_squawk = str(data.get('squawk', '1200'))
        meta['squawk'] = current_squawk

        priority = {'7500': 3, '7700': 2, '7600': 1, '1200': 0}
        current_p = priority.get(current_squawk, 0)
        stored_p = priority.get(meta['worst_squawk'], 0)

        if current_p > stored_p:
            meta['worst_squawk'] = current_squawk
            print(f"⚠️ {plane_id} FLAGGED: {current_squawk}")

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


# --- WEBSOCKETS ---
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

            log_manager.append_log(plane_id, data)
            await manager.update_plane(plane_id, data)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Server Error: {e}")

    meta = manager.plane_meta.get(plane_id, {})
    final_status = "LANDED" if clean_landing else "LOST_SIGNAL"

    # Check severity if we have metadata
    if 'worst_squawk' in meta and meta['worst_squawk'] in ['7500', '7700'] and not clean_landing:
        final_status = "CRASHED"

    meta['status'] = final_status
    await manager.broadcast_state(plane_id)

    # Archive
    worst = meta.get('worst_squawk', '1200')
    log_manager.archive_flight(plane_id, final_status, worst)

    if plane_id in manager.plane_meta:
        del manager.plane_meta[plane_id]


@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    await manager.connect_dashboard(websocket)
    try:
        while True: await websocket.receive_text()
    except:
        if websocket in manager.dashboards:
            manager.dashboards.remove(websocket)


# --- API ROUTES ---

@app.get("/api/logs")
def get_logs(request: Request):
    # FIX: We now correctly ask FastAPI to give us the Request object
    client_ip = request.client.host
    return JSONResponse(log_manager.get_all_logs(client_ip))


@app.delete("/api/logs/{category}/{filename}")
def delete_log_file(category: str, filename: str, request: Request):
    client_ip = request.client.host
    try:
        deleted_file = log_manager.delete_log(category, filename, client_ip)
        print(f"🗑️ DELETED LOG: {category}/{deleted_file} by {client_ip}")
        return {"status": "success", "file": deleted_file}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/verify/{category}/{filename}")
def verify_log_integrity(category: str, filename: str, request: Request):
    """
    New Endpoint: Checks if the file on disk matches the Ledger.
    """
    client_ip = request.client.host
    try:
        result = log_manager.verify_log(category, filename, client_ip)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/chain/verify")
def verify_chain_integrity():
    """
    Replays the entire ledger and verifies every chain_link.
    Returns whether the chain is intact or has been tampered with.
    """
    result = ledger.verify_chain()
    status_code = 200 if result["intact"] else 409
    return JSONResponse(result, status_code=status_code)


@app.get("/api/anchor/status/{hash_value}")
def get_anchor_status(hash_value: str):
    """
    Returns the real-blockchain anchoring status for a given SHA-256 hash
    (checks OriginStamp — confirms Bitcoin/Ethereum anchoring).
    """
    result = blockchain_anchor.get_anchor_status(hash_value)
    return JSONResponse(result)


@app.get("/api/anchor/log")
def get_anchor_log():
    """Returns all real-blockchain anchor submissions made by this server."""
    entries = blockchain_anchor.get_all_anchors()
    return JSONResponse({"count": len(entries), "anchors": entries})


# --- STATIC FILES ---
@app.get("/")
def get_index(): return HTMLResponse(open("static/index.html").read())


@app.get("/logs")
def get_logs_page(): return HTMLResponse(open("static/logs.html").read())


@app.get("/plane")
def get_plane(): return HTMLResponse(open("static/plane.html").read())