"""
Local Ethereum (Ganache) storage for BlackBox Sentinel.

Every audit/telemetry/archive event becomes a single Ganache transaction whose
`data` field is laid out as:

    0x | 64-hex SHA-256 | utf8(metadata-json).hex()

That is: the first 32 bytes are the evidence hash, the rest is a compact JSON
blob describing what was anchored (action, plane, filename, etc.). With this
encoding, the chain alone is a complete, self-describing audit log — no
secondary on-disk ledger is required.

A small in-memory cache mirrors the chain so listing/searching anchors is O(1).
At startup we rebuild the cache by walking every block of the local chain
(fast on Ganache, which keeps blocks small).

Environment
-----------
ETH_NODE_URL    – JSON-RPC endpoint   (default: http://localhost:8545)
ETH_CHAIN_ID    – chain id            (default: 1337 — Ganache default)
ETH_PRIVATE_KEY – hex private key of the signing account. If absent, uses
                  Ganache's first unlocked account (dev only).
"""

import json
import logging
import os
import queue
import threading
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

ETH_NODE_URL    = os.environ.get("ETH_NODE_URL",    "http://localhost:8545")
ETH_CHAIN_ID    = int(os.environ.get("ETH_CHAIN_ID", "1337"))
ETH_PRIVATE_KEY = os.environ.get("ETH_PRIVATE_KEY", "")

log = logging.getLogger("blockchain_eth")

_queue: "queue.Queue" = queue.Queue()

# In-memory mirror of every anchor we have observed on the chain.
_anchors: list[dict] = []
_by_hash: dict[str, dict] = {}
_anchors_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Web3 helpers
# ---------------------------------------------------------------------------

def _get_w3():
    try:
        from web3 import Web3
        return Web3(Web3.HTTPProvider(ETH_NODE_URL, request_kwargs={"timeout": 10}))
    except ImportError:
        return None


def is_connected() -> bool:
    w3 = _get_w3()
    if w3 is None:
        return False
    try:
        return w3.is_connected()
    except Exception:
        return False


def _get_sender_address():
    w3 = _get_w3()
    if w3 is None or not w3.is_connected():
        return None
    if ETH_PRIVATE_KEY:
        return w3.eth.account.from_key(ETH_PRIVATE_KEY).address
    accounts = w3.eth.accounts
    return accounts[0] if accounts else None


def _hex(v) -> str:
    """Normalise a HexBytes / bytes / str into a `0x…` lowercase hex string."""
    if hasattr(v, "hex"):
        s = v.hex()
    else:
        s = str(v)
    return s if s.startswith("0x") else "0x" + s


# ---------------------------------------------------------------------------
# Tx data encoding/decoding
# ---------------------------------------------------------------------------

def _build_tx_data(sha256_hex: str, metadata: dict | None) -> str:
    payload = "0x" + sha256_hex
    if metadata:
        try:
            payload += json.dumps(metadata, separators=(",", ":")).encode().hex()
        except Exception:
            pass
    return payload


def _parse_tx_data(tx_input) -> tuple[str | None, dict | None]:
    hex_str = tx_input.hex() if hasattr(tx_input, "hex") else str(tx_input)
    if hex_str.startswith("0x"):
        hex_str = hex_str[2:]
    if len(hex_str) < 64:
        return None, None
    sha256 = hex_str[:64]
    rest_hex = hex_str[64:]
    metadata = None
    if rest_hex:
        try:
            metadata = json.loads(bytes.fromhex(rest_hex).decode())
        except Exception:
            metadata = None
    return sha256, metadata


# ---------------------------------------------------------------------------
# Public API — write
# ---------------------------------------------------------------------------

def queue_anchor(sha256_hex: str, label: str, metadata: dict | None = None):
    """Non-blocking. Worker thread mines the tx and updates the cache."""
    _queue.put({
        "hash":      sha256_hex,
        "label":     label,
        "metadata":  metadata or {"label": label},
        "queued_at": datetime.now().isoformat(),
    })


def anchor_sync(sha256_hex: str, label: str, metadata: dict | None = None) -> dict:
    """Block until the anchor is mined. Use for events whose tx_hash is needed
    immediately (e.g. archive ⇒ verification right after)."""
    w3 = _get_w3()
    if w3 is None or not w3.is_connected():
        record = {
            "queued_at":    datetime.now().isoformat(),
            "processed_at": datetime.now().isoformat(),
            "hash":         sha256_hex,
            "label":        label,
            "metadata":     metadata or {"label": label},
            "status":       "ERROR",
            "tx_hash":      None,
            "block":        None,
            "error":        f"Cannot reach Ethereum node at {ETH_NODE_URL}",
            "node":         ETH_NODE_URL,
        }
        with _anchors_lock:
            _anchors.append(record)
        log.error(f"[ANCHOR] node unreachable, sync anchor failed: {label[:60]}")
        return record
    return _do_anchor(w3, sha256_hex, label, metadata or {"label": label})


# ---------------------------------------------------------------------------
# Public API — read
# ---------------------------------------------------------------------------

def get_anchor(tx_hash_hex: str) -> dict:
    """Fetch a single anchor by tx hash, reading straight from the chain."""
    w3 = _get_w3()
    if w3 is None:
        return {"status": "ERROR", "message": "web3 package not installed"}
    if not w3.is_connected():
        return {"status": "ERROR", "message": f"Cannot reach {ETH_NODE_URL}"}
    try:
        tx = w3.eth.get_transaction(tx_hash_hex)
        sha256, metadata = _parse_tx_data(tx.input)
        return {
            "status":   "FOUND",
            "tx_hash":  tx_hash_hex,
            "block":    tx.blockNumber,
            "sha256":   sha256,
            "metadata": metadata,
            "from":     tx["from"],
            "chain_id": ETH_CHAIN_ID,
            "node":     ETH_NODE_URL,
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def get_all_anchors() -> list:
    with _anchors_lock:
        return list(_anchors)


def find_anchor_by_hash(sha256_hex: str) -> dict | None:
    with _anchors_lock:
        return _by_hash.get(sha256_hex)


def find_anchor_by_metadata(predicate) -> dict | None:
    with _anchors_lock:
        for a in _anchors:
            meta = a.get("metadata") or {}
            try:
                if predicate(meta):
                    return a
            except Exception:
                continue
    return None


def verify_chain() -> dict:
    """For every cached anchor, re-fetch its tx and confirm the on-chain
    SHA-256 still matches. Also reports events that failed to anchor at all
    (status=ERROR) — those represent gaps in the audit trail."""
    w3 = _get_w3()
    if w3 is None or not w3.is_connected():
        return {"intact": False, "entries": 0, "breaks": [], "errored": 0,
                "errored_anchors": [],
                "error":  f"Cannot reach Ethereum node at {ETH_NODE_URL}"}

    with _anchors_lock:
        snapshot = list(_anchors)

    breaks = []
    errored_anchors = []
    checked = 0
    for record in snapshot:
        status = record.get("status")
        if status == "ERROR":
            errored_anchors.append({
                "label":     record.get("label"),
                "error":     record.get("error"),
                "queued_at": record.get("queued_at"),
            })
            continue
        if status != "ANCHORED":
            continue
        checked += 1
        tx_hash = record.get("tx_hash")
        expected = record.get("hash")
        try:
            tx = w3.eth.get_transaction(tx_hash)
            sha256, _ = _parse_tx_data(tx.input)
            if sha256 != expected:
                breaks.append({
                    "tx_hash":  tx_hash,
                    "label":    record.get("label"),
                    "expected": expected,
                    "found":    sha256,
                    "reason":   "on-chain SHA-256 does not match cached anchor",
                })
        except Exception as e:
            breaks.append({
                "tx_hash": tx_hash,
                "label":   record.get("label"),
                "reason":  f"tx not found on chain: {e}",
            })

    errored = len(errored_anchors)
    intact  = len(breaks) == 0 and errored == 0

    if intact:
        message = f"All {checked} anchor(s) verified against Ganache."
    elif breaks and errored:
        message = (f"{len(breaks)} anchor(s) altered/missing on-chain "
                   f"AND {errored} event(s) never made it on-chain.")
    elif breaks:
        message = f"{len(breaks)} anchor(s) missing or altered on-chain."
    else:
        message = (f"All {checked} anchored entries match the chain, but "
                   f"{errored} event(s) failed to anchor (gap in audit trail).")

    return {
        "intact":          intact,
        "entries":         checked,
        "breaks":          breaks,
        "errored":         errored,
        "errored_anchors": errored_anchors,
        "message":         message,
    }


def rebuild_cache_from_chain() -> int:
    """Walks every block on the local chain, finds txs originating from our
    sender address, and rebuilds the in-memory anchor cache. Cheap on Ganache."""
    w3 = _get_w3()
    if w3 is None or not w3.is_connected():
        return 0
    sender = _get_sender_address()
    if not sender:
        return 0
    sender_lc = sender.lower()
    try:
        latest = w3.eth.block_number
    except Exception:
        return 0

    found = []
    for n in range(0, latest + 1):
        try:
            block = w3.eth.get_block(n, full_transactions=True)
        except Exception:
            continue
        for tx in block.transactions:
            try:
                if tx["from"].lower() != sender_lc:
                    continue
            except Exception:
                continue
            sha256, metadata = _parse_tx_data(tx.input)
            if not sha256:
                continue
            ts_iso = datetime.fromtimestamp(block.timestamp).isoformat() if block.timestamp else None
            record = {
                "queued_at":    (metadata or {}).get("ts") or ts_iso,
                "processed_at": ts_iso,
                "hash":         sha256,
                "label":        (metadata or {}).get("label", ""),
                "metadata":     metadata or {},
                "status":       "ANCHORED",
                "tx_hash":      _hex(tx.hash),
                "block":        tx.blockNumber,
                "error":        None,
                "node":         ETH_NODE_URL,
            }
            found.append(record)

    with _anchors_lock:
        _anchors.clear()
        _by_hash.clear()
        for r in found:
            _anchors.append(r)
            _by_hash[r["hash"]] = r

    log.info(f"[ANCHOR] cache rebuilt from chain: {len(found)} anchors found")
    return len(found)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _send_tx(w3, sha256_hex: str, metadata: dict | None = None) -> dict:
    data_field = _build_tx_data(sha256_hex, metadata)

    if ETH_PRIVATE_KEY:
        account = w3.eth.account.from_key(ETH_PRIVATE_KEY)
        nonce   = w3.eth.get_transaction_count(account.address)
        try:
            gas_estimate = w3.eth.estimate_gas({
                "from":  account.address,
                "to":    account.address,
                "value": 0,
                "data":  data_field,
            })
        except Exception:
            gas_estimate = 100_000
        tx = {
            "nonce":    nonce,
            "to":       account.address,
            "value":    0,
            "data":     data_field,
            "gas":      int(gas_estimate * 1.2) + 1000,
            "gasPrice": w3.eth.gas_price,
            "chainId":  ETH_CHAIN_ID,
        }
        signed  = w3.eth.account.sign_transaction(tx, ETH_PRIVATE_KEY)
        raw     = getattr(signed, "raw_transaction", None) or signed.rawTransaction
        tx_hash = w3.eth.send_raw_transaction(raw)
    else:
        sender = w3.eth.accounts[0]
        try:
            gas_estimate = w3.eth.estimate_gas({
                "from":  sender,
                "to":    sender,
                "value": 0,
                "data":  data_field,
            })
        except Exception:
            gas_estimate = 100_000
        tx_hash = w3.eth.send_transaction({
            "from":  sender,
            "to":    sender,
            "value": 0,
            "data":  data_field,
            "gas":   int(gas_estimate * 1.2) + 1000,
        })

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    return {
        "tx_hash": _hex(tx_hash),
        "block":   receipt.blockNumber,
        "status":  "success" if receipt.status == 1 else "reverted",
    }


def _do_anchor(w3, sha256_hex: str, label: str, metadata: dict) -> dict:
    record = {
        "queued_at":    metadata.get("ts") or datetime.now().isoformat(),
        "processed_at": None,
        "hash":         sha256_hex,
        "label":        label,
        "metadata":     metadata,
        "status":       "PENDING",
        "tx_hash":      None,
        "block":        None,
        "error":        None,
        "node":         ETH_NODE_URL,
    }
    try:
        result = _send_tx(w3, sha256_hex, metadata)
        record["status"]       = "ANCHORED"
        record["tx_hash"]      = result["tx_hash"]
        record["block"]        = result["block"]
        record["processed_at"] = datetime.now().isoformat()
        with _anchors_lock:
            _anchors.append(record)
            _by_hash[sha256_hex] = record
        log.info(f"[ANCHOR] block={result['block']}  tx={result['tx_hash'][:16]}…  {label[:70]}")
    except Exception as e:
        record["status"]       = "ERROR"
        record["error"]        = str(e)
        record["processed_at"] = datetime.now().isoformat()
        with _anchors_lock:
            _anchors.append(record)
        log.error(f"[ANCHOR] failed: {e}  label={label[:60]}")
    return record


def _worker():
    while True:
        item = _queue.get()
        if item is None:
            break
        w3 = _get_w3()
        if w3 is None or not w3.is_connected():
            err_record = {
                "queued_at":    item["queued_at"],
                "processed_at": datetime.now().isoformat(),
                "hash":         item["hash"],
                "label":        item["label"],
                "metadata":     item.get("metadata") or {},
                "status":       "ERROR",
                "tx_hash":      None,
                "block":        None,
                "error":        f"Cannot reach Ethereum node at {ETH_NODE_URL}",
                "node":         ETH_NODE_URL,
            }
            with _anchors_lock:
                _anchors.append(err_record)
            log.error(f"[ANCHOR] Ethereum node unreachable at {ETH_NODE_URL}")
        else:
            _do_anchor(w3, item["hash"], item["label"], item.get("metadata") or {"label": item["label"]})
        _queue.task_done()


threading.Thread(target=_worker, daemon=True, name="eth-anchor").start()
