from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from chain import Blockchain

app = FastAPI(title="BlackBox Blockchain Node", version="1.0")
bc = Blockchain()


# ------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------

class BlockPayload(BaseModel):
    data: dict


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "blocks": len(bc.chain)}


@app.post("/blocks", status_code=201)
def add_block(body: BlockPayload):
    """Appends a new block to the chain. Body: {"data": {...}}"""
    block = bc.add_block(body.data)
    return block.to_dict()


@app.get("/chain")
def get_chain():
    """Returns the full chain."""
    return {
        "length": len(bc.chain),
        "chain":  [b.to_dict() for b in bc.chain],
    }


@app.get("/chain/verify")
def verify_chain():
    """Replays every hash and chain link. Returns an integrity report."""
    return bc.verify()


@app.get("/blocks/latest")
def get_latest():
    """Returns the most recently added block."""
    return bc.latest_block().to_dict()


@app.get("/blocks/{index}")
def get_block(index: int):
    """Returns a single block by its index."""
    block = bc.get_block(index)
    if block is None:
        raise HTTPException(status_code=404, detail=f"Block {index} not found.")
    return block.to_dict()


@app.get("/blocks/search/{key}/{value}")
def search_blocks(key: str, value: str):
    """Returns all blocks whose data[key] == value."""
    results = bc.search(key, value)
    return {"count": len(results), "blocks": [b.to_dict() for b in results]}
