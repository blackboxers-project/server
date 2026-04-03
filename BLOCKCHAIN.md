# How the Blockchain Works

## Part 1: Blockchain in Plain English

### The Problem

Imagine you write something in a notebook. Someone could rip out a page, change a word, or add fake entries — and nobody would ever know.

Digital files have the same problem. A flight log is just a text file. Anyone with access could open it, delete a suspicious line, and save it. The evidence is gone forever.

**Blockchain solves this: it makes tampering detectable.**

### The Core Idea: Hashing

A **hash** is a digital fingerprint. You feed any data into a hash function, and it spits out a fixed-length string of characters.

```
Input:  "altitude: 35000, squawk: 1200"
Output: a1b2c3d4e5f6...  (64 characters, always)
```

Three critical properties:

1. **Deterministic** — The same input ALWAYS produces the same hash.
2. **Irreversible** — You cannot reverse-engineer the original data from the hash.
3. **Avalanche effect** — Change one single character in the input, and the entire hash changes completely.

```
"altitude: 35000" -> 7f83b1657ff1fc...
"altitude: 35001" -> 2c26b46b68ffc6...  (completely different!)
```

This means if you store the hash of a file, you can later recalculate it. If the hashes match, the file hasn't been touched. If they differ — someone tampered with it.

### The Chain: Why "Block-CHAIN"

A single hash proves one file is intact. But what stops someone from tampering with the hash record itself?

**Answer: you chain them together.**

Each new entry includes the hash of the previous entry. This creates a chain:

```
Entry 1:
  data: "Flight started"
  chain_link: "GENESIS_BLOCK"           <-- first entry, no previous hash

Entry 2:
  data: "Altitude 5000ft"
  chain_link: hash(Entry 1)             <-- locked to Entry 1

Entry 3:
  data: "Altitude 10000ft"
  chain_link: hash(Entry 2)             <-- locked to Entry 2

Entry 4:
  data: "Flight landed"
  chain_link: hash(Entry 3)             <-- locked to Entry 3
```

Now imagine someone tries to modify Entry 2. The moment they change anything:

- The hash of Entry 2 changes
- But Entry 3 still contains the OLD hash of Entry 2
- The chain is broken — **tampering detected**

To hide the modification, they'd need to rewrite Entry 2, then recalculate Entry 3, then Entry 4, and every single entry after that. With thousands of entries being added constantly, this becomes practically impossible.

### The Genesis Block

Every chain needs a starting point. The very first entry has no previous entry to link to, so it uses a special placeholder:

```
"GENESIS_BLOCK_000000000000000000000000"
```

This is the anchor of the entire chain. Every entry that follows ultimately traces back to this origin.

### Summary of Blockchain Principles

| Concept | What it does |
|---------|-------------|
| **Hash** | Creates a unique fingerprint of data |
| **Chain link** | Each entry contains the hash of the previous entry |
| **Genesis block** | The fixed starting point of the chain |
| **Immutability** | Changing any entry breaks the chain from that point forward |
| **Append-only** | You can only add to the end, never insert or modify |

---

## Part 2: How It's Implemented in BlackBox Sentinel

### The Ledger File

All blockchain data lives in one file:

```
flight_logs/secure_ledger.jsonl
```

This is a **JSONL** file (JSON Lines) — each line is one independent JSON object. One line = one block in the chain.

### What Gets Recorded

Every action in the system creates a blockchain entry:

| Action | When it fires |
|--------|--------------|
| `SYSTEM_STARTUP` | Server boots up |
| `FLIGHT_STARTED` | A plane connects via WebSocket |
| `LOG_ENTRY` | Every single telemetry data point received |
| `FLIGHT_ARCHIVED` | A flight ends and the log file is moved to its category |
| `STANDARD_OPS_REGISTERED` | A normal flight is explicitly registered in the blockchain |
| `LIST_VIEWED` | Someone views the log list |
| `INTEGRITY_CHECK` | Someone verifies a file |
| `EVIDENCE_DESTROYED` | Someone deletes a log file |

### Anatomy of a Blockchain Entry

Every entry in `secure_ledger.jsonl` looks like this:

```json
{
  "timestamp": "2026-02-13T10:36:52.912847",
  "action": "FLIGHT_ARCHIVED",
  "actor": "SYSTEM",
  "target": "20260213_103652_F16-324.jsonl",
  "evidence_hash": "dcfdd01cfc28190aac78c77a8167e1aa929a08...",
  "details": "Moved to normal",
  "chain_link": "12fe7d8bdb010fea2c9f9c10ccb3a167b57bab..."
}
```

| Field | Purpose |
|-------|---------|
| `timestamp` | Exactly when this event happened |
| `action` | What type of event this is |
| `actor` | Who did it (IP address or "SYSTEM") |
| `target` | Which file or plane this relates to |
| `evidence_hash` | SHA-256 fingerprint of the file or data at that moment |
| `details` | Human-readable description |
| `chain_link` | SHA-256 hash of the **entire previous line** in the ledger |

### Telemetry Entries

When a plane sends telemetry data (altitude, gyro, squawk, etc.), the full data is embedded directly in the blockchain:

```json
{
  "timestamp": "2026-02-13T10:36:50.292459",
  "action": "LOG_ENTRY",
  "actor": "SYSTEM",
  "target": "F16-324",
  "evidence_hash": "8b2c4f1a9e3d...",
  "telemetry": {
    "gyro": {"x": 1.105, "y": 0, "z": 0},
    "audio_level": 115,
    "squawk": "1200",
    "altitude": 451,
    "server_ts": "2026-02-13T10:36:50.292459"
  },
  "chain_link": "a7c3e9f2b1d4..."
}
```

The `evidence_hash` is computed from the telemetry data itself. Even if someone modifies the telemetry field, the hash won't match — and the chain link from the next entry won't match either. Double protection.

### The Chain in Action

Here's a real sequence showing how the chain builds:

```
Line 1: SYSTEM_STARTUP
         chain_link: "GENESIS_BLOCK_000000000000000000000000"

Line 2: FLIGHT_STARTED (F16-324)
         chain_link: sha256(Line 1)  ->  "4a2f8c..."

Line 3: LOG_ENTRY (altitude: 451)
         chain_link: sha256(Line 2)  ->  "9b3e1d..."

Line 4: LOG_ENTRY (altitude: 903)
         chain_link: sha256(Line 3)  ->  "f7d2a5..."

Line 5: FLIGHT_ARCHIVED -> standard_ops
         chain_link: sha256(Line 4)  ->  "2e8c4f..."

Line 6: STANDARD_OPS_REGISTERED
         chain_link: sha256(Line 5)  ->  "c1a9b7..."
```

Every line is locked to the one before it. Modify any line, and every line after it becomes invalid.

### How Verification Works

When you click "Verify" on a log file in the dashboard, this happens:

```
1. System reads the file from disk
2. Calculates its SHA-256 hash RIGHT NOW
3. Searches secure_ledger.jsonl for the FLIGHT_ARCHIVED
   or STANDARD_OPS_REGISTERED entry for that file
4. Compares the current hash with the hash recorded at archive time
```

**If they match:** The file is identical to when it was archived. No tampering.

**If they differ:** Someone modified the file after it was archived. The evidence has been compromised.

### Standard Ops in the Blockchain

Normal flights (no emergencies) go to `flight_logs/standard_ops/`. These flights get two blockchain entries when archived:

1. **`FLIGHT_ARCHIVED`** — Records that the file was moved to standard_ops
2. **`STANDARD_OPS_REGISTERED`** — Separately hashes the full file content and locks it into the chain

This means normal flights have the same tamper-proof protection as emergency investigations. A routine flight log is just as protected as a crash recording.

### What the Blockchain Does NOT Do

To be clear about the boundaries:

- **It does not encrypt data.** All entries are plain text, readable by anyone with file access.
- **It does not prevent deletion.** Someone can still delete `secure_ledger.jsonl` entirely. But they cannot selectively edit it without breaking the chain.
- **It is not distributed.** Traditional blockchains (like Bitcoin) are copied across thousands of computers. This is a single-node blockchain — its strength comes from the hash chain, not from distribution.
- **It does not replace backups.** For production use, the ledger file should be backed up to a separate, read-only location.

### The Code

All blockchain logic lives in `ledger.py`. The key functions:

| Function | What it does |
|----------|-------------|
| `calculate_file_hash(filepath)` | SHA-256 fingerprint of any file |
| `get_last_chain_hash()` | Reads the last line of the ledger and hashes it for chaining |
| `log_event(action, target, actor, details)` | Writes a general blockchain entry |
| `log_telemetry(plane_id, data)` | Writes a telemetry data point with embedded flight data |
| `log_standard_ops(filepath, plane_id)` | Registers a standard_ops file in the blockchain |
| `get_original_hash(filename)` | Finds the archived hash of a file for verification |

Thread safety is ensured with a `threading.Lock()` — only one entry can be written at a time, preventing race conditions that could corrupt the chain.

Disk safety is ensured with `flush()` + `fsync()` — data is forced to the physical disk before the function returns, preventing data loss on power failure or crashes.
