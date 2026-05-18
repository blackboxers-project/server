import asyncio
import logging
from datetime import datetime
from typing import List, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse

import log_manager
import ledger
import blockchain_eth

# ---------------------------------------------------------------------------
# Logging — single format used everywhere so Docker logs are easy to read
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger("server")

from pathlib import Path

app = FastAPI()

_dist = Path("frontend/dist")
if (_dist / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(_dist / "assets")), name="vite-assets")

# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------
log.info("=== BlackBox Sentinel starting ===")
eth_ok = blockchain_eth.is_connected()
log.info(f"Ethereum node : {blockchain_eth.ETH_NODE_URL}  connected={eth_ok}")
if not eth_ok:
    log.warning("Ganache is not reachable — on-chain anchoring will fail until it is up")
else:
    n = blockchain_eth.rebuild_cache_from_chain()
    log.info(f"[CHAIN]    cache primed with {n} historical anchor(s)")

log_manager.setup_directories()


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------
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
        log.info(f"[CONNECT]  {plane_id} connected  (active planes: {len(self.plane_meta)})")
        await self.broadcast_state(plane_id)

    async def update_plane(self, plane_id, data):
        meta = self.plane_meta[plane_id]
        current_squawk = str(data.get('squawk', '1200'))
        meta['squawk'] = current_squawk

        priority = {'7500': 3, '7700': 2, '7600': 1, '1200': 0}
        if priority.get(current_squawk, 0) > priority.get(meta['worst_squawk'], 0):
            meta['worst_squawk'] = current_squawk
            log.warning(f"[SQUAWK]   {plane_id} flagged {current_squawk}")

        await self.broadcast_telemetry(plane_id, data)

    async def connect_dashboard(self, websocket: WebSocket):
        await websocket.accept()
        self.dashboards.append(websocket)
        log.info(f"[DASHBOARD] viewer connected  (viewers: {len(self.dashboards)})")
        for pid, meta in self.plane_meta.items():
            if meta['status'] == 'ONLINE':
                await websocket.send_json({'type': 'status_update', 'plane_id': pid, 'state': meta})

    async def broadcast_state(self, plane_id):
        msg = {'type': 'status_update', 'plane_id': plane_id, 'state': self.plane_meta[plane_id]}
        for ws in self.dashboards:
            try:
                await ws.send_json(msg)
            except Exception:
                pass

    async def broadcast_telemetry(self, plane_id, data):
        msg = {'type': 'telemetry', 'plane_id': plane_id, 'data': data,
               'squawk': self.plane_meta[plane_id]['squawk']}
        for ws in self.dashboards:
            try:
                await ws.send_json(msg)
            except Exception:
                pass


manager = ConnectionManager()


# ---------------------------------------------------------------------------
# WebSockets
# ---------------------------------------------------------------------------

@app.websocket("/ws/plane/{plane_id}")
async def websocket_plane(websocket: WebSocket, plane_id: str):
    await websocket.accept()
    await manager.register_plane(plane_id)
    clean_landing = False
    frames = 0

    try:
        while True:
            data = await websocket.receive_json()
            if data.get('type') == 'disconnect':
                clean_landing = True
                break

            frames += 1
            log_manager.append_log(plane_id, data)
            await manager.update_plane(plane_id, data)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error(f"[ERROR]    {plane_id}: {e}")

    meta = manager.plane_meta.get(plane_id, {})
    final_status = "LANDED" if clean_landing else "LOST_SIGNAL"
    if 'worst_squawk' in meta and meta['worst_squawk'] in ['7500', '7700'] and not clean_landing:
        final_status = "CRASHED"

    meta['status'] = final_status
    await manager.broadcast_state(plane_id)

    worst = meta.get('worst_squawk', '1200')
    log.info(f"[ARCHIVE]  {plane_id}  status={final_status}  squawk={worst}  frames={frames}")
    log_manager.archive_flight(plane_id, final_status, worst)

    if plane_id in manager.plane_meta:
        del manager.plane_meta[plane_id]


@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    await manager.connect_dashboard(websocket)
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        if websocket in manager.dashboards:
            manager.dashboards.remove(websocket)
        log.info(f"[DASHBOARD] viewer disconnected  (viewers: {len(manager.dashboards)})")


# ---------------------------------------------------------------------------
# REST API
# ---------------------------------------------------------------------------

@app.get("/api/logs")
def get_logs(request: Request):
    client_ip = request.client.host
    log.info(f"[API]  GET /api/logs  from {client_ip}")
    return JSONResponse(log_manager.get_all_logs(client_ip))


@app.delete("/api/logs/{category}/{filename}")
def delete_log_file(category: str, filename: str, request: Request):
    client_ip = request.client.host
    try:
        deleted_file = log_manager.delete_log(category, filename, client_ip)
        log.warning(f"[DELETE]   {category}/{deleted_file}  by {client_ip}")
        return {"status": "success", "file": deleted_file}
    except Exception as e:
        log.error(f"[DELETE]   failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/tamper/log/{category}/{filename}")
def tamper_log_file(category: str, filename: str, request: Request):
    """
    Demo endpoint: injects a fake telemetry value into one line of a flight log.
    After tampering, GET /api/verify/{category}/{filename} will return TAMPERED.
    """
    import json as _json

    if category not in log_manager.DIRS:
        return JSONResponse({"error": "Invalid category"}, status_code=400)

    file_path = log_manager.DIRS[category] / filename
    if not file_path.parent == log_manager.DIRS[category]:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not file_path.exists():
        return JSONResponse({"error": "File not found"}, status_code=404)

    lines = file_path.read_text().splitlines(keepends=True)
    non_empty = [(i, l) for i, l in enumerate(lines) if l.strip()]
    if not non_empty:
        return JSONResponse({"error": "File is empty"}, status_code=400)

    # Pick the first data line and inject a fake altitude
    idx, raw = non_empty[0]
    try:
        entry = _json.loads(raw)
    except _json.JSONDecodeError:
        return JSONResponse({"error": "First line is not valid JSON"}, status_code=400)

    original_alt = entry.get("altitude", "<absent>")
    entry["altitude"]      = 99999
    entry["_tampered"]     = True
    entry["_tamper_note"]  = "Altitude injected by demo tamper endpoint"

    lines[idx] = _json.dumps(entry) + "\n"
    file_path.write_text("".join(lines))

    log.warning(f"[TAMPER]   flight log  {category}/{filename}  alt={original_alt}→99999")

    return JSONResponse({
        "tampered":       True,
        "file":           filename,
        "category":       category,
        "field":          "altitude",
        "original_value": str(original_alt),
        "injected_value": 99999,
        "hint":           f"Run GET /api/verify/{category}/{filename} to detect the tampering.",
    })


@app.get("/api/verify/{category}/{filename}")
def verify_log_integrity(category: str, filename: str, request: Request):
    client_ip = request.client.host
    try:
        result = log_manager.verify_log(category, filename, client_ip)
        log.info(f"[VERIFY]   {category}/{filename}  status={result.get('status')}  from {client_ip}")
        return JSONResponse(result)
    except Exception as e:
        log.error(f"[VERIFY]   failed: {e}")
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/chain/verify")
def verify_chain_integrity():
    chain    = ledger.verify_chain()
    archives = log_manager.verify_all_archives()

    tampered = archives.get("tampered", [])
    missing  = archives.get("missing", [])

    intact = (chain.get("intact", False)
              and not tampered
              and not missing)

    parts = []
    if chain.get("breaks"):
        parts.append(f"{len(chain['breaks'])} on-chain mismatch(es)")
    if chain.get("errored"):
        parts.append(f"{chain['errored']} event(s) never reached the chain")
    if tampered:
        parts.append(f"{len(tampered)} tampered file(s) on disk")
    if missing:
        parts.append(f"{len(missing)} archived file(s) missing from disk")

    message = (f"All {chain.get('entries', 0)} anchor(s) verified — "
               f"every archived flight file still matches the chain."
               if intact else "Issues: " + "; ".join(parts) + ".")

    result = {
        **chain,
        "intact":         intact,
        "tampered_files": tampered,
        "missing_files":  missing,
        "message":        message,
    }
    log.info(
        f"[CHAIN]    verify  intact={intact}  entries={result.get('entries')}  "
        f"breaks={len(chain.get('breaks', []))}  errored={chain.get('errored', 0)}  "
        f"tampered={len(tampered)}  missing={len(missing)}"
    )
    return JSONResponse(result, status_code=200 if intact else 409)


@app.get("/api/eth/anchor/{tx_hash}")
def get_eth_anchor(tx_hash: str):
    result = blockchain_eth.get_anchor(tx_hash)
    log.info(f"[ETH]      get_anchor {tx_hash[:16]}…  status={result.get('status')}")
    return JSONResponse(result)


@app.get("/api/eth/anchors")
def get_eth_anchor_log():
    entries = blockchain_eth.get_all_anchors()
    log.info(f"[ETH]      anchors requested  count={len(entries)}")
    return JSONResponse({"count": len(entries), "anchors": entries})


@app.get("/api/eth/status")
def get_eth_status():
    connected = blockchain_eth.is_connected()
    return JSONResponse({
        "connected": connected,
        "node":      blockchain_eth.ETH_NODE_URL,
        "chain_id":  blockchain_eth.ETH_CHAIN_ID,
    })


@app.post("/api/eth/rpc")
async def eth_rpc_proxy(request: Request):
    """
    Thin JSON-RPC proxy to Ganache.
    Lets the browser fetch raw on-chain data without CORS issues.
    Only whitelisted read-only methods are forwarded.
    """
    import httpx
    ALLOWED = {"eth_getTransactionByHash", "eth_getBlockByNumber",
               "eth_getBlockByHash", "eth_blockNumber", "net_version"}
    body = await request.json()
    if body.get("method") not in ALLOWED:
        return JSONResponse({"error": "method not allowed"}, status_code=403)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                blockchain_eth.ETH_NODE_URL,
                json={"jsonrpc": "2.0", "id": 1, **{k: body[k] for k in ("method", "params") if k in body}},
                timeout=5,
            )
        return JSONResponse(resp.json())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ---------------------------------------------------------------------------
# SPA fallback — serves the React app for all non-API routes
# ---------------------------------------------------------------------------

@app.get("/{full_path:path}")
def spa_fallback(full_path: str = ""):
    index = _dist / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text())
    return HTMLResponse(
        "<pre>Frontend not built.\nRun: cd frontend && npm install && npm run build</pre>",
        status_code=503,
    )
