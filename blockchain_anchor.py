"""
Real blockchain anchoring via OriginStamp (free tier).
Every anchored hash gets committed to Bitcoin + Ethereum once per day
via a Merkle tree. No wallet, no gas fees.

Setup:
  1. Sign up at https://originstamp.com/developer (free)
  2. Set env var: ORIGINSTAMP_API_KEY=your_key_here
"""

import json
import os
import queue
import threading
from datetime import datetime
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).parent.resolve()
ANCHOR_LOG = ROOT_DIR / "flight_logs" / "blockchain_anchors.jsonl"

ORIGINSTAMP_API_KEY = os.environ.get("ORIGINSTAMP_API_KEY", "")
ORIGINSTAMP_CREATE_URL = "https://api.originstamp.com/v4/timestamp/create"
ORIGINSTAMP_STATUS_URL = "https://api.originstamp.com/v4/timestamp/{hash}"

_queue: queue.Queue = queue.Queue()
_lock = threading.Lock()


def queue_anchor(sha256_hex: str, label: str):
    """Non-blocking. Queues a hash for OriginStamp anchoring."""
    _queue.put({
        "hash": sha256_hex,
        "label": label,
        "queued_at": datetime.now().isoformat(),
    })


def get_anchor_status(sha256_hex: str) -> dict:
    """Returns the OriginStamp anchoring status for a given SHA-256 hash."""
    if not ORIGINSTAMP_API_KEY:
        return {
            "status": "NO_API_KEY",
            "message": "Set ORIGINSTAMP_API_KEY env variable (free key at originstamp.com/developer).",
        }
    try:
        resp = requests.post(
            ORIGINSTAMP_STATUS_URL.format(hash=sha256_hex),
            headers={"Authorization": f"Token {ORIGINSTAMP_API_KEY}", "Content-Type": "application/json"},
            json={},
            timeout=10,
        )
        resp.raise_for_status()
        return {"status": "OK", "originstamp": resp.json()}
    except requests.HTTPError as e:
        return {"status": "HTTP_ERROR", "code": e.response.status_code, "message": str(e)}
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def get_all_anchors() -> list:
    """Returns all entries from the local anchor log."""
    if not ANCHOR_LOG.exists():
        return []
    entries = []
    with open(ANCHOR_LOG, "r") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _worker():
    while True:
        item = _queue.get()
        if item is None:
            break

        record = {
            "queued_at":    item["queued_at"],
            "processed_at": datetime.now().isoformat(),
            "hash":         item["hash"],
            "label":        item["label"],
            "status":       "PENDING",
            "response":     None,
            "error":        None,
        }

        if not ORIGINSTAMP_API_KEY:
            record["status"] = "NO_API_KEY"
            record["error"] = "Set ORIGINSTAMP_API_KEY to enable real-blockchain anchoring."
        else:
            try:
                resp = requests.post(
                    ORIGINSTAMP_CREATE_URL,
                    headers={"Authorization": f"Token {ORIGINSTAMP_API_KEY}", "Content-Type": "application/json"},
                    json={"hash": item["hash"], "comment": item["label"], "notifications": []},
                    timeout=15,
                )
                resp.raise_for_status()
                record["status"] = "SUBMITTED"
                record["response"] = resp.json()
                print(f"⛓️  ORIGINSTAMP: {item['label'][:70]}")
            except requests.HTTPError as e:
                record["status"] = "HTTP_ERROR"
                record["error"] = f"HTTP {e.response.status_code}: {e}"
                print(f"⚠️  OriginStamp error: {record['error']}")
            except Exception as e:
                record["status"] = "ERROR"
                record["error"] = str(e)
                print(f"⚠️  OriginStamp error: {e}")

        with _lock:
            ANCHOR_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ANCHOR_LOG, "a") as f:
                f.write(json.dumps(record) + "\n")

        _queue.task_done()


threading.Thread(target=_worker, daemon=True, name="blockchain-anchor").start()
