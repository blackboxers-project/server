"""
Audit log for BlackBox Sentinel.

There is no local ledger file: every event we used to write to
`secure_ledger.jsonl` is now an Ethereum transaction on the Ganache container.
The chain *is* the ledger. This module is a thin convenience layer that
prepares structured payloads and hands them to `blockchain_eth`.

Event types written on-chain:
    SYSTEM_STARTUP             — server booted
    FLIGHT_STARTED             — plane connected
    LOG_ENTRY                  — one telemetry frame
    FLIGHT_ARCHIVED            — flight ended and file archived
    STANDARD_OPS_REGISTERED    — secondary anchor for normal flights
    LIST_VIEWED                — dashboard fetched the log list
    INTEGRITY_CHECK            — verification was run
    EVIDENCE_DESTROYED         — a log file was deleted
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import blockchain_eth


# ---------------------------------------------------------------------------
# Hashing helpers
# ---------------------------------------------------------------------------

def calculate_file_hash(filepath: Path) -> str:
    if not filepath.exists():
        return "FILE_MISSING"
    sha256 = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)
        return sha256.hexdigest()
    except Exception:
        return "HASH_ERROR"


def _hash_payload(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Write — every entry below produces one Ganache transaction
# ---------------------------------------------------------------------------

def log_event(action: str, target_file: str, actor_ip: str, extra_info: str = ""):
    payload = {
        "ts":     datetime.now().isoformat(),
        "action": action,
        "actor":  actor_ip,
        "target": Path(target_file).name,
        "info":   extra_info,
    }
    digest = _hash_payload(payload)
    blockchain_eth.queue_anchor(digest, f"{action} | {payload['target']}", payload)


def log_telemetry(plane_id: str, data: dict):
    """One telemetry frame → one on-chain anchor. Each frame is hashed and the
    raw telemetry is embedded in the tx's metadata, so the chain alone proves
    the frame was received with these values at this time."""
    payload = {
        "ts":        datetime.now().isoformat(),
        "action":    "LOG_ENTRY",
        "plane":     plane_id,
        "telemetry": data,
    }
    digest = _hash_payload(payload)
    blockchain_eth.queue_anchor(digest, f"LOG_ENTRY plane={plane_id}", payload)


def log_flight_archived(filepath: Path, plane_id: str, category: str,
                        squawk: str = "1200"):
    """Synchronous: the archive event must be on-chain before verification
    is offered to the user."""
    file_hash = calculate_file_hash(filepath)
    metadata = {
        "ts":       datetime.now().isoformat(),
        "action":   "FLIGHT_ARCHIVED",
        "plane":    plane_id,
        "category": category,
        "squawk":   squawk,
        "filename": filepath.name,
    }
    blockchain_eth.anchor_sync(
        file_hash,
        f"FLIGHT_ARCHIVED squawk={squawk} plane={plane_id} | {filepath.name}",
        metadata,
    )


def log_standard_ops(filepath: Path, plane_id: str):
    file_hash = calculate_file_hash(filepath)
    metadata = {
        "ts":       datetime.now().isoformat(),
        "action":   "STANDARD_OPS_REGISTERED",
        "plane":    plane_id,
        "filename": filepath.name,
    }
    blockchain_eth.anchor_sync(
        file_hash,
        f"STANDARD_OPS {plane_id} | {filepath.name}",
        metadata,
    )


# ---------------------------------------------------------------------------
# Read / verification — always answered from the chain
# ---------------------------------------------------------------------------

def get_original_hash(filename: str) -> dict | None:
    """Returns the on-chain anchor record for an archived filename, in a shape
    compatible with the legacy ledger entry format."""
    anchor = blockchain_eth.find_anchor_by_metadata(
        lambda meta: meta.get("filename") == filename
        and meta.get("action") in ("FLIGHT_ARCHIVED", "STANDARD_OPS_REGISTERED")
    )
    if not anchor:
        return None
    meta = anchor.get("metadata") or {}
    return {
        "evidence_hash": anchor.get("hash"),
        "timestamp":     meta.get("ts"),
        "tx_hash":       anchor.get("tx_hash"),
        "block":         anchor.get("block"),
        "action":        meta.get("action"),
    }


def verify_chain() -> dict:
    """Walks the cached anchors and re-fetches each tx from Ganache. With an
    immutable chain there is nothing to tamper, so this primarily proves that
    Ganache still holds the data we expect (catches lost-volume scenarios)."""
    return blockchain_eth.verify_chain()
