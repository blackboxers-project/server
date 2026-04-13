"""
Tests for blockchain_eth.py — local Ethereum anchoring module.

These tests run against a real Ganache instance.
Start it before running:

    docker compose up -d ganache

Then:
    pip install web3 pytest
    pytest tests/test_blockchain_eth.py -v
"""

import hashlib
import os
import time

import pytest

# Point the module at the local Ganache (override before import)
os.environ.setdefault("ETH_NODE_URL",   "http://localhost:8545")
os.environ.setdefault("ETH_CHAIN_ID",   "1337")
os.environ.setdefault("ETH_PRIVATE_KEY", "")   # use unlocked Ganache accounts

import blockchain_eth  # noqa: E402  (import after env vars are set)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def w3():
    """Return a connected Web3 instance, skip all tests if node is down."""
    from web3 import Web3
    node = Web3(Web3.HTTPProvider(os.environ["ETH_NODE_URL"]))
    if not node.is_connected():
        pytest.skip(
            f"Ganache not reachable at {os.environ['ETH_NODE_URL']} — "
            "run `docker compose up -d ganache` first."
        )
    return node


@pytest.fixture()
def sample_hash():
    return hashlib.sha256(b"test flight data 12345").hexdigest()


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

class TestConnectivity:
    def test_is_connected_returns_true(self, w3):
        assert blockchain_eth.is_connected() is True

    def test_node_url_env_is_respected(self):
        assert blockchain_eth.ETH_NODE_URL == os.environ["ETH_NODE_URL"]

    def test_chain_id_matches_ganache(self, w3):
        assert w3.eth.chain_id == blockchain_eth.ETH_CHAIN_ID


# ---------------------------------------------------------------------------
# Anchoring (send hash → chain)
# ---------------------------------------------------------------------------

class TestAnchorSubmit:
    def _drain_queue(self, timeout: float = 10.0):
        """Wait for the background worker to process all queued items."""
        deadline = time.time() + timeout
        while not blockchain_eth._queue.empty():
            if time.time() > deadline:
                pytest.fail("Anchor queue not drained within timeout")
            time.sleep(0.1)
        # Give the worker a moment to finish writing the log entry
        time.sleep(0.3)

    def test_queue_anchor_adds_to_queue(self, sample_hash):
        before = blockchain_eth._queue.qsize()
        blockchain_eth.queue_anchor(sample_hash, "test: queue_anchor_adds_to_queue")
        assert blockchain_eth._queue.qsize() == before + 1

    def test_anchor_is_processed_by_worker(self, w3, sample_hash, tmp_path, monkeypatch):
        """The worker sends a real transaction and logs it."""
        log_file = tmp_path / "eth_anchors.jsonl"
        monkeypatch.setattr(blockchain_eth, "ETH_LOG", log_file)

        blockchain_eth.queue_anchor(sample_hash, "test: anchor_processed_by_worker")
        self._drain_queue()

        assert log_file.exists(), "Worker did not create the log file"
        import json
        entries = [json.loads(l) for l in log_file.read_text().splitlines()]
        assert len(entries) >= 1
        last = entries[-1]
        assert last["hash"] == sample_hash
        assert last["status"] == "ANCHORED", f"Unexpected status: {last}"
        assert last["tx_hash"] is not None
        assert last["block"] is not None

    def test_anchor_tx_is_on_chain(self, w3, sample_hash, tmp_path, monkeypatch):
        """After anchoring, the transaction exists on Ganache."""
        log_file = tmp_path / "eth_anchors.jsonl"
        monkeypatch.setattr(blockchain_eth, "ETH_LOG", log_file)

        blockchain_eth.queue_anchor(sample_hash, "test: tx_on_chain")
        self._drain_queue()

        import json
        entry = json.loads(log_file.read_text().splitlines()[-1])
        tx_hash = entry["tx_hash"]

        tx = w3.eth.get_transaction(tx_hash)
        assert tx is not None
        assert tx.blockNumber is not None  # mined

    def test_anchor_input_data_encodes_sha256(self, w3, sample_hash, tmp_path, monkeypatch):
        """The transaction input field contains 0x + the SHA-256 hex."""
        log_file = tmp_path / "eth_anchors.jsonl"
        monkeypatch.setattr(blockchain_eth, "ETH_LOG", log_file)

        blockchain_eth.queue_anchor(sample_hash, "test: input_data_encodes_sha256")
        self._drain_queue()

        import json
        entry = json.loads(log_file.read_text().splitlines()[-1])
        tx = w3.eth.get_transaction(entry["tx_hash"])

        raw_input = tx.input
        hex_str = raw_input.hex() if hasattr(raw_input, "hex") else str(raw_input)
        embedded = hex_str.lstrip("0x")
        assert embedded == sample_hash, (
            f"Expected {sample_hash!r}, got {embedded!r} in tx input"
        )


# ---------------------------------------------------------------------------
# Retrieval (get anchor by tx hash)
# ---------------------------------------------------------------------------

class TestAnchorRetrieval:
    def _submit_and_get_tx(self, w3, sha256_hex: str, label: str) -> str:
        """Directly call _send_tx (bypasses queue) and return the tx hash."""
        result = blockchain_eth._send_tx(w3, sha256_hex)
        assert result["status"] == "success", result
        return result["tx_hash"]

    def test_get_anchor_returns_found(self, w3, sample_hash):
        tx_hash = self._submit_and_get_tx(w3, sample_hash, "test: get_anchor_found")
        result = blockchain_eth.get_anchor(tx_hash)
        assert result["status"] == "FOUND"

    def test_get_anchor_returns_correct_sha256(self, w3, sample_hash):
        tx_hash = self._submit_and_get_tx(w3, sample_hash, "test: correct_sha256")
        result = blockchain_eth.get_anchor(tx_hash)
        assert result["sha256"] == sample_hash

    def test_get_anchor_returns_block_number(self, w3, sample_hash):
        tx_hash = self._submit_and_get_tx(w3, sample_hash, "test: block_number")
        result = blockchain_eth.get_anchor(tx_hash)
        assert isinstance(result["block"], int)
        assert result["block"] >= 0

    def test_get_anchor_invalid_hash_returns_error(self, w3):
        result = blockchain_eth.get_anchor("0xdeadbeef" + "00" * 28)
        assert result["status"] == "ERROR"

    def test_two_different_hashes_get_different_txs(self, w3):
        h1 = hashlib.sha256(b"flight-A").hexdigest()
        h2 = hashlib.sha256(b"flight-B").hexdigest()
        tx1 = self._submit_and_get_tx(w3, h1, "test: h1")
        tx2 = self._submit_and_get_tx(w3, h2, "test: h2")
        assert tx1 != tx2
        assert blockchain_eth.get_anchor(tx1)["sha256"] == h1
        assert blockchain_eth.get_anchor(tx2)["sha256"] == h2


# ---------------------------------------------------------------------------
# Log persistence
# ---------------------------------------------------------------------------

class TestAnchorLog:
    def test_get_all_anchors_empty_when_no_log(self, tmp_path, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "ETH_LOG", tmp_path / "none.jsonl")
        assert blockchain_eth.get_all_anchors() == []

    def test_get_all_anchors_returns_list(self, tmp_path, monkeypatch):
        import json
        log_file = tmp_path / "eth_anchors.jsonl"
        log_file.write_text(
            json.dumps({"hash": "abc", "status": "ANCHORED", "tx_hash": "0x1", "block": 1}) + "\n"
            + json.dumps({"hash": "def", "status": "ANCHORED", "tx_hash": "0x2", "block": 2}) + "\n"
        )
        monkeypatch.setattr(blockchain_eth, "ETH_LOG", log_file)
        entries = blockchain_eth.get_all_anchors()
        assert len(entries) == 2
        assert entries[0]["hash"] == "abc"
        assert entries[1]["hash"] == "def"

    def test_get_all_anchors_ignores_malformed_lines(self, tmp_path, monkeypatch):
        import json
        log_file = tmp_path / "eth_anchors.jsonl"
        log_file.write_text(
            json.dumps({"hash": "good", "status": "ANCHORED"}) + "\n"
            + "NOT JSON\n"
        )
        monkeypatch.setattr(blockchain_eth, "ETH_LOG", log_file)
        entries = blockchain_eth.get_all_anchors()
        assert len(entries) == 1
        assert entries[0]["hash"] == "good"
