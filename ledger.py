import json
import hashlib
import os
import threading
from datetime import datetime
from pathlib import Path

import blockchain_anchor

# --- CONFIGURATION ---
ROOT_DIR = Path(__file__).parent.resolve()
AUDIT_FILE = ROOT_DIR / "flight_logs" / "secure_ledger.jsonl"
LOCK = threading.Lock()

# Single genesis sentinel — used everywhere so chain verifiers always agree.
GENESIS_BLOCK = "GENESIS_BLOCK_000000000000000000000000"

# In-memory index: filename -> last FLIGHT_ARCHIVED / STANDARD_OPS_REGISTERED entry.
# Built once at module load; updated on every new archive write.  O(1) lookups.
_archive_index: dict[str, dict] = {}
_index_lock = threading.Lock()


def _build_index():
    """Scans the ledger once at startup to populate _archive_index."""
    if not AUDIT_FILE.exists():
        return
    try:
        with open(AUDIT_FILE, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    if entry.get("action") in ("FLIGHT_ARCHIVED", "STANDARD_OPS_REGISTERED"):
                        target = entry.get("target", "")
                        _archive_index[target] = entry
                except (json.JSONDecodeError, KeyError):
                    continue
    except Exception:
        pass


_build_index()


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def calculate_file_hash(filepath: Path) -> str:
    """Returns SHA-256 hex digest of a file, or a sentinel string on error."""
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


def get_last_chain_hash() -> str:
    """
    Returns the SHA-256 of the last raw line in the ledger.
    Returns GENESIS_BLOCK if the ledger is absent or empty.
    Must be called inside LOCK.
    """
    if not AUDIT_FILE.exists():
        return GENESIS_BLOCK

    try:
        with open(AUDIT_FILE, "rb") as f:
            # Seek backwards to find the last non-empty line efficiently — O(1) for
            # the common case where the last line is short (< a few KB).
            try:
                f.seek(-2, os.SEEK_END)
                while f.read(1) != b"\n":
                    f.seek(-2, os.SEEK_CUR)
            except OSError:
                f.seek(0)

            last_line = f.readline().decode().strip()

        if not last_line:
            return GENESIS_BLOCK

        return hashlib.sha256(last_line.encode()).hexdigest()

    except Exception:
        return GENESIS_BLOCK


# ---------------------------------------------------------------------------
# Write functions
# ---------------------------------------------------------------------------

def _write_entry(entry: dict, *, flush: bool = False):
    """Serialises and appends one entry. Must be called inside LOCK."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(AUDIT_FILE, "a") as f:
        line = json.dumps(entry) + "\n"
        f.write(line)
        if flush:
            f.flush()
            os.fsync(f.fileno())


def log_event(action: str, target_file: str, actor_ip: str, extra_info: str = ""):
    """Writes a tamper-evident audit entry for any system event."""
    with LOCK:
        prev_hash = get_last_chain_hash()

        target_path = Path(target_file)
        if not target_path.is_absolute():
            target_path = ROOT_DIR / "flight_logs" / target_file

        file_fingerprint = "N/A"
        if target_path.exists():
            file_fingerprint = calculate_file_hash(target_path)

        entry = {
            "timestamp":    datetime.now().isoformat(),
            "action":       action,
            "actor":        actor_ip,
            "target":       target_path.name,
            "evidence_hash": file_fingerprint,
            "details":      extra_info,
            "chain_link":   prev_hash,
        }
        _write_entry(entry)


def log_telemetry(plane_id: str, data: dict):
    """
    Records one telemetry data point in the ledger.
    Every point is hashed and chained — tamper-evident down to the sample level.
    """
    with LOCK:
        prev_hash = get_last_chain_hash()

        data_json = json.dumps(data, sort_keys=True)
        data_hash = hashlib.sha256(data_json.encode()).hexdigest()

        entry = {
            "timestamp":    datetime.now().isoformat(),
            "action":       "LOG_ENTRY",
            "actor":        "SYSTEM",
            "target":       plane_id,
            "evidence_hash": data_hash,
            "telemetry":    data,
            "chain_link":   prev_hash,
        }
        _write_entry(entry, flush=True)


def log_standard_ops(filepath: Path, plane_id: str):
    """Registers a normal flight archive in the ledger + anchors its hash via OriginStamp."""
    with LOCK:
        prev_hash = get_last_chain_hash()
        file_hash = calculate_file_hash(filepath)

        entry = {
            "timestamp":    datetime.now().isoformat(),
            "action":       "STANDARD_OPS_REGISTERED",
            "actor":        "SYSTEM",
            "target":       filepath.name,
            "evidence_hash": file_hash,
            "details":      f"Normal flight {plane_id} registered in blockchain",
            "chain_link":   prev_hash,
        }
        _write_entry(entry, flush=True)

        with _index_lock:
            _archive_index[filepath.name] = entry

    blockchain_anchor.queue_anchor(file_hash, f"STANDARD_OPS {plane_id} | {filepath.name}")


def log_flight_archived(filepath: Path, plane_id: str, category: str,
                        squawk: str = "1200"):
    """Adds a FLIGHT_ARCHIVED ledger entry and anchors the file hash via OriginStamp."""
    with LOCK:
        prev_hash = get_last_chain_hash()
        file_hash = calculate_file_hash(filepath)

        entry = {
            "timestamp":    datetime.now().isoformat(),
            "action":       "FLIGHT_ARCHIVED",
            "actor":        "SYSTEM",
            "target":       filepath.name,
            "evidence_hash": file_hash,
            "details":      f"Moved to {category}",
            "chain_link":   prev_hash,
        }
        _write_entry(entry, flush=True)

        with _index_lock:
            _archive_index[filepath.name] = entry

    blockchain_anchor.queue_anchor(
        file_hash,
        f"FLIGHT_ARCHIVED squawk={squawk} plane={plane_id} | {filepath.name}",
    )


# ---------------------------------------------------------------------------
# Read / verification
# ---------------------------------------------------------------------------

def get_original_hash(filename: str) -> dict | None:
    """
    Returns the ledger entry that recorded the original archive hash for
    a given filename.  O(1) via in-memory index (falls back to linear scan
    if the entry predates this server session).
    """
    with _index_lock:
        entry = _archive_index.get(filename)
    if entry:
        return entry

    # Fallback: linear scan (covers files archived before server started)
    if not AUDIT_FILE.exists():
        return None
    try:
        with open(AUDIT_FILE, "r") as f:
            found = None
            for line in f:
                try:
                    e = json.loads(line)
                    if (e.get("target", "").endswith(filename) and
                            e.get("action") in ("FLIGHT_ARCHIVED", "STANDARD_OPS_REGISTERED")):
                        found = e
                except (json.JSONDecodeError, KeyError):
                    continue
        if found:
            with _index_lock:
                _archive_index[filename] = found
        return found
    except Exception:
        return None


def verify_chain() -> dict:
    """
    Replays the entire ledger and checks every chain_link.
    Returns a summary dict with 'intact' bool and list of any broken links.
    """
    if not AUDIT_FILE.exists():
        return {"intact": True, "entries": 0, "breaks": [],
                "message": "Ledger does not exist yet."}

    breaks = []
    prev_raw = None
    count = 0

    try:
        with open(AUDIT_FILE, "r") as f:
            for lineno, line in enumerate(f, start=1):
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    breaks.append({"line": lineno, "reason": "invalid JSON"})
                    prev_raw = line
                    count += 1
                    continue

                if prev_raw is None:
                    # First entry: chain_link must be GENESIS_BLOCK.
                    # Accept the legacy "GENESIS_BLOCK" sentinel too (written by older code).
                    valid_genesis = (GENESIS_BLOCK, "GENESIS_BLOCK")
                    if entry.get("chain_link") not in valid_genesis:
                        breaks.append({
                            "line": lineno,
                            "reason": "first entry does not link to GENESIS_BLOCK",
                            "found": entry.get("chain_link"),
                        })
                else:
                    expected = hashlib.sha256(prev_raw.encode()).hexdigest()
                    if entry.get("chain_link") != expected:
                        breaks.append({
                            "line": lineno,
                            "action": entry.get("action"),
                            "timestamp": entry.get("timestamp"),
                            "reason": "chain_link mismatch — entry may have been tampered with",
                            "expected": expected[:16] + "...",
                            "found":    str(entry.get("chain_link", ""))[:16] + "...",
                        })

                prev_raw = line
                count += 1

    except Exception as e:
        return {"intact": False, "entries": count, "breaks": breaks,
                "error": str(e)}

    return {
        "intact":  len(breaks) == 0,
        "entries": count,
        "breaks":  breaks,
        "message": ("Chain is intact." if not breaks
                    else f"{len(breaks)} break(s) detected — possible tampering."),
    }
