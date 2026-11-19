#!/usr/bin/env python3
"""
scripts/maintenance/dillo_referral_validator.py
=============================================================================
DILLO MINER FLEET-WIDE REFERRAL HARVESTER & WITHDRAWAL UNLOCKER
=============================================================================
Iterates across all 23 fleet accounts:
1. Pulls live state for each account.
2. Taps max available energy (n = energy) to maximize mining output.
3. Claims daily check-in (+1,200 to +12,000 DILLO).
4. Claims free side-tasks if available.
5. Executes 'collect' to harvest mined pool into personal balance and advance
   referral progression to Step 2 ('✓ a valid friend').
6. Checks Master Account (6727787768):
   - When valid friends >= 1: executes withdrawal of pool wallet balance!
   - When GRAM balance >= 0.05: executes gram_withdraw!
=============================================================================
"""

import os
import sys
import json
import time
import random
import urllib.request
import urllib.error

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)

MASTER_UID = "6727787768"
API_URL = "https://dillo-miner-bot.ih0st.app/api/app/call"

def call_dillo(init_data: str, event: str, data: dict = None) -> dict:
    if data is None:
        data = {}
    payload = {
        "initData": init_data,
        "event": event,
        "data": data
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
            "Origin": "https://dillo-miner-bot.ih0st.app",
            "Referer": "https://dillo-miner-bot.ih0st.app/"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "error": f"HTTP_{e.code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def robust_json_loads(val):
    res = val
    for _ in range(3):
        if isinstance(res, str):
            try:
                res = json.loads(res)
            except Exception:
                break
        else:
            break
    return res if isinstance(res, (dict, list)) else {}

def load_data():
    tok_file = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")
    acc_file = os.path.join(BASE_DIR, "accounts.json")
    tokens = {}
    accounts = []
    if os.path.exists(tok_file):
        try:
            with open(tok_file, "r", encoding="utf-8") as f:
                tokens = json.load(f)
        except Exception:
            pass
    if os.path.exists(acc_file):
        try:
            with open(acc_file, "r", encoding="utf-8") as f:
                accounts = json.load(f)
        except Exception:
            pass

    # Cloud fallback for GitHub Actions / cloud runners
    up_url = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_URL")
    up_tok = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_TOKEN")
    if (not accounts or not tokens) and up_url and up_tok:
        try:
            import urllib.request
            headers = {"Authorization": f"Bearer {up_tok}"}
            if not accounts:
                req_a = urllib.request.Request(f"{up_url}/get/fleet:accounts", headers=headers)
                with urllib.request.urlopen(req_a, timeout=6) as r:
                    d = json.loads(r.read().decode())
                    res_a = robust_json_loads(d.get("result"))
                    if isinstance(res_a, list):
                        accounts = res_a
            if not tokens:
                req_t = urllib.request.Request(f"{up_url}/get/miniapp_tokens", headers=headers)
                with urllib.request.urlopen(req_t, timeout=6) as r:
                    d = json.loads(r.read().decode())
                    res_t = robust_json_loads(d.get("result"))
                    if isinstance(res_t, dict):
                        tokens = res_t
        except Exception:
            pass

    return tokens, accounts

def main():
    print("=" * 75)
    print("⛏️ DILLO MINER FLEET-WIDE REFERRAL PROGRESSION & HARVEST ENGINE")
    print("=" * 75)
    tokens, accounts = load_data()

    total_tapped = 0
    total_collected = 0.0
    gate_blocked_count = 0
    valid_friends_count = 0

    # 1. Sweep worker accounts first to advance Step 2
    for idx, acc in enumerate(accounts, 1):
        uid = str(acc.get("user_id") or acc.get("id"))
        name = acc.get("name") or acc.get("first_name") or f"Account #{idx}"
        tok = tokens.get(uid, {}).get("dillo_init_data") or tokens.get(uid, {}).get("dillo")
        if not tok:
            continue

        print(f"\n[{idx}/{len(accounts)}] ⛏️ {name} ({uid})")
        
        # State
        st_resp = call_dillo(tok, "state", {})
        if not st_resp.get("ok"):
            print(f"  ❌ State error: {st_resp.get('error')}")
            continue

        d = st_resp.get("data", {})
        me = d.get("me", {})
        energy = me.get("energy", 0)
        pending = me.get("pending", 0)
        mined = me.get("mined", 0)
        pool = me.get("pool", 0)
        gate = d.get("gate", {})
        gate_passed = gate.get("passed", False)
        missing = [m.get("name") for m in gate.get("missing", [])]

        print(f"  📊 Pool: {pool:.2f} | Mined: {mined:.2f} | Pending: {pending:.2f} | Energy: {energy}")

        # Checkin
        ci_res = call_dillo(tok, "checkin", {})
        if ci_res.get("ok") and ci_res.get("data", {}).get("checked"):
            reward = ci_res.get("data", {}).get("reward", 0)
            print(f"  📅 Check-in claimed: +{reward:,} DILLO")

        # Tap energy
        if energy > 0:
            tap_res = call_dillo(tok, "tap", {"n": energy})
            if tap_res.get("ok"):
                total_tapped += energy
                print(f"  ⚡ Tapped {energy} energy -> Pending updated")

        # Collect
        col_res = call_dillo(tok, "collect", {})
        if col_res.get("ok") and col_res.get("data", {}).get("collected", 0) > 0:
            c_amt = col_res.get("data", {}).get("collected", 0)
            total_collected += c_amt
            print(f"  🎉 HARVEST SUCCESSFUL: +{c_amt:.2f} DILLO collected! Step 2 validated!")
        elif col_res.get("data", {}).get("error") == "gate":
            gate_blocked_count += 1
            print(f"  ⏳ Sponsor Gate Active: {missing} (waiting for rotation / pass)")
        else:
            err = col_res.get("data", {}).get("error") or col_res.get("error", "no_harvest")
            print(f"  ℹ️ Collect note: {err}")

    # 2. Check Master Account Status & Unlocks
    print("\n" + "=" * 75)
    print("👑 MASTER ACCOUNT STATUS & WITHDRAWAL GATE AUDIT")
    print("=" * 75)
    master_tok = tokens.get(MASTER_UID, {}).get("dillo_init_data")
    if master_tok:
        m_state = call_dillo(master_tok, "state", {})
        m_d = m_state.get("data", {})
        m_me = m_d.get("me", {})
        m_pool = m_me.get("pool", 0)
        m_friends = m_me.get("friends", 0)
        m_pass = m_d.get("pass", {})
        m_gate = m_d.get("gate", {})
        m_rows = m_me.get("rows", [])
        
        valid_friends_count = m_friends
        print(f"  👑 Master Pool Wallet: {m_pool:,.2f} DILLO")
        print(f"  👥 Total Connected Friends: {len(m_rows)}")
        print(f"  ✅ Valid Friends Count: {m_friends}")
        print(f"  🚪 Withdrawal Pass Requirements: {m_pass}")

        # Check GRAM status
        gram_res = call_dillo(master_tok, "gram", {})
        g_d = gram_res.get("data", {}).get("gram", {})
        g_bal = g_d.get("bal", 0)
        g_min = g_d.get("min", 0.05)
        print(f"  💎 Master GRAM Balance: {g_bal:.4f} GRAM (min to wd: {g_min:.4f})")

        if m_friends >= 1:
            print("  🔓 WITHDRAWAL GATE UNLOCKED! Valid friends requirement fulfilled!")
            if g_bal >= g_min:
                req_code = "r" + str(int(time.time())) + str(random.randint(1000, 9999))
                g_wd = call_dillo(master_tok, "gram_withdraw", {"req": req_code})
                print(f"  🚀 GRAM WITHDRAWAL DISPATCHED: {g_wd}")
        else:
            print(f"  ⏳ 1 more valid friend required to unlock pool withdrawal.")

    print("\n" + "=" * 75)
    print(f"📋 HARVEST COMPLETE: Tapped {total_tapped} energy | Collected {total_collected:.2f} DILLO | Valid friends: {valid_friends_count}")
    print("=" * 75)

if __name__ == "__main__":
    main()
