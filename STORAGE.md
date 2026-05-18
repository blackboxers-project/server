# How BlackBox Sentinel Stores and Verifies Flight Data

This doc explains, in plain terms, where flight data lives, how it's pinned to
the blockchain, who signs the transactions, and how integrity is checked.

## TL;DR

- **Flight files** (one per flight) live on **disk** in `flight_logs/` — fast to read for the dashboard.
- **Every event** (boot, frame received, file archived, verify, delete…) is recorded as a **Ganache transaction**. The chain is the audit log.
- An anchor tx stores the **SHA-256 of the protected data plus a small JSON metadata blob** in its `data` field.
- Each tx is **signed locally** with the private key in `.env` — Ganache only mines what we send it.
- Two independent checks: **chain integrity** (anchors still on Ganache?) and **data integrity** (disk files still match the chain?). They answer different questions.

---

## 1. Where things are stored

```
flight_logs/                 ← convenience cache on disk
├── live_cache/
│   └── B737-424.jsonl       ← active flight, being appended in real time
├── standard_ops/
│   └── 20260518_073932_B737-424.jsonl   ← archived after the plane lands
└── investigation/
    ├── 7500_security/
    ├── 7700_emergency/
    ├── 7600_radio_loss/
    ├── crashes/
    └── signal_loss/

Ganache container            ← source of truth
└── chain DB (persistent volume `ganache_data`)
    └── one transaction per event
```

The disk copy is just a convenience for the dashboard. The chain is the only place we trust.

---

## 2. What goes on-chain — a real anchor

Here is one actual anchor from the running system. It records "flight B737-424 was archived as normal." We took it directly out of Ganache via JSON-RPC:

```json
{
  "hash":        "0x55cdfe5ddcb221a7a0075b678351c231eab6a2cc33df25aa3aaee3f3c85c4dd1",
  "from":        "0x0d3d532090a54bcd849b022e04a59c0caf02a3fe",
  "to":          "0x0d3d532090a54bcd849b022e04a59c0caf02a3fe",
  "value":       "0x0",
  "nonce":       "0x66",
  "gas":         "0x74d1",
  "gasPrice":    "0x77359400",
  "blockNumber": "0x67",
  "input":       "0x7866c33b1ac84f21afcec750108b01baecfac139600f3cc0d96ec2cfb44a17d27b227473223a22323032362d30352d31385430373a33393a33322e333531313136222c…",
  "v": "0xa96", "r": "0x9649…", "s": "0x2340…"
}
```

Field by field:

| Field         | Hex value        | Decoded                                                       | Why we care                                                       |
|---------------|------------------|---------------------------------------------------------------|-------------------------------------------------------------------|
| `hash`        | `0x55cdfe…`      | The tx hash (its identity on the chain)                       | What we save to look it up again later                            |
| `from` / `to` | `0x0d3d…`        | Our anchor account — sender and recipient are the same        | We're not paying anyone; just storing data in `input`             |
| `value`       | `0x0`            | 0 ETH                                                         | This is a storage tx, not a transfer                              |
| `nonce`       | `0x66`           | 102                                                           | This is the 103rd tx from this account; per-sender ordering glue  |
| `gas`         | `0x74d1`         | 29 905 — the **max** "computational units" we'll allow         | Pays for the work of including this data on-chain                 |
| `gasPrice`    | `0x77359400`     | 2 000 000 000 wei = 2 Gwei                                    | The "price per unit of work" we offered                           |
| `blockNumber` | `0x67`           | Block 103                                                     | Tells you which block this tx was mined into                      |
| `input`       | `0x7866c3…7b22…` | **SHA-256 + JSON metadata** (see next section)                | The actual payload — what this anchor protects                    |
| `v`, `r`, `s` | …                | ECDSA signature                                                | Proves `from` really sent this; can't be forged without the key   |

### About gas — the short version

`gas` and `gasPrice` exist on real Ethereum to pay miners for the CPU work of running your tx. The total cost is `gas_used × gasPrice`, paid in ETH out of your account's balance.

**In our setup it doesn't really cost anything** because:
- Ganache is a local simulator — its "ETH" is fake.
- Each of our 5 wallet accounts boots with 1000 fake ETH.
- We never run out, so the gas fields are pure formality.

On real Ethereum mainnet, anchoring the same tx would cost real money. The amount depends on the size of the `input` field (more data → more gas). Our 124-byte input would cost a fraction of a dollar at typical mainnet prices.

---

## 3. Decoding the `input` field — where the actual data lives

`input` is one long hex string. Our code packs two things into it:

```
input = "0x"  +  <32-byte SHA-256 hash>  +  <JSON metadata as UTF-8 bytes, hex-encoded>
```

For the tx above:

```
0x  7866c33b1ac84f21afcec750108b01baecfac139600f3cc0d96ec2cfb44a17d2          ← SHA-256 (64 hex chars = 32 bytes)
    7b227473223a22323032362d30352d31385430373a33393a33322e333531313136222c…  ← rest is hex-encoded JSON
```

Decoding the SHA-256 part — it's the hash of the archived file `20260518_073932_B737-424.jsonl`. Recompute the file's SHA-256 right now and compare.

Decoding the rest with `bytes.fromhex(...).decode()`:

```json
{
  "ts":       "2026-05-18T07:39:32.351116",
  "action":   "FLIGHT_ARCHIVED",
  "plane":    "B737-424",
  "category": "normal",
  "squawk":   "1200",
  "filename": "20260518_073932_B737-424.jsonl"
}
```

Anyone with the tx hash can read this back from Ganache and answer:
*"What was anchored, when, about which plane, and what hash should the file have?"*
No secondary database needed — the chain is fully self-describing.

---

## 4. How a tx is signed

The signing key is **not** generated by Ganache. It lives in your `.env`:

```
ETH_PRIVATE_KEY=0xdce368029c632b3761112754bf94474002fe2e631718b92fa66644f212ce7b67
```

This 32-byte secret determines exactly one Ethereum address: `0x0d3d…02a3fe`, the `from` address in the tx above. Anyone holding this key can sign as that account; anyone *not* holding it cannot.

The signing flow inside `blockchain_eth._send_tx`:

```python
account = w3.eth.account.from_key(ETH_PRIVATE_KEY)       # derive address
tx      = {nonce, to, value, data, gas, gasPrice, ...}    # build unsigned tx
signed  = w3.eth.account.sign_transaction(tx, ETH_PRIVATE_KEY)  # ECDSA sign
w3.eth.send_raw_transaction(signed.raw_transaction)       # ship to Ganache
```

Two things to take away:

1. **Signing happens in our Python process**, not in Ganache. Ganache receives an already-signed payload, verifies the signature against the `from` address, and includes the tx in a block. Ganache *never sees the private key*.

2. The signature (`v, r, s`) is the proof. Anyone reading the tx later can run the same ECDSA verification, recover the signer's public address, and confirm it was `0x0d3d…02a3fe` that authorised the anchor. **Forging an anchor from this address without the key is computationally infeasible.**

If `ETH_PRIVATE_KEY` is *not* set in `.env`, our code falls back to `w3.eth.send_transaction(...)` (no local signing). Ganache then signs using one of its unlocked accounts. This is a Ganache-only convenience; real Ethereum nodes don't hold keys for you.

---

## 5. How verification works

There are **two independent integrity questions**, and they answer different things.

### A. Per-file verification — "did this specific file change after archive?"

Triggered by the 🛡️ VERIFY button on the Logs page. Code: `log_manager.verify_log()`.

```
1. Read file from disk   → flight_logs/standard_ops/20260518_073932_B737-424.jsonl
2. SHA-256(file)         → e.g. 7866c33b1ac8…
3. Look up the anchor on Ganache for this filename
4. Compare disk hash vs on-chain hash
```

- Match → **VALID** (file is untouched since archive)
- Mismatch → **TAMPERED** (someone edited the file after it was anchored)
- No anchor found → **UNKNOWN** (file pre-dates the chain)

### B. Chain-wide verification — "is the audit trail itself in good shape?"

Triggered by the 🔍 VERIFY CHAIN button on the Blockchain page. Code: `blockchain_eth.verify_chain()` + `log_manager.verify_all_archives()`.

Three checks rolled into one:

| Check                | What it asks                                                              | Catches                                              |
|----------------------|---------------------------------------------------------------------------|------------------------------------------------------|
| Chain integrity      | "Does every anchor we know about still exist on Ganache with the same SHA?" | Lost Ganache data (e.g. volume wiped)                |
| Completeness         | "Did any event fail to anchor at all?"                                    | Ganache was unreachable when we tried to write       |
| Disk integrity       | "Does every archived file on disk still match its on-chain anchor?"       | Tampered files, deleted files                        |

It returns a JSON summary like:

```json
{
  "intact":  false,
  "entries": 147,
  "breaks":  [],
  "errored": 0,
  "tampered_files": [
    {
      "filename":      "20260518_073935_B737-106.jsonl",
      "category":      "normal",
      "expected_hash": "1926136f00e2b1bf…",
      "current_hash":  "76f2f5ffd00ce276…",
      "tx_hash":       "0x82be2b05…"
    }
  ],
  "missing_files": [],
  "message": "Issues: 1 tampered file(s) on disk."
}
```

---

## 6. What this gives you — and what it doesn't

**Detected:**
- Any edit to an archived flight file (per-file or chain verify)
- Any silently-deleted archived file (chain verify, "missing")
- Loss of Ganache state (chain verify, "breaks")
- Server events that never reached the chain because Ganache was down (chain verify, "errored")

**Not detected:**
- Edits to a *live* file (still in `live_cache/`, not yet archived). Live frames are anchored individually, but the live file itself isn't a single artefact yet.
- Someone deleting the `flight_logs/` directory **and** wiping Ganache's `ganache_data` volume in one go. With both gone, there's nothing to compare against. In production you'd back up the Ganache state to a separate, write-once location.
- The on-chain `from` address being compromised. Whoever steals `ETH_PRIVATE_KEY` can sign new anchors *as us*. Treat that key like any other secret.

---

## 7. Quick command recipes

```bash
# See Ganache status + chain ID
curl -s http://localhost:8000/api/eth/status | jq

# List every anchor we've made
curl -s http://localhost:8000/api/eth/anchors | jq '.count, .anchors[0:3]'

# Fetch the raw tx for one anchor straight from Ganache
curl -s -X POST http://localhost:8000/api/eth/rpc \
  -H 'Content-Type: application/json' \
  -d '{"method":"eth_getTransactionByHash","params":["0x55cdfe5d…"]}' | jq

# Run full chain + disk verify
curl -s http://localhost:8000/api/chain/verify | jq

# Verify a single file
curl -s http://localhost:8000/api/verify/normal/20260518_073932_B737-424.jsonl | jq
```
