import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

CHAIN_FILE = Path("/data/chain.json")
GENESIS_HASH = "0" * 64


class Block:
    def __init__(self, index: int, data: dict, previous_hash: str):
        self.index = index
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.data = data
        self.previous_hash = previous_hash
        self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        raw = json.dumps({
            "index":         self.index,
            "timestamp":     self.timestamp,
            "data":          self.data,
            "previous_hash": self.previous_hash,
        }, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "index":         self.index,
            "timestamp":     self.timestamp,
            "data":          self.data,
            "previous_hash": self.previous_hash,
            "hash":          self.hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        b = cls.__new__(cls)
        b.index         = d["index"]
        b.timestamp     = d["timestamp"]
        b.data          = d["data"]
        b.previous_hash = d["previous_hash"]
        b.hash          = d["hash"]
        return b


class Blockchain:
    def __init__(self):
        self._lock = threading.Lock()
        self.chain: list[Block] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        if CHAIN_FILE.exists():
            with open(CHAIN_FILE) as f:
                self.chain = [Block.from_dict(b) for b in json.load(f)]
        else:
            self.chain = [self._make_genesis()]
            self._save()

    def _save(self):
        CHAIN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(CHAIN_FILE, "w") as f:
            json.dump([b.to_dict() for b in self.chain], f, indent=2)

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def add_block(self, data: dict) -> Block:
        with self._lock:
            block = Block(len(self.chain), data, self.chain[-1].hash)
            self.chain.append(block)
            self._save()
            return block

    def get_block(self, index: int) -> Block | None:
        if 0 <= index < len(self.chain):
            return self.chain[index]
        return None

    def latest_block(self) -> Block:
        return self.chain[-1]

    def search(self, key: str, value: str) -> list[Block]:
        """Returns blocks whose data contains key=value (string match)."""
        results = []
        for block in self.chain:
            v = block.data.get(key)
            if v is not None and str(v) == value:
                results.append(block)
        return results

    def verify(self) -> dict:
        """Replays every hash and chain link. Returns a full integrity report."""
        breaks = []

        # Genesis block
        genesis = self.chain[0]
        if genesis.previous_hash != GENESIS_HASH:
            breaks.append({"index": 0, "reason": "genesis previous_hash is not all-zeros"})
        if genesis.hash != genesis._compute_hash():
            breaks.append({"index": 0, "reason": "genesis hash is invalid"})

        for i in range(1, len(self.chain)):
            current  = self.chain[i]
            previous = self.chain[i - 1]

            if current.hash != current._compute_hash():
                breaks.append({"index": i, "reason": "hash mismatch — block data was altered"})

            if current.previous_hash != previous.hash:
                breaks.append({"index": i, "reason": "chain link broken — block was inserted or removed"})

        return {
            "intact":  len(breaks) == 0,
            "length":  len(self.chain),
            "breaks":  breaks,
            "message": "Chain is intact." if not breaks else f"{len(breaks)} break(s) detected.",
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _make_genesis() -> Block:
        b = Block.__new__(Block)
        b.index         = 0
        b.timestamp     = datetime.now(timezone.utc).isoformat()
        b.data          = {"genesis": True, "message": "BlackBox Sentinel — genesis block"}
        b.previous_hash = GENESIS_HASH
        b.hash          = b._compute_hash()
        return b
