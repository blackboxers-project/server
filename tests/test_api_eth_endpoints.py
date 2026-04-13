"""
Tests for the FastAPI /api/eth/* endpoints in server.py.

Uses httpx + TestClient. blockchain_eth functions are patched per-test with
monkeypatch so the real module is preserved for the integration test suite.

    pip install pytest httpx fastapi
    pytest tests/test_api_eth_endpoints.py -v
"""

import hashlib
import os
import sys

import pytest

os.environ.setdefault("ETH_NODE_URL",    "http://localhost:8545")
os.environ.setdefault("ETH_CHAIN_ID",    "1337")
os.environ.setdefault("ETH_PRIVATE_KEY", "")

# conftest.py already stubs log_manager + ledger.
# Import the real blockchain_eth (background worker starts lazily).
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import blockchain_eth  # noqa: E402
import server           # noqa: E402  (uses the real blockchain_eth)

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(server.app, raise_server_exceptions=True)

GOOD_TX   = "0x" + "ab" * 32
GOOD_HASH = hashlib.sha256(b"sample").hexdigest()


# ---------------------------------------------------------------------------
# /api/eth/status
# ---------------------------------------------------------------------------

class TestEthStatus:
    def test_returns_200(self, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "is_connected", lambda: True)
        assert client.get("/api/eth/status").status_code == 200

    def test_body_when_connected(self, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "is_connected", lambda: True)
        body = client.get("/api/eth/status").json()
        assert body["connected"] is True
        assert body["node"] == "http://localhost:8545"
        assert body["chain_id"] == 1337

    def test_body_when_disconnected(self, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "is_connected", lambda: False)
        body = client.get("/api/eth/status").json()
        assert body["connected"] is False


# ---------------------------------------------------------------------------
# /api/eth/anchor/{tx_hash}
# ---------------------------------------------------------------------------

class TestGetAnchor:
    def test_found_returns_200(self, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "get_anchor", lambda _: {
            "status": "FOUND", "tx_hash": GOOD_TX, "block": 5, "sha256": GOOD_HASH,
        })
        assert client.get(f"/api/eth/anchor/{GOOD_TX}").status_code == 200

    def test_found_body_contains_sha256(self, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "get_anchor", lambda _: {
            "status": "FOUND", "tx_hash": GOOD_TX, "block": 5, "sha256": GOOD_HASH,
        })
        body = client.get(f"/api/eth/anchor/{GOOD_TX}").json()
        assert body["sha256"] == GOOD_HASH
        assert body["block"] == 5

    def test_get_anchor_called_with_tx_hash(self, monkeypatch):
        calls = []
        monkeypatch.setattr(blockchain_eth, "get_anchor",
                            lambda tx: calls.append(tx) or {"status": "FOUND"})
        client.get(f"/api/eth/anchor/{GOOD_TX}")
        assert calls == [GOOD_TX]

    def test_error_still_returns_200(self, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "get_anchor",
                            lambda _: {"status": "ERROR", "message": "tx not found"})
        resp = client.get("/api/eth/anchor/0xdeadbeef")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ERROR"


# ---------------------------------------------------------------------------
# /api/eth/anchors
# ---------------------------------------------------------------------------

class TestGetAnchorLog:
    def test_empty_log(self, monkeypatch):
        monkeypatch.setattr(blockchain_eth, "get_all_anchors", lambda: [])
        body = client.get("/api/eth/anchors").json()
        assert body["count"] == 0
        assert body["anchors"] == []

    def test_non_empty_log(self, monkeypatch):
        entries = [
            {"hash": GOOD_HASH, "status": "ANCHORED", "tx_hash": GOOD_TX, "block": 1},
            {"hash": "aaaa",    "status": "ANCHORED", "tx_hash": "0xbbbb", "block": 2},
        ]
        monkeypatch.setattr(blockchain_eth, "get_all_anchors", lambda: entries)
        body = client.get("/api/eth/anchors").json()
        assert body["count"] == 2
        assert len(body["anchors"]) == 2
        assert body["anchors"][0]["tx_hash"] == GOOD_TX

    def test_count_matches_anchors_length(self, monkeypatch):
        entries = [{"hash": "x", "status": "ANCHORED"}] * 7
        monkeypatch.setattr(blockchain_eth, "get_all_anchors", lambda: entries)
        body = client.get("/api/eth/anchors").json()
        assert body["count"] == len(body["anchors"]) == 7
