import json
import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path

# --- CONFIGURATION ---
# Use absolute path to match log_manager
ROOT_DIR = Path(__file__).parent.resolve()
AUDIT_FILE = ROOT_DIR / "flight_logs" / "secure_ledger.jsonl"
LOCK = threading.Lock()


def calculate_file_hash(filepath: Path) -> str:
    """Generates SHA-256 Fingerprint."""
    if not filepath.exists(): return "FILE_MISSING"

    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return "HASH_ERROR"


def get_last_chain_hash() -> str:
    """Gets the hash of the previous ledger entry to form the chain."""
    if not AUDIT_FILE.exists():
        return "GENESIS_BLOCK_000000000000000000000000"

    try:
        with open(AUDIT_FILE, 'rb') as f:
            try:
                f.seek(-2, os.SEEK_END)
                while f.read(1) != b'\n':
                    f.seek(-2, os.SEEK_CUR)
            except OSError:
                f.seek(0)

            last_line = f.readline().decode().strip()
            if not last_line: return "GENESIS_BLOCK"

            # Hash the entire previous JSON line
            return hashlib.sha256(last_line.encode()).hexdigest()
    except Exception:
        return "BROKEN_CHAIN"


def log_event(action: str, target_file: str, actor_ip: str, extra_info: str = ""):
    """Writes a tamper-evident entry."""
    # Ensure directory exists
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with LOCK:
        prev_hash = get_last_chain_hash()

        # Handle Path: If target_file is absolute, use it.
        # If relative, join it with ROOT_DIR/flight_logs
        target_path = Path(target_file)
        if not target_path.is_absolute():
            target_path = ROOT_DIR / "flight_logs" / target_file

        # Generate Evidence Hash
        file_fingerprint = "N/A"
        if target_path.exists():
            file_fingerprint = calculate_file_hash(target_path)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "actor": actor_ip,
            "target": str(target_path.name),  # Store just the filename for cleaner logs
            "evidence_hash": file_fingerprint,
            "details": extra_info,
            "chain_link": prev_hash
        }

        with open(AUDIT_FILE, "a") as f:
            f.write(json.dumps(entry) + "\n")


def log_telemetry(plane_id: str, data: dict):
    """Records a telemetry data point in the blockchain ledger.
    Each entry is hashed and chained to the previous one, making
    every single flight data point tamper-evident."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with LOCK:
        prev_hash = get_last_chain_hash()

        # Hash the raw telemetry data for integrity proof
        data_json = json.dumps(data, sort_keys=True)
        data_hash = hashlib.sha256(data_json.encode()).hexdigest()

        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": "LOG_ENTRY",
            "actor": "SYSTEM",
            "target": plane_id,
            "evidence_hash": data_hash,
            "telemetry": data,
            "chain_link": prev_hash
        }

        with open(AUDIT_FILE, "a") as f:
            line = json.dumps(entry) + "\n"
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def log_standard_ops(filepath: Path, plane_id: str):
    """Registers a standard_ops flight in the blockchain.
    Hashes the full archived file and chains it into the ledger,
    ensuring normal flights are as tamper-proof as emergencies."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)

    with LOCK:
        prev_hash = get_last_chain_hash()
        file_hash = calculate_file_hash(filepath)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "action": "STANDARD_OPS_REGISTERED",
            "actor": "SYSTEM",
            "target": str(filepath.name),
            "evidence_hash": file_hash,
            "details": f"Normal flight {plane_id} registered in blockchain",
            "chain_link": prev_hash
        }

        with open(AUDIT_FILE, "a") as f:
            line = json.dumps(entry) + "\n"
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


# ... (keep all previous code in ledger.py) ...

def get_original_hash(filename: str) -> dict:
    """
    Scans the ledger to find the 'FLIGHT_ARCHIVED' entry for a specific file.
    Returns the hash that was recorded at that moment.
    """
    if not AUDIT_FILE.exists():
        return None

    found_entry = None

    # We read line by line.
    # In a real production system with millions of logs, you'd use a database.
    # For a few thousand text logs, this is perfectly fine.
    try:
        with open(AUDIT_FILE, 'r') as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    # We look for the creation/archiving event of this specific filename
                    # We check if the target PATH ends with our filename
                    if entry['target'].endswith(filename) and entry['action'] in ("FLIGHT_ARCHIVED", "STANDARD_OPS_REGISTERED"):
                        found_entry = entry
                except:
                    continue
    except Exception:
        return None

    return found_entry