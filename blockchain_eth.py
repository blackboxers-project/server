"""
Local Ethereum anchoring via Ganache (or any EVM-compatible node).

How it works
------------
A SHA-256 hash is submitted as a zero-value transaction whose `input` (data)
field contains "0x" + the 64-char hex digest.  The transaction is mined
immediately by Ganache and lives on the chain forever.  Anyone with the
tx_hash can retrieve and verify the original digest without any 3rd party.

Environment variables
---------------------
ETH_NODE_URL   – JSON-RPC endpoint   (default: http://localhost:8545)
ETH_CHAIN_ID   – chain id            (default: 1337  — Ganache default)
ETH_PRIVATE_KEY – hex private key of the signing account.
                  If absent, falls back to the first unlocked Ganache account
                  (fine for local dev; MUST be set in production).
"""

import os
import queue
import threading
import json
from datetime import datetime
from pathlib import Path

import logging

from dotenv import load_dotenv

# Load .env from the project root (no-op if the file doesn't exist)
load_dotenv(Path(__file__).parent / ".env")

ETH_NODE_URL    = os.environ.get("ETH_NODE_URL",    "http://localhost:8545")
ETH_CHAIN_ID    = int(os.environ.get("ETH_CHAIN_ID", "1337"))
ETH_PRIVATE_KEY = os.environ.get("ETH_PRIVATE_KEY", "")

log = logging.getLogger("blockchain_eth")

ROOT_DIR  = Path(__file__).parent.resolve()
ETH_LOG   = ROOT_DIR / "flight_logs" / "eth_anchors.jsonl"

_queue: queue.Queue = queue.Queue()
_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Lazy-initialise Web3 so the module imports even without the `web3` package.
# ---------------------------------------------------------------------------

def _get_w3():
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(ETH_NODE_URL, request_kwargs={"timeout": 10}))
        return w3
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def queue_anchor(sha256_hex: str, label: str):
    """Non-blocking. Queues a hash for local-chain anchoring."""
    _queue.put({
        "hash":      sha256_hex,
        "label":     label,
        "queued_at": datetime.now().isoformat(),
    })


def get_anchor(tx_hash_hex: str) -> dict:
    """
    Retrieves an anchored entry from the local chain by transaction hash.
    Returns the embedded SHA-256 digest from the transaction input data.
    """
    w3 = _get_w3()
    if w3 is None:
        return {"status": "ERROR", "message": "web3 package not installed"}
    if not w3.is_connected():
        return {"status": "ERROR", "message": f"Cannot reach {ETH_NODE_URL}"}

    try:
        tx = w3.eth.get_transaction(tx_hash_hex)
        raw_input = tx.input          # bytes or HexBytes
        # input is  0x + 64 hex chars (the SHA-256)
        hex_data  = raw_input.hex() if hasattr(raw_input, "hex") else str(raw_input)
        sha256    = hex_data.lstrip("0x") if hex_data.startswith("0x") else hex_data
        return {
            "status":    "FOUND",
            "tx_hash":   tx_hash_hex,
            "block":     tx.blockNumber,
            "sha256":    sha256,
            "from":      tx["from"],
            "chain_id":  ETH_CHAIN_ID,
            "node":      ETH_NODE_URL,
        }
    except Exception as e:
        return {"status": "ERROR", "message": str(e)}


def get_all_anchors() -> list:
    """Returns all entries from the local ETH anchor log."""
    if not ETH_LOG.exists():
        return []
    entries = []
    with open(ETH_LOG, "r") as f:
        for line in f:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _send_tx(w3, sha256_hex: str) -> dict:
    """Submits the hash as transaction input data and waits for receipt."""
    data_field = "0x" + sha256_hex

    if ETH_PRIVATE_KEY:
        # Sign with explicit key (production / non-Ganache nodes)
        from web3 import Web3
        account = w3.eth.account.from_key(ETH_PRIVATE_KEY)
        nonce   = w3.eth.get_transaction_count(account.address)
        tx      = {
            "nonce":    nonce,
            "to":       account.address,
            "value":    0,
            "data":     data_field,
            "gas":      30_000,
            "gasPrice": w3.eth.gas_price,
            "chainId":  ETH_CHAIN_ID,
        }
        signed  = w3.eth.account.sign_transaction(tx, ETH_PRIVATE_KEY)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    else:
        # Use unlocked Ganache account (dev only)
        sender  = w3.eth.accounts[0]
        tx_hash = w3.eth.send_transaction({
            "from":  sender,
            "to":    sender,
            "value": 0,
            "data":  data_field,
            "gas":   30_000,
        })

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
    return {
        "tx_hash": tx_hash.hex(),
        "block":   receipt.blockNumber,
        "status":  "success" if receipt.status == 1 else "reverted",
    }


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
            "tx_hash":      None,
            "block":        None,
            "error":        None,
            "node":         ETH_NODE_URL,
        }

        w3 = _get_w3()
        if w3 is None:
            record["status"] = "ERROR"
            record["error"]  = "web3 package not installed (pip install web3)"
        elif not w3.is_connected():
            record["status"] = "ERROR"
            record["error"]  = f"Cannot reach Ethereum node at {ETH_NODE_URL}"
            log.error(f"[ANCHOR] Ethereum node unreachable at {ETH_NODE_URL}")
        else:
            try:
                result            = _send_tx(w3, item["hash"])
                record["status"]  = "ANCHORED"
                record["tx_hash"] = result["tx_hash"]
                record["block"]   = result["block"]
                log.info(f"[ANCHOR] block={result['block']}  tx={result['tx_hash'][:16]}…  {item['label'][:70]}")
            except Exception as e:
                record["status"] = "ERROR"
                record["error"]  = str(e)
                log.error(f"[ANCHOR] failed: {e}  label={item['label'][:60]}")

        with _lock:
            ETH_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ETH_LOG, "a") as f:
                f.write(json.dumps(record) + "\n")

        _queue.task_done()


threading.Thread(target=_worker, daemon=True, name="eth-anchor").start()
