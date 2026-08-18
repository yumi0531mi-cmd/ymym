from __future__ import annotations

import os


# Unit and UI tests exercise deterministic parsing and card rendering only. The
# live Yahoo background stream is checked separately during a controlled manual
# market probe, so it must not outlive the pytest interpreter at shutdown.
os.environ.setdefault("SCANNER_ENABLE_YAHOO_STREAM", "false")
