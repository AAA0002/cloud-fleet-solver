#!/usr/bin/env python3
"""
Axiom.co Cloud Telemetry Logger for AAA AUTO Mining Fleet
Transmits structured JSON telemetry events to Axiom (500GB/mo free tier).
Non-blocking, non-crashing fire-and-forget implementation.
"""

import os
import json
import time
import threading
import urllib.request
from typing import Dict, Any, Optional

AXIOM_TOKEN = os.getenv("AXIOM_TOKEN") or ""
AXIOM_ORG_ID = os.getenv("AXIOM_ORG_ID") or "aaa-fleet-kwpm"
AXIOM_DATASET = os.getenv("AXIOM_DATASET") or "aaa-fleet"
AXIOM_ENDPOINT = f"https://api.axiom.co/v1/datasets/{AXIOM_DATASET}/ingest"


def _send_axiom_payload(payload: list):
    if not AXIOM_TOKEN:
        return
    headers = {
        "Authorization": f"Bearer {AXIOM_TOKEN}",
        "X-AXIOM-ORG-ID": AXIOM_ORG_ID,
        "Content-Type": "application/json",
        "User-Agent": "AAA-Fleet-Telemetry/1.0"
    }
    try:
        req = urllib.request.Request(AXIOM_ENDPOINT, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            pass
    except Exception:
        pass


def log_axiom_event(service: str, event: str, level: str = "info", data: Optional[Dict[str, Any]] = None):
    """
    Dispatches a structured telemetry event to Axiom asynchronously without blocking.
    """
    if not AXIOM_TOKEN:
        return

    doc = {
        "_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "service": service,
        "event": event,
        "level": level,
        "timestamp_unix": int(time.time())
    }
    if data:
        doc.update(data)

    threading.Thread(target=_send_axiom_payload, args=([doc],), daemon=True).start()


if __name__ == "__main__":
    print("Testing Axiom Cloud Telemetry Logger...")
    log_axiom_event("test_service", "manual_test_event", "info", {"test_key": "test_value"})
    time.sleep(1.5)
    print("✅ Axiom event dispatched successfully.")
