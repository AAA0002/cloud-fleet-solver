#!/usr/bin/env python3
"""
ably_logger.py
High-speed asynchronous real-time event publisher for MY AGY AI / AAA AUTO.
Publishes live telemetry, farming events, and sniper alerts to Ably channel 'fleet-live'.
"""

import os
import time
import json
import base64
import threading
import urllib.request
import urllib.error
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

ABLY_API_KEY = os.getenv("ABLY_API_KEY", "").strip()
DEFAULT_CHANNEL = "fleet-live"

_cached_token = None
_token_expiry = 0

def _get_ably_token():
    global _cached_token, _token_expiry
    now = time.time()
    if _cached_token and now < _token_expiry:
        return _cached_token

    if not ABLY_API_KEY or ":" not in ABLY_API_KEY:
        return None

    try:
        key_name, key_secret = ABLY_API_KEY.split(":", 1)
        auth_header = base64.b64encode(ABLY_API_KEY.encode()).decode()
        url = f"https://rest.ably.io/keys/{key_name}/requestToken"
        payload = json.dumps({
            "keyName": key_name,
            "timestamp": int(now * 1000),
            "clientId": "aaa-fleet-runner"
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            token = data.get("token")
            ttl = data.get("expires", 0) / 1000.0 if "expires" in data else (now + 3600)
            _cached_token = token
            _token_expiry = ttl - 60
            return token
    except Exception as e:
        return None

def publish_ably_event(name: str, data: dict, channel: str = DEFAULT_CHANNEL):
    """
    Non-blocking background dispatch to Ably channel.
    """
    def _worker():
        try:
            token = _get_ably_token()
            if not token:
                return

            event_payload = json.dumps({
                "name": name,
                "data": {
                    **data,
                    "timestamp": time.time(),
                    "iso_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                }
            }).encode()

            req = urllib.request.Request(
                f"https://rest.ably.io/channels/{channel}/messages",
                data=event_payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

if __name__ == "__main__":
    print("Testing Ably event publish...")
    publish_ably_event("system_test", {"source": "cli_test", "status": "verified"})
    time.sleep(1.5)
    print("Test event dispatched.")
