"""
Filesystem layout for flight files. Flight `.jsonl` files live on disk for
fast dashboard access; every event involving them is anchored on Ganache via
the `ledger` module, so the disk copy is just a convenience cache — the
authoritative record is on-chain.
"""

import os
import shutil
import json
from datetime import datetime
from pathlib import Path

import ledger


ROOT_DIR = Path(__file__).parent.resolve()
BASE_DIR = ROOT_DIR / "flight_logs"

DIRS = {
    "live":       BASE_DIR / "live_cache",
    "normal":     BASE_DIR / "standard_ops",
    "radio_fail": BASE_DIR / "investigation" / "7600_radio_loss",
    "emergency":  BASE_DIR / "investigation" / "7700_emergency",
    "security":   BASE_DIR / "investigation" / "7500_security",
    "crash":      BASE_DIR / "investigation" / "crashes",
    "lost":       BASE_DIR / "investigation" / "signal_loss",
}


def setup_directories():
    print(f"📂 LOADING LOGS FROM: {BASE_DIR}")
    for d in DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    ledger.log_event("SYSTEM_STARTUP", "N/A", "LOCALHOST", "Server Booted")


def get_live_path(plane_id: str) -> Path:
    return DIRS["live"] / f"{plane_id}.jsonl"


def append_log(plane_id: str, data: dict):
    file_path = get_live_path(plane_id)
    is_new = not file_path.exists()

    try:
        data['server_ts'] = datetime.now().isoformat()
        json_line = json.dumps(data) + "\n"

        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json_line)
            f.flush()
            os.fsync(f.fileno())

        if is_new:
            ledger.log_event("FLIGHT_STARTED", str(file_path), "SYSTEM",
                             f"Plane {plane_id} connected")

        ledger.log_telemetry(plane_id, data)

    except Exception as e:
        print(f"❌ Error writing log for {plane_id}: {e}")


def archive_flight(plane_id: str, final_status: str, max_severity_squawk: str):
    src = get_live_path(plane_id)
    if not src.exists():
        return

    category = "normal"
    if max_severity_squawk == '7500':
        category = "security"
    elif max_severity_squawk == '7700':
        category = "emergency"
    elif max_severity_squawk == '7600':
        category = "radio_fail"
    elif final_status == "CRASHED":
        category = "crash"
    elif final_status == "LOST_SIGNAL":
        category = "lost"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_name = f"{timestamp}_{plane_id}.jsonl"
    dst = DIRS[category] / dst_name

    try:
        shutil.move(str(src), str(dst))
        ledger.log_flight_archived(dst, plane_id, category, squawk=max_severity_squawk)
        if category == "normal":
            ledger.log_standard_ops(dst, plane_id)
        print(f"🗄️ ARCHIVED {plane_id} -> {category.upper()}")
    except Exception as e:
        print(f"❌ Error archiving {plane_id}: {e}")


def get_all_logs(requestor_ip: str):
    ledger.log_event("LIST_VIEWED", "ALL", requestor_ip, "User viewed log list")
    results = {key: [] for key in DIRS.keys() if key != "live"}
    for category, path in DIRS.items():
        if category == "live":
            continue
        files = [f.name for f in path.glob("*.jsonl")]
        results[category] = sorted(files, reverse=True)
    return results


def delete_log(category: str, filename: str, requestor_ip: str):
    if category not in DIRS:
        raise ValueError("Invalid category")
    file_path = DIRS[category] / filename
    if not file_path.parent == DIRS[category]:
        raise ValueError("Invalid path")
    if not file_path.exists():
        raise FileNotFoundError("File not found")

    ledger.log_event("EVIDENCE_DESTROYED", str(file_path), requestor_ip,
                     "User deleted log file")
    file_path.unlink()
    return filename


def verify_all_archives() -> dict:
    """Cross-check every archived file on disk against its on-chain anchor.
    Also reports anchors whose file is no longer on disk."""
    import blockchain_eth

    tampered = []
    seen = set()

    # Walk disk → confirm each archived file matches its chain anchor.
    for category, path in DIRS.items():
        if category == "live":
            continue
        for file_path in path.glob("*.jsonl"):
            seen.add(file_path.name)
            record = ledger.get_original_hash(file_path.name)
            if not record:
                continue
            current_hash = ledger.calculate_file_hash(file_path)
            if current_hash != record["evidence_hash"]:
                tampered.append({
                    "filename":      file_path.name,
                    "category":      category,
                    "expected_hash": record["evidence_hash"],
                    "current_hash":  current_hash,
                    "tx_hash":       record.get("tx_hash"),
                })

    # Walk chain → flag archive anchors whose file is no longer on disk.
    missing = []
    missing_seen = set()
    for anchor in blockchain_eth.get_all_anchors():
        if anchor.get("status") != "ANCHORED":
            continue
        meta = anchor.get("metadata") or {}
        if meta.get("action") not in ("FLIGHT_ARCHIVED", "STANDARD_OPS_REGISTERED"):
            continue
        filename = meta.get("filename")
        if not filename or filename in seen or filename in missing_seen:
            continue
        missing_seen.add(filename)
        missing.append({
            "filename":      filename,
            "category":      meta.get("category"),
            "expected_hash": anchor.get("hash"),
            "tx_hash":       anchor.get("tx_hash"),
        })

    return {"tampered": tampered, "missing": missing}


def verify_log(category: str, filename: str, requestor_ip: str):
    """Compares the file's current SHA-256 against the hash anchored on
    Ganache at archive time. Mismatch ⇒ file was tampered with after archive."""
    if category not in DIRS:
        raise ValueError("Invalid category")

    file_path = DIRS[category] / filename
    if not file_path.exists():
        raise FileNotFoundError("File not found")

    current_hash = ledger.calculate_file_hash(file_path)
    original_record = ledger.get_original_hash(filename)

    ledger.log_event("INTEGRITY_CHECK", str(file_path), requestor_ip,
                     "User ran validity check")

    if not original_record:
        return {
            "status":       "UNKNOWN",
            "message":      "No on-chain anchor found (file may pre-date the chain).",
            "current_hash": current_hash,
        }

    original_hash = original_record['evidence_hash']

    if current_hash == original_hash:
        return {
            "status":    "VALID",
            "message":   "✅ INTEGRITY CONFIRMED. File matches the hash anchored on Ganache.",
            "timestamp": original_record['timestamp'],
            "hash":      current_hash,
            "tx_hash":   original_record.get('tx_hash'),
            "block":     original_record.get('block'),
        }
    return {
        "status":        "TAMPERED",
        "message":       "🚨 WARNING: FILE HAS BEEN ALTERED!",
        "original_hash": original_hash,
        "current_hash":  current_hash,
        "tx_hash":       original_record.get('tx_hash'),
        "block":         original_record.get('block'),
    }
