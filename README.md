# BlackBox Sentinel

Tamper-evident flight-data demo: telemetry from simulated planes is streamed to
a FastAPI server, displayed on a React dashboard, and anchored on a local
Ethereum (Ganache) chain so any later edit to an archived flight log can be
detected.

## Stack

- **Server** — FastAPI + WebSockets, Python 3.11
- **Chain** — Ganache (local Ethereum) inside Docker, persistent `ganache_data` volume
- **Frontend** — React + Vite
- **Simulator** — terminal Python script that fakes a fleet of planes

## Run it

```bash
docker compose up --build
```

That starts three containers:

| Container          | URL                       | Purpose                              |
|--------------------|---------------------------|--------------------------------------|
| `blackbox_frontend`| http://localhost:5173     | React dashboard (Logs + Blockchain)  |
| `blackbox_server`  | http://localhost:8000     | FastAPI + WebSockets                 |
| `blackbox_ganache` | http://localhost:8545     | Local Ethereum JSON-RPC              |

Then, from a second terminal on the host, launch the simulator:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python simulator.py
```

Controls inside the simulator: **SPACE** launch plane · **K** crash random ·
**L** land random · **C** chaos mode · **Q** quit.

## Try the tamper demo

1. Launch a plane in the simulator, let it run a few seconds, land it.
2. Open the **Logs** page → 🛡️ VERIFY a flight → should say *VALID*.
3. Click ☠ TAMPER on the same flight → file is edited on disk.
4. Click 🛡️ VERIFY again → now says *TAMPERED* with the original on-chain hash.
5. Open the **Blockchain** page → 🔍 VERIFY CHAIN → tampered file is flagged
   in the "File Logs vs Chain" panel, while the "Blockchain Chain" panel
   stays green (the chain itself is fine; the file changed).

## Project layout

```
server.py             FastAPI app, WebSockets, REST endpoints
log_manager.py        Disk layout for flight files, verification helpers
ledger.py             Audit-log API — every event becomes one Ganache tx
blockchain_eth.py     Web3 anchoring: queue worker, signing, cache, verify
simulator.py          Terminal-based plane fleet simulator
frontend/             React SPA (Dashboard, Logs, Blockchain pages)
flight_logs/          On-disk flight files (live + archived). Source of truth: the chain.
tests/                pytest suite, runs against the local Ganache
```

## Where to read more

- **`STORAGE.md`** — full walk-through of how data is stored, signed, and
  verified, with a real anchor transaction decoded field-by-field. Also covers
  what a production setup would look like (consortium chains, Merkle batching,
  HSM key custody, cost math for a real fleet).
- **`BLOCKCHAIN.md`** — plain-English intro to blockchain hashing and chains.
  (Note: parts predate the move to Ganache as source of truth — `STORAGE.md`
  is the up-to-date reference.)

## Configuration

`.env` (already populated for the local Ganache setup):

```env
ETH_NODE_URL=http://localhost:8545
ETH_CHAIN_ID=1337
ETH_PRIVATE_KEY=0x…       # signing key for all anchor transactions
```

To point at any other EVM chain (Base, Sepolia, etc.), just change those three
values — the rest of the code is chain-agnostic. See `STORAGE.md §8` for
guidance on going to a real testnet or mainnet.

## Tests

```bash
docker compose up -d ganache    # tests need the chain
.venv/bin/pytest tests/ -v
```
