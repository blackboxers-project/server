# ✈️ BlackBox Sentinel

**Secure Flight Telemetry logging & Forensics System**

**BlackBox Sentinel** is a high-fidelity flight data recorder backend designed for accountability and non-repudiation. It captures real-time telemetry via WebSockets, automatically categorizes flights based on squawk codes/outcomes, and secures every byte of data using a **blockchain-lite immutable ledger**.

---

## 🏗️ System Architecture

The system is built on a "Separation of Concerns" architecture to ensure data integrity even during server crashes.

### Core Components

1. **Connection Manager (`server.py`)**: Handles real-time WebSocket connections from aircraft and dashboards.
2. **Log Manager (`log_manager.py`)**: Manages physical file I/O, forcing OS buffers to flush immediately to disk to prevent data loss.
3. **The Ledger (`ledger.py`)**: A cryptographic auditor that creates a "Chain of Custody." Every file creation, movement, or deletion is hashed and signed.

---

## ✨ Key Features

### 1. 📂 Intelligent Sorting

The system doesn't just dump logs; it analyzes the flight's history. If a pilot squawks `7700` (Emergency) but lands safely, the log is still filed under **EMERGENCY** for review.

* **Standard Ops:** Clean flights.
* **7500:** Hijacking/Security Threats.
* **7600:** Radio Failure.
* **7700:** General Emergency.
* **Crash:** Signal lost while airborne + severe squawk.

### 2. 🔐 Immutable Audit Trail

We utilize a local **append-only ledger** (`secure_ledger.jsonl`).

* **Hashing:** Every log file is fingerprinted using **SHA-256**.
* **Chaining:** Each ledger entry contains the hash of the *previous* entry. If an attacker deletes a line in the audit log, the cryptographic chain breaks.
* **Actor Tracking:** Records the IP address of anyone who requests, views, or deletes a file.

### 3. 🛡️ Tamper Detection (Forensics)

The frontend includes a **"Verify Integrity"** feature.

1. User clicks "Verify" on a archived log.
2. Server calculates the hash of the file *currently on the disk*.
3. Server looks up the *original* hash recorded in the Ledger at the moment of archiving.
4. **Match?** ✅ The file is original.
5. **Mismatch?** 🚨 The file has been altered (e.g., altitude data falsified after the crash).

---

## 🚀 Installation & Setup

### Prerequisites

* Python 3.8+
* `pip`

### 1. Install Dependencies

```bash
pip install fastapi uvicorn websockets jinja2

```

### 2. Project Structure

Ensure your directory looks like this:

```text
/flight_tracker
├── server.py           # Main application entry point
├── log_manager.py      # File I/O handler
├── ledger.py           # Cryptographic security core
├── static/
│   ├── index.html      # Live Dashboard
│   ├── logs.html       # Archives & Verification Interface
│   └── plane.html      # Plane Simulator Client
└── flight_logs/        # (Created automatically) Stores logs & ledger

```

### 3. Run the Server

Start the FastAPI server using Uvicorn:

```bash
uvicorn server:app --reload --host 0.0.0.0 --port 8000

```

---

## 📡 API Documentation

### WebSockets

| Endpoint | Description |
| --- | --- |
| `ws://localhost:8000/ws/plane/{id}` | Connects a plane. Sends JSON telemetry. |
| `ws://localhost:8000/ws/dashboard` | Connects a live monitoring dashboard. |

### REST API

| Method | Endpoint | Description |
| --- | --- | --- |
| **GET** | `/api/logs` | Returns a JSON list of all archived logs, grouped by category. |
| **GET** | `/api/verify/{cat}/{file}` | **Forensic Check.** Compares current file hash vs. Ledger hash. |
| **DELETE** | `/api/logs/{cat}/{file}` | **Destructive.** Deletes a log file (action is permanently recorded in Ledger). |

---

## 🖥️ Usage Guide

### 1. Simulating a Flight

You can create a simple python script to simulate a plane connecting:

```python
# client_sim.py
import websocket, json, time

ws = websocket.WebSocket()
ws.connect("ws://localhost:8000/ws/plane/FLIGHT_777")

# Simulate standard flight
data = {"alt": 1000, "spd": 250, "squawk": "1200", "fuel": 90}
ws.send(json.dumps(data))
time.sleep(1)

# Simulate Emergency
data["squawk"] = "7700" 
ws.send(json.dumps(data))

ws.close()

```

### 2. Verifying Evidence

1. Navigate to `http://localhost:8000/logs`.
2. Locate a flight in the **Emergency** or **Crash** section.
3. Click the **🛡️ VERIFY** button.
* **Green:** The file is pristine.
* **Red:** The file content has been modified since it was archived.



---

## 🔒 Security Specification

The `secure_ledger.jsonl` file follows this schema:

```json
{
  "timestamp": "2023-10-27T14:30:00",
  "action": "FLIGHT_ARCHIVED",
  "actor": "SYSTEM",
  "target": "flight_logs/investigation/7700_emergency/20231027_FLIGHT_777.jsonl",
  "evidence_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "chain_link": "a8f9d..."
}

```

> **⚠️ WARNING:** Manually editing the `secure_ledger.jsonl` file will break the cryptographic chain, alerting administrators that the audit trail itself has been compromised.

---

*Built with ❤️ and Paranoia by Miti*