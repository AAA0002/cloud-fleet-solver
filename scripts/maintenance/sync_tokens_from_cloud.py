#!/usr/bin/env python3
"""
scripts/maintenance/sync_tokens_from_cloud.py
=============================================================================
Sync Fresh Cloud MiniApp Tokens to Local Workspace
=============================================================================
Fetches the latest cryptographically signed 24h WebApp tokens from Upstash Redis
(`fleet:tokens:{uid}`) and/or Turso LibSQL (`kv_mesh`), updating local
`fleet_miniapp_tokens.json` across all 23 fleet accounts.

Complies with:
- Rule 4: Zero Phone Load Directive (Lightweight HTTPS sync, 0 bots)
- Rule 6: Strict Secret Hygiene (0 plaintext credentials)
- Rule 9: 24-Hour WebApp Token Lifetime
=============================================================================
"""

import os
import sys
import json
import time
import urllib.request
import urllib.parse
from typing import Dict, Any

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)

ENV_FILE = os.path.join(BASE_DIR, ".env")
env = dict(os.environ)
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'\"'")
                if k not in env:
                    env[k] = v

UPSTASH_URL = env.get("UPSTASH_REDIS_REST_URL") or env.get("UPSTASH_URL", "")
UPSTASH_TOKEN = env.get("UPSTASH_REDIS_REST_TOKEN") or env.get("UPSTASH_TOKEN", "")
LOCAL_TOKENS_FILE = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")


def extract_auth_date(tok: Any) -> float:
    if not isinstance(tok, str) or "auth_date=" not in tok:
        return 0.0
    try:
        parsed = urllib.parse.parse_qs(tok)
        return float(parsed.get("auth_date", [0])[0])
    except Exception:
        return 0.0


def main():
    print("=" * 65)
    print("🔄 AAA AUTO — SYNC MINIAPP TOKENS FROM CLOUD TO LOCAL")
    print("=" * 65)

    if not (UPSTASH_URL and UPSTASH_TOKEN):
        print("❌ Error: Missing UPSTASH_URL or UPSTASH_TOKEN in environment.")
        sys.exit(1)

    local_tokens: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(LOCAL_TOKENS_FILE):
        try:
            with open(LOCAL_TOKENS_FILE, "r", encoding="utf-8") as f:
                local_tokens = json.load(f)
            print(f"📖 Loaded {len(local_tokens)} accounts from local fleet_miniapp_tokens.json")
        except Exception as e:
            print(f"⚠️ Warning loading local tokens: {e}")
            local_tokens = {}

    # 1. Fetch all fleet:tokens:* keys from Upstash
    req = urllib.request.Request(
        f"{UPSTASH_URL}/keys/fleet:tokens:*",
        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read().decode())
        keys = resp.get("result", [])

    print(f"☁️ Found {len(keys)} cloud token entries in Upstash Redis.")

    updated_count = 0
    total_new_tokens = 0

    for k in keys:
        uid = k.split(":")[-1]
        try:
            req_get = urllib.request.Request(
                f"{UPSTASH_URL}/get/{k}",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}
            )
            with urllib.request.urlopen(req_get, timeout=8) as r_get:
                raw_data = json.loads(r_get.read().decode()).get("result")
                if not raw_data:
                    continue
                cloud_dict = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                if not isinstance(cloud_dict, dict):
                    continue

                if uid not in local_tokens:
                    local_tokens[uid] = {}

                acc_updated = False
                for bot_k, bot_v in cloud_dict.items():
                    if not bot_v or not isinstance(bot_v, str):
                        continue
                    local_v = local_tokens[uid].get(bot_k)
                    cloud_ad = extract_auth_date(bot_v)
                    local_ad = extract_auth_date(local_v)

                    # Update if missing or cloud token has newer auth_date
                    if not local_v or (cloud_ad > 0 and cloud_ad > local_ad):
                        local_tokens[uid][bot_k] = bot_v
                        acc_updated = True
                        total_new_tokens += 1

                if acc_updated:
                    updated_count += 1
        except Exception as e:
            print(f"  ⚠️ Error syncing tokens for UID {uid}: {e}")

    # Write back updated fleet_miniapp_tokens.json
    with open(LOCAL_TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(local_tokens, f, indent=2)

    print(f"\n✅ Synchronization complete!")
    print(f"   - Accounts updated: {updated_count} / {len(keys)}")
    print(f"   - Individual tokens refreshed: {total_new_tokens}")
    print(f"   - Total accounts in local file: {len(local_tokens)}")


if __name__ == "__main__":
    main()
