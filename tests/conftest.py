"""
Session-level setup shared by all test modules.

We stub log_manager and ledger so that importing server.py (which calls
log_manager.setup_directories() at import time) does not touch the filesystem.

blockchain_eth is intentionally NOT stubbed here — integration tests need
the real module, and unit tests patch individual functions with monkeypatch.
"""

import sys
from unittest.mock import MagicMock

# ── log_manager stub ────────────────────────────────────────────────────────
_log_manager_stub = MagicMock()
_log_manager_stub.setup_directories = MagicMock(return_value=None)
_log_manager_stub.get_all_logs = MagicMock(return_value={})
sys.modules.setdefault("log_manager", _log_manager_stub)

# ── ledger stub ─────────────────────────────────────────────────────────────
_ledger_stub = MagicMock()
sys.modules.setdefault("ledger", _ledger_stub)
