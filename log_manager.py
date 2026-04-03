import shutil
import os
import json
from datetime import datetime
from pathlib import Path
import ledger

# --- CONFIGURATION ---
ROOT_DIR = Path(__file__).parent.resolve()
BASE_DIR = ROOT_DIR / "flight_logs"

DIRS = {
    "live": BASE_DIR / "live_cache",
    "normal": BASE_DIR / "standard_ops",
    "radio_fail": BASE_DIR / "investigation" / "7600_radio_loss",
    "emergency": BASE_DIR / "investigation" / "7700_emergency",
    "security": BASE_DIR / "investigation" / "7500_security",
    "crash": BASE_DIR / "investigation" / "crashes",
    "lost": BASE_DIR / "investigation" / "signal_loss",
}


def setup_directories():
    print(f"📂 LOADING LOGS FROM: {BASE_DIR}")
    for d in DIRS.values():
        d.mkdir(parents=True, exist_ok=True)
    # Initialize Ledger
    ledger.log_event("SYSTEM_STARTUP", "N/A", "LOCALHOST", "Server Booted")


def get_live_path(plane_id: str) -> Path:
    return DIRS["live"] / f"{plane_id}.jsonl"


def append_log(plane_id: str, data: dict):
    file_path = get_live_path(plane_id)
    is_new = not file_path.exists()

    try:
        data['server_ts'] = datetime.now().isoformat()
        json_line = json.dumps(data) + "\n"

        # --- CRITICAL FIX HERE ---
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(json_line)
            f.flush()  # Push data from Python to OS buffer
            os.fsync(f.fileno())  # Force OS to write to physical disk

        if is_new:
            # Now that we forced the write, the ledger will see the file correctly
            ledger.log_event("FLIGHT_STARTED", str(file_path), "SYSTEM", f"Plane {plane_id} connected")

        # Record every telemetry data point in the blockchain
        ledger.log_telemetry(plane_id, data)

    except Exception as e:
        print(f"❌ Error writing log for {plane_id}: {e}")


def archive_flight(plane_id: str, final_status: str, max_severity_squawk: str):
    src = get_live_path(plane_id)
    if not src.exists(): return

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
        ledger.log_event("FLIGHT_ARCHIVED", str(dst), "SYSTEM", f"Moved to {category}")

        # Register standard_ops flights explicitly in the blockchain
        if category == "normal":
            ledger.log_standard_ops(dst, plane_id)

        print(f"🗄️ ARCHIVED {plane_id} -> {category.upper()}")
    except Exception as e:
        print(f"❌ Error archiving {plane_id}: {e}")


def get_all_logs(requestor_ip: str):
    ledger.log_event("LIST_VIEWED", "ALL", requestor_ip, "User viewed log list")
    results = {key: [] for key in DIRS.keys() if key != "live"}

    for category, path in DIRS.items():
        if category == "live": continue
        # Use simple list comprehension to avoid generator issues
        files = [f.name for f in path.glob("*.jsonl")]
        results[category] = sorted(files, reverse=True)

    return results


def delete_log(category: str, filename: str, requestor_ip: str):
    if category not in DIRS: raise ValueError("Invalid category")

    file_path = DIRS[category] / filename

    # Security: Ensure we don't escape the folder
    if not file_path.parent == DIRS[category]: raise ValueError("Invalid path")
    if not file_path.exists(): raise FileNotFoundError("File not found")

    ledger.log_event("EVIDENCE_DESTROYED", str(file_path), requestor_ip, "User deleted log file")
    file_path.unlink()
    return filename


# ... (Keep imports and previous code) ...

def verify_log(category: str, filename: str, requestor_ip: str):
    """
    Compares current disk hash vs ledger hash.
    """
    if category not in DIRS: raise ValueError("Invalid category")

    file_path = DIRS[category] / filename
    if not file_path.exists(): raise FileNotFoundError("File not found")

    # 1. Calculate Hash of the file RIGHT NOW
    current_hash = ledger.calculate_file_hash(file_path)

    # 2. Retrieve Hash from the Ledger (Back in time)
    original_record = ledger.get_original_hash(filename)

    ledger.log_event("INTEGRITY_CHECK", str(file_path), requestor_ip, "User ran validity check")

    if not original_record:
        return {
            "status": "UNKNOWN",
            "message": "No ledger record found (File might pre-date the system).",
            "current_hash": current_hash
        }

    original_hash = original_record['evidence_hash']

    if current_hash == original_hash:
        return {
            "status": "VALID",
            "message": "✅ INTEGRITY CONFIRMED. File is identical to original archive.",
            "timestamp": original_record['timestamp'],
            "hash": current_hash
        }
    else:
        return {
            "status": "TAMPERED",
            "message": "🚨 WARNING: FILE HAS BEEN ALTERED!",
            "original_hash": original_hash,
            "current_hash": current_hash
        }