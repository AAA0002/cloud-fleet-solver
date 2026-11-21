#!/usr/bin/env python3
"""
scripts/maintenance/sync_tokens_from_cloud.py
=============================================================================
Sync Fresh Cloud MiniApp Tokens to Local Workspace
=============================================================================
Fetches the latest cryptographically signed 24h WebApp tokens from Turso LibSQL SSOT
(`kv_mesh`) and Cloudflare Edge Mesh (`fleet:tokens:{uid}`),
updating local `fleet_miniapp_tokens.json` across all 23 fleet accounts.

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

TURSO_URL = env.get("TURSO_DB_URL") or env.get("TURSO_URL", "")
TURSO_TOKEN = env.get("TURSO_DB_TOKEN") or env.get("TURSO_TOKEN", "")
LOCAL_TOKENS_FILE = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")


def extract_auth_date(tok: Any) -> float:
    if not isinstance(tok, str) or "auth_date=" not in tok:
        return 0.0
    try:
        parsed = urllib.parse.parse_qs(tok)
        return float(parsed.get("auth_date", [0])[0])
    except Exception:
        return 0.0


def robust_json_loads(val: Any) -> Any:
    for _ in range(4):
        if isinstance(val, str):
            val = val.strip()
            if (val.startswith("{") and val.endswith("}")) or (val.startswith("[") and val.endswith("]")):
                try:
                    val = json.loads(val)
                except Exception:
                    break
            else:
                break
        else:
            break
    return val


def main():
    print("=" * 65)
    print("🔄 AAA AUTO — SYNC MINIAPP TOKENS FROM CLOUD TO LOCAL")
    print("=" * 65)

    local_tokens: Dict[str, Dict[str, Any]] = {}
    if os.path.exists(LOCAL_TOKENS_FILE):
        try:
            with open(LOCAL_TOKENS_FILE, "r", encoding="utf-8") as f:
                local_tokens = json.load(f)
            print(f"📖 Loaded {len(local_tokens)} accounts from local fleet_miniapp_tokens.json")
        except Exception as e:
            print(f"⚠️ Warning loading local tokens: {e}")
            local_tokens = {}

    updated_count = 0
    total_new_tokens = 0

    def merge_account_tokens(uid: str, cloud_dict: dict):
        nonlocal updated_count, total_new_tokens
        if not isinstance(cloud_dict, dict) or not uid:
            return
        uid = str(uid)
        if uid == "8881914294":
            return
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

    # 1. Primary: Turso LibSQL SSOT (Zero rate limits, 25M ops/mo)
    if TURSO_URL and TURSO_TOKEN:
        print("🏛️ Fetching latest tokens from Turso LibSQL SSOT...")
        try:
            t_http = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
            t_req = json.dumps({
                "requests": [
                    {"type": "execute", "stmt": {"sql": "SELECT val FROM kv_mesh WHERE key = 'miniapp_tokens' OR key = 'fleet:tokens_state' LIMIT 1;", "args": []}},
                    {"type": "execute", "stmt": {"sql": "SELECT key, val FROM kv_mesh WHERE key LIKE 'fleet:tokens:%' AND key NOT LIKE '%:%:%:%';", "args": []}},
                    {"type": "close"}
                ]
            }).encode()
            req = urllib.request.Request(t_http, data=t_req, headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
                results = data.get("results", [])
                if results and results[0].get("type") == "ok":
                    rows0 = results[0].get("response", {}).get("result", {}).get("rows", [])
                    if rows0:
                        raw_v = rows0[0][0].get("value")
                        if raw_v:
                            d_all = robust_json_loads(raw_v)
                            if isinstance(d_all, dict):
                                print(f"  ✅ Loaded master miniapp_tokens ({len(d_all)} accounts) from Turso LibSQL")
                                for u_id, u_toks in d_all.items():
                                    merge_account_tokens(u_id, u_toks)
                if len(results) > 1 and results[1].get("type") == "ok":
                    rows1 = results[1].get("response", {}).get("result", {}).get("rows", [])
                    if rows1:
                        print(f"  ✅ Scanned {len(rows1)} atomic fleet:tokens:* entries in Turso LibSQL")
                        for r in rows1:
                            k_str = r[0].get("value", "")
                            v_str = r[1].get("value", "")
                            if k_str.count(":") == 2:
                                u_part = k_str.split(":")[2]
                                if u_part.isdigit():
                                    d_u = robust_json_loads(v_str)
                                    if isinstance(d_u, dict):
                                        merge_account_tokens(u_part, d_u)
        except Exception as e_turso:
            print(f"⚠️ Turso LibSQL token fetch note: {e_turso}")

    # 2. Secondary: Cloudflare Edge Mesh Fallback
    print("🌐 Checking Cloudflare Edge Mesh tokens (/api/miniapp/tokens)...")
    cf_endpoints = [
        "https://restore-agy.agorameet.workers.dev/api/miniapp/tokens",
        "https://aaaai2.workers.dev/api/miniapp/tokens"
    ]
    for cf_url in cf_endpoints:
        try:
            req_cf = urllib.request.Request(cf_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req_cf, timeout=5) as cf_resp:
                if cf_resp.status == 200:
                    cf_data = json.loads(cf_resp.read().decode())
                    tok_dict = cf_data.get("tokens", cf_data)
                    if isinstance(tok_dict, dict) and tok_dict:
                        print(f"  ✅ Loaded {len(tok_dict)} accounts from Cloudflare Edge ({cf_url})")
                        for u_id, u_toks in tok_dict.items():
                            merge_account_tokens(u_id, u_toks)
                        break
        except Exception:
            pass

    # Write back updated fleet_miniapp_tokens.json
    with open(LOCAL_TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(local_tokens, f, indent=2)

    print(f"\n✅ Synchronization complete!")
    print(f"   - Accounts updated: {updated_count}")
    print(f"   - Individual tokens refreshed: {total_new_tokens}")
    print(f"   - Total accounts in local file: {len(local_tokens)}")


if __name__ == "__main__":
    main()
