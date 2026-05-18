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

os.environ.setdefault("ETH_NODE_URL",   "http://localhost:8545")
os.environ.setdefault("ETH_CHAIN_ID",   "1337")
os.environ.setdefault("ETH_PRIVATE_KEY", "")

import blockchain_eth  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def w3():
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


def _drain_queue(timeout: float = 10.0):
    deadline = time.time() + timeout
    while not blockchain_eth._queue.empty():
        if time.time() > deadline:
            pytest.fail("Anchor queue not drained within timeout")
        time.sleep(0.1)
    time.sleep(0.3)


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
# Anchoring — async queue path
# ---------------------------------------------------------------------------

class TestAnchorSubmit:
    def test_queue_anchor_adds_to_queue(self, sample_hash):
        before = blockchain_eth._queue.qsize()
        blockchain_eth.queue_anchor(sample_hash, "test: queue_anchor_adds_to_queue")
        assert blockchain_eth._queue.qsize() == before + 1

    def test_anchor_is_processed_by_worker(self, w3, sample_hash):
        blockchain_eth.queue_anchor(sample_hash, "test: anchor_processed_by_worker")
        _drain_queue()
        entry = blockchain_eth.find_anchor_by_hash(sample_hash)
        assert entry is not None
        assert entry["status"] == "ANCHORED"
        assert entry["tx_hash"] is not None
        assert entry["block"] is not None

    def test_anchor_tx_is_on_chain(self, w3, sample_hash):
        blockchain_eth.queue_anchor(sample_hash, "test: tx_on_chain")
        _drain_queue()
        entry = blockchain_eth.find_anchor_by_hash(sample_hash)
        tx = w3.eth.get_transaction(entry["tx_hash"])
        assert tx is not None
        assert tx.blockNumber is not None

    def test_anchor_input_data_encodes_sha256(self, w3):
        unique = hashlib.sha256(os.urandom(32)).hexdigest()
        meta   = {"label": "test: input_encodes_sha256", "action": "TEST"}
        blockchain_eth.queue_anchor(unique, "test: input_encodes_sha256", meta)
        _drain_queue()
        entry  = blockchain_eth.find_anchor_by_hash(unique)
        tx     = w3.eth.get_transaction(entry["tx_hash"])

        sha256, decoded_meta = blockchain_eth._parse_tx_data(tx.input)
        assert sha256 == unique
        assert decoded_meta == meta


# ---------------------------------------------------------------------------
# Anchoring — synchronous path
# ---------------------------------------------------------------------------

class TestAnchorSync:
    def test_anchor_sync_returns_tx_hash(self, w3):
        h = hashlib.sha256(os.urandom(32)).hexdigest()
        record = blockchain_eth.anchor_sync(h, "test: anchor_sync", {"label": "test: anchor_sync"})
        assert record["status"] == "ANCHORED"
        assert record["tx_hash"]
        assert record["block"] is not None
        # Verify cache was populated
        assert blockchain_eth.find_anchor_by_hash(h) is record


# ---------------------------------------------------------------------------
# Retrieval (get anchor by tx hash)
# ---------------------------------------------------------------------------

class TestAnchorRetrieval:
    def _submit(self, w3, sha256_hex: str, label: str) -> str:
        meta = {"label": label}
        result = blockchain_eth._send_tx(w3, sha256_hex, meta)
        assert result["status"] == "success", result
        return result["tx_hash"]

    def test_get_anchor_returns_found(self, w3, sample_hash):
        tx_hash = self._submit(w3, sample_hash, "test: get_anchor_found")
        result = blockchain_eth.get_anchor(tx_hash)
        assert result["status"] == "FOUND"

    def test_get_anchor_returns_correct_sha256(self, w3):
        h = hashlib.sha256(os.urandom(32)).hexdigest()
        tx_hash = self._submit(w3, h, "test: correct_sha256")
        result = blockchain_eth.get_anchor(tx_hash)
        assert result["sha256"] == h

    def test_get_anchor_returns_block_number(self, w3, sample_hash):
        tx_hash = self._submit(w3, sample_hash, "test: block_number")
        result = blockchain_eth.get_anchor(tx_hash)
        assert isinstance(result["block"], int) and result["block"] >= 0

    def test_get_anchor_returns_metadata(self, w3):
        h = hashlib.sha256(os.urandom(32)).hexdigest()
        meta = {"label": "test: metadata roundtrip", "action": "TEST", "extra": 42}
        result = blockchain_eth._send_tx(w3, h, meta)
        anchor = blockchain_eth.get_anchor(result["tx_hash"])
        assert anchor["metadata"] == meta

    def test_get_anchor_invalid_hash_returns_error(self, w3):
        result = blockchain_eth.get_anchor("0xdeadbeef" + "00" * 28)
        assert result["status"] == "ERROR"

    def test_two_different_hashes_get_different_txs(self, w3):
        h1 = hashlib.sha256(b"flight-A").hexdigest()
        h2 = hashlib.sha256(b"flight-B").hexdigest()
        tx1 = self._submit(w3, h1, "test: h1")
        tx2 = self._submit(w3, h2, "test: h2")
        assert tx1 != tx2
        assert blockchain_eth.get_anchor(tx1)["sha256"] == h1
        assert blockchain_eth.get_anchor(tx2)["sha256"] == h2


# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

class TestAnchorCache:
    def test_get_all_anchors_returns_list(self, w3):
        h = hashlib.sha256(os.urandom(32)).hexdigest()
        blockchain_eth.queue_anchor(h, "test: cache")
        _drain_queue()
        anchors = blockchain_eth.get_all_anchors()
        assert any(a.get("hash") == h for a in anchors)

    def test_rebuild_cache_from_chain(self, w3):
        before = len(blockchain_eth.get_all_anchors())
        h = hashlib.sha256(os.urandom(32)).hexdigest()
        blockchain_eth.anchor_sync(h, "test: rebuild")
        found = blockchain_eth.rebuild_cache_from_chain()
        assert found >= before + 1
        assert blockchain_eth.find_anchor_by_hash(h) is not None

    def test_verify_chain_after_anchor(self, w3):
        h = hashlib.sha256(os.urandom(32)).hexdigest()
        blockchain_eth.anchor_sync(h, "test: verify_chain")
        result = blockchain_eth.verify_chain()
        assert result["intact"] is True
        assert result["entries"] >= 1
