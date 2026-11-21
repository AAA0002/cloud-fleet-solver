#!/usr/bin/env python3
"""
sync_fleet_all_tokens.py
=============================================================================
BIDIRECTIONAL 11-BOT TOKEN SYNCHRONIZER & STATE UNIFIER
=============================================================================
Synchronizes all 11 bot initData tokens across all 23 accounts between:
  1. Upstash Redis `fleet:tokens:{uid}` (individual account keys)
  2. Upstash Redis `miniapp_tokens` & `fleet_miniapp_tokens` (master aggregated dictionaries)
  3. Local `accounts.json`
  4. Local `fleet_miniapp_tokens.json`
  5. Cloudflare Edge KV (via Primary Edge Gateway Node 4 /api/tokens)
"""

import os
import re
import json
import time
import urllib.request
import urllib.error

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

env = {}
env_file = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("\"'")

TURSO_TOKEN = os.getenv("TURSO_DB_TOKEN") or env.get("TURSO_DB_TOKEN") or env.get("TURSO_TOKEN", "")
TURSO_URL = "https://my-agy-fleet-db-aaa2743.aws-ap-south-1.turso.io/v2/pipeline"
CF_SECRET_KEY = os.getenv("CF_SECRET_KEY") or env.get("CF_SECRET_KEY", "")

PRIMARY_EDGE_NODE = "https://restore-agy.agorameet.workers.dev"

TARGET_BOTS = [
    "atf",
    "kynex",
    "pepegram",
    "adxearn",
    "chickenland",
    "earnvault",
    "mexoearn",
    "myage",
    "lifetime",
    "victors",
    "dillo",
    "hunter"
]

EXCLUDED_UIDS = {"8881914294", "tokens"}

TOKEN_KEYS = [
    "atf_init_data",
    "kynex_init_data",
    "kynex_refresh_token",
    "pepegram_init_data",
    "adxearn_init_data",
    "chickenland_init_data",
    "earnvault_init_data",
    "earnvault_refresh_token",
    "earnvault_cents",
    "mexo_init_data",
    "mexo_token",
    "myage_init_data",
    "lifetime_energy_init_data",
    "lifetime_init_data",
    "victors_init_data",
    "victors_pass",
    "victors_human_pass",
    "dillo_init_data",
    "dillo",
    "hunter_init_data",
    "hunter_jwt",
    # Aliases
    "pepe_init_data",
    "adx_init_data",
    "chicken_init_data",
    "ev_init_data",
    "mexoearn_init_data",
    "myageapp_init_data"
]


def get_token_timestamp(val) -> int:
    """Extracts auth_date UNIX timestamp from WebApp token string for freshness comparison."""
    if not val or not isinstance(val, str):
        return 0
    m = re.search(r"auth_date(?:%3D|=)(\d+)", val)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    return 1


def pick_best_token(*candidates):
    """Selects the candidate token with the highest (freshest) auth_date timestamp or best non-empty value."""
    best_val = None
    best_ts = -1
    for c in candidates:
        if c is None or c == "":
            continue
        if isinstance(c, (int, float)):
            return c
        if isinstance(c, str):
            ts = get_token_timestamp(c)
            if ts > best_ts:
                best_ts = ts
                best_val = c
        elif c:
            best_val = c
    return best_val or ""

from turso_mesh import turso_get, turso_set, turso_keys, turso_mget

# Turso LibSQL Primary SSOT Key-Value Mesh
upstash_get = turso_get
upstash_set = turso_set
upstash_get_keys = turso_keys

def main():
    print("=" * 70)
    print("🚀 AAA AUTO FLEET 12-BOT TOKEN SYNCHRONIZER & STATE UNIFIER")
    print("=" * 70)

    # 1. Load accounts.json
    accs_file = os.path.join(BASE_DIR, "accounts.json")
    accs = []
    if os.path.exists(accs_file):
        with open(accs_file, "r", encoding="utf-8") as f:
            accs = json.load(f)
    print(f"📁 Loaded {len(accs)} accounts from accounts.json")

    # Map accounts by str(account_id)
    accs_by_id = {}
    for a in accs:
        uid = str(a.get("account_id") or a.get("id") or a.get("uid", "")).strip()
        if uid and uid != "null" and uid not in EXCLUDED_UIDS:
            accs_by_id[uid] = a

    # 2. Batch-load individual Turso LibSQL keys `fleet:tokens:*`
    redis_keys = turso_keys("fleet:tokens:*")
    print(f"🌐 Found {len(redis_keys)} fleet:tokens keys in Turso LibSQL SSOT")

    redis_tokens_by_id = {}
    # Batch-fetch keys in chunks of 50 via turso_mget for sub-second efficiency
    for i in range(0, len(redis_keys), 50):
        chunk = redis_keys[i:i + 50]
        batch_vals = turso_mget(chunk)
        for k, data in batch_vals.items():
            parts = k.split(":")
            if len(parts) == 3:  # fleet:tokens:{uid}
                uid = parts[2].strip()
                if uid in EXCLUDED_UIDS or uid == "null" or not uid:
                    continue
                if data and isinstance(data, dict):
                    if uid not in redis_tokens_by_id:
                        redis_tokens_by_id[uid] = {}
                    redis_tokens_by_id[uid].update(data)
            elif len(parts) == 4:  # fleet:tokens:{uid}:{token_key}
                uid = parts[2].strip()
                if uid in EXCLUDED_UIDS or uid == "null" or not uid:
                    continue
                token_key = parts[3]
                if data:
                    if uid not in redis_tokens_by_id:
                        redis_tokens_by_id[uid] = {}
                    redis_tokens_by_id[uid][token_key] = data

    print(f"📦 Successfully batch-fetched tokens for {len(redis_tokens_by_id)} accounts from Turso LibSQL")

    # 3. Load existing miniapp_tokens from Turso LibSQL
    miniapp_tokens = turso_get("miniapp_tokens") or {}
    if isinstance(miniapp_tokens, str):
        try:
            miniapp_tokens = json.loads(miniapp_tokens)
        except Exception:
            miniapp_tokens = {}
    if not isinstance(miniapp_tokens, dict):
        miniapp_tokens = {}
    miniapp_tokens = {str(k): v for k, v in miniapp_tokens.items() if str(k).isdigit() and str(k) not in EXCLUDED_UIDS}
    print(f"📦 Existing Upstash miniapp_tokens entries: {len(miniapp_tokens)}")

    # 3b. Load existing local fleet_miniapp_tokens.json
    loc_tokens_file = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")
    fmt_tokens = {}
    if os.path.exists(loc_tokens_file):
        try:
            with open(loc_tokens_file, "r", encoding="utf-8") as f:
                fmt_tokens = json.load(f)
            fmt_tokens = {str(k): v for k, v in fmt_tokens.items() if str(k).isdigit() and str(k) not in EXCLUDED_UIDS}
        except Exception as e:
            print(f"  ⚠️ Error loading fleet_miniapp_tokens.json: {e}")
    print(f"📦 Existing local fleet_miniapp_tokens entries: {len(fmt_tokens)}")

    # 4. Bidirectional merge for all known accounts
    all_uids = {str(u) for u in (set(accs_by_id.keys()) | set(redis_tokens_by_id.keys()) | set(miniapp_tokens.keys()) | set(fmt_tokens.keys())) if str(u).isdigit() and str(u) not in EXCLUDED_UIDS}
    print(f"🎯 Total unique accounts across all sources: {len(all_uids)}")

    master_miniapp_tokens = {}
    updated_accs = []

    for uid in sorted(all_uids, key=lambda x: str(x)):
        acc = accs_by_id.get(uid, {})
        redis_tok = redis_tokens_by_id.get(uid, {})
        m_tok = miniapp_tokens.get(uid, {})
        fmt_tok = fmt_tokens.get(uid, {})

        acc = acc if isinstance(acc, dict) else {}
        redis_tok = redis_tok if isinstance(redis_tok, dict) else (json.loads(redis_tok) if isinstance(redis_tok, str) and redis_tok.startswith("{") else {})
        m_tok = m_tok if isinstance(m_tok, dict) else (json.loads(m_tok) if isinstance(m_tok, str) and m_tok.startswith("{") else {})
        fmt_tok = fmt_tok if isinstance(fmt_tok, dict) else (json.loads(fmt_tok) if isinstance(fmt_tok, str) and fmt_tok.startswith("{") else {})

        # Merge tokens: select freshest token based on auth_date timestamp
        merged_tokens = {}

        # Collect all token fields, selecting newest token across local, redis, and mesh
        for k in TOKEN_KEYS:
            val = pick_best_token(fmt_tok.get(k), redis_tok.get(k), acc.get(k), m_tok.get(k))
            if val:
                merged_tokens[k] = val

        # Ensure canonical aliases are populated
        if merged_tokens.get("pepegram_init_data") and not merged_tokens.get("pepe_init_data"):
            merged_tokens["pepe_init_data"] = merged_tokens["pepegram_init_data"]
        elif merged_tokens.get("pepe_init_data") and not merged_tokens.get("pepegram_init_data"):
            merged_tokens["pepegram_init_data"] = merged_tokens["pepe_init_data"]

        if merged_tokens.get("adxearn_init_data") and not merged_tokens.get("adx_init_data"):
            merged_tokens["adx_init_data"] = merged_tokens["adxearn_init_data"]
        elif merged_tokens.get("adx_init_data") and not merged_tokens.get("adxearn_init_data"):
            merged_tokens["adxearn_init_data"] = merged_tokens["adx_init_data"]

        if merged_tokens.get("chickenland_init_data") and not merged_tokens.get("chicken_init_data"):
            merged_tokens["chicken_init_data"] = merged_tokens["chickenland_init_data"]
        elif merged_tokens.get("chicken_init_data") and not merged_tokens.get("chickenland_init_data"):
            merged_tokens["chickenland_init_data"] = merged_tokens["chicken_init_data"]

        if merged_tokens.get("earnvault_init_data") and not merged_tokens.get("ev_init_data"):
            merged_tokens["ev_init_data"] = merged_tokens["earnvault_init_data"]
        elif merged_tokens.get("ev_init_data") and not merged_tokens.get("earnvault_init_data"):
            merged_tokens["earnvault_init_data"] = merged_tokens["ev_init_data"]

        if merged_tokens.get("mexo_init_data") and not merged_tokens.get("mexoearn_init_data"):
            merged_tokens["mexoearn_init_data"] = merged_tokens["mexo_init_data"]
        elif merged_tokens.get("mexoearn_init_data") and not merged_tokens.get("mexo_init_data"):
            merged_tokens["mexo_init_data"] = merged_tokens["mexoearn_init_data"]

        if merged_tokens.get("myage_init_data") and not merged_tokens.get("myageapp_init_data"):
            merged_tokens["myageapp_init_data"] = merged_tokens["myage_init_data"]
        elif merged_tokens.get("myageapp_init_data") and not merged_tokens.get("myage_init_data"):
            merged_tokens["myage_init_data"] = merged_tokens["myageapp_init_data"]

        if merged_tokens.get("lifetime_energy_init_data") and not merged_tokens.get("lifetime_init_data"):
            merged_tokens["lifetime_init_data"] = merged_tokens["lifetime_energy_init_data"]
        elif merged_tokens.get("lifetime_init_data") and not merged_tokens.get("lifetime_energy_init_data"):
            merged_tokens["lifetime_energy_init_data"] = merged_tokens["lifetime_init_data"]

        if merged_tokens.get("dillo_init_data") and not merged_tokens.get("dillo"):
            merged_tokens["dillo"] = merged_tokens["dillo_init_data"]
        elif merged_tokens.get("dillo") and not merged_tokens.get("dillo_init_data"):
            merged_tokens["dillo_init_data"] = merged_tokens["dillo"]

        name = acc.get("name") or redis_tok.get("name") or m_tok.get("name") or fmt_tok.get("name") or f"Account_{uid}"
        phone = acc.get("phone") or redis_tok.get("phone") or m_tok.get("phone") or fmt_tok.get("phone") or ""

        # Update accounts.json entry
        if not acc:
            acc = {
                "account_id": uid,
                "name": name,
                "phone": phone
            }
        for k, v in merged_tokens.items():
            acc[k] = v
        acc["miniapp_tokens_synced"] = True
        updated_accs.append(acc)

        # Update master_miniapp_tokens entry
        entry = {
            "account_id": uid,
            "uid": uid,
            "name": name,
            "phone": phone,
            "synced_at": time.time(),
            "updated_at": time.time(),
            **merged_tokens
        }
        master_miniapp_tokens[uid] = entry

        # Update individual Upstash Redis key if missing any bot tokens
        needs_redis_update = False
        for k, v in merged_tokens.items():
            if k not in redis_tok:
                needs_redis_update = True
                break
        if needs_redis_update:
            updated_redis_payload = {**redis_tok, **entry}
            upstash_set(f"fleet:tokens:{uid}", updated_redis_payload)

    # 5. Save updated accounts.json
    with open(accs_file, "w", encoding="utf-8") as f:
        json.dump(updated_accs, f, indent=2)
    print(f"✅ Saved updated accounts.json ({len(updated_accs)} accounts)")

    # 6. Save fleet_miniapp_tokens.json
    miniapp_json_file = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")
    with open(miniapp_json_file, "w", encoding="utf-8") as f:
        json.dump(master_miniapp_tokens, f, indent=2)
    print(f"✅ Saved local fleet_miniapp_tokens.json ({len(master_miniapp_tokens)} accounts)")

    # 7. Write master_miniapp_tokens to Turso LibSQL Primary SSOT
    t1 = turso_set("miniapp_tokens", master_miniapp_tokens)
    t2 = turso_set("fleet:tokens_state", master_miniapp_tokens)
    print(f"✅ Synced master dictionary to Turso miniapp_tokens (success={t1})")
    print(f"✅ Synced master dictionary to Turso fleet:tokens_state (success={t2})")

    # 8. Push to Primary Edge Gateway Node 4 (/api/tokens)
    print(f"🌐 Syncing all {len(master_miniapp_tokens)} accounts to Edge Gateway Node 4 KV...")
    synced_edge = 0
    headers = {
        "Authorization": f"Bearer {CF_SECRET_KEY}",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        req = urllib.request.Request(
            f"{PRIMARY_EDGE_NODE}/api/tokens",
            data=json.dumps(master_miniapp_tokens).encode("utf-8"),
            headers=headers
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                synced_edge = len(master_miniapp_tokens)
    except Exception as ee:
        print(f"  ⚠️ Edge sync notice: {ee}")

    print(f"✅ Synced {synced_edge}/{len(master_miniapp_tokens)} accounts directly to Edge Gateway Node 4 KV")

    # 9. Audit and verify final state
    print("\n" + "=" * 70)
    print("📊 FINAL VERIFICATION: 12-BOT COVERAGE AUDIT")
    print("=" * 70)
    for bot in TARGET_BOTS:
        acc_c = sum(1 for a in updated_accs if a.get(f"{bot}_init_data") or a.get(f"{bot}_token") or a.get(bot))
        miniapp_c = sum(1 for uid, d in master_miniapp_tokens.items() if d.get(f"{bot}_init_data") or d.get(f"{bot}_token") or d.get(bot))
        status = "✅" if acc_c >= 21 and miniapp_c >= 21 else "⚠️"
        print(f"  {status} {bot:12}: accounts.json={acc_c}/{len(updated_accs)} | miniapp_tokens={miniapp_c}/{len(updated_accs)}")

    print("\n🎉 Token state synchronization and database unification complete!")

if __name__ == "__main__":
    main()
