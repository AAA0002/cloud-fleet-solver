#!/usr/bin/env python3
"""
scripts/maintenance/chickenland_24h_withdrawal_sniper.py
=============================================================================
CHICKEN LAND AUTONOMOUS 24-HOUR WITHDRAWAL WATCHDOG & SNIPER
=============================================================================
Strictly dedicated to:
1. Master Account: Arif Hossain (6727787768)
2. Koram Shah: (7648254021)
Target TON Vault: UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT

Protocol Rules & Constraints:
- 1 on-chain GRAM withdrawal per 24 hours per account (perDay: 1).
- Server lock enforced by 'nextAt' unix timestamp.
- When countdown expires (now >= nextAt):
  1. Executes /api/sell to convert nest eggs into spendable egg balance.
  2. Submits /api/withdraw for full spendable egg balance (>= 15,020 eggs).
  3. Records transaction receipt into payout ledger channel.
=============================================================================
"""

import os
import sys
import json
import time
import math
import random
import urllib.request
import urllib.error

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, BASE_DIR)

from chickenland_miner import ChickenLandMiner

TARGET_WALLET = "UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT"
ACCOUNTS_MAP = {
    "6727787768": {"name": "Arif Hossain (Master)", "memo": "C"},
    "7648254021": {"name": "Koram Shah", "memo": ""}
}

def send_telegram_alert(text: str):
    """Dispatches concise operational card to AAA Admin notification channel (-1004358687850)."""
    bot_token = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN") or ""
    channel = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
    if not bot_token:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = json.dumps({"chat_id": channel, "text": text, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            pass
    except Exception as e:
        print(f"  ⚠️ Notification dispatch warning: {e}")


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


def load_tokens():
    tokens = {}
    # 1. Cloud-First: Fetch fresh tokens from Upstash Redis
    up_url = os.getenv("UPSTASH_URL") or os.getenv("UPSTASH_REDIS_REST_URL")
    up_token = os.getenv("UPSTASH_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if up_url and up_token:
        try:
            req = urllib.request.Request(f"{up_url.rstrip('/')}/get/miniapp_tokens", headers={"Authorization": f"Bearer {up_token}"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode())
                parsed = robust_json_loads(data.get("result"))
                if isinstance(parsed, dict):
                    tokens.update(parsed)
        except Exception as e:
            print(f"⚠️ Note: Failed to load miniapp_tokens from Upstash Redis: {e}")

        # Also probe fleet:tokens:{uid} for dedicated accounts
        for uid in ACCOUNTS_MAP.keys():
            try:
                req_t = urllib.request.Request(f"{up_url.rstrip('/')}/get/fleet:tokens:{uid}", headers={"Authorization": f"Bearer {up_token}"})
                with urllib.request.urlopen(req_t, timeout=4) as resp_t:
                    d_t = json.loads(resp_t.read().decode())
                    p_t = robust_json_loads(d_t.get("result"))
                    if isinstance(p_t, dict) and p_t:
                        tokens.setdefault(uid, {}).update(p_t)
            except Exception:
                pass

    # 2. Local Fallback: Merge local fleet_miniapp_tokens.json
    tok_file = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")
    if os.path.exists(tok_file):
        try:
            with open(tok_file, "r", encoding="utf-8") as f:
                local_tok = json.load(f)
                if isinstance(local_tok, dict):
                    for k, v in local_tok.items():
                        if k not in tokens:
                            tokens[k] = v
                        elif isinstance(v, dict):
                            tokens[k].update({k2: v2 for k2, v2 in v.items() if k2 not in tokens[k]})
        except Exception:
            pass

    return tokens

def sweep_account(uid: str, info: dict, tokens_db: dict):
    name = info["name"]
    memo = info["memo"]
    tok_entry = tokens_db.get(uid, {})
    init_data = tok_entry.get("chicken_init_data") or tok_entry.get("chickenland_init_data")
    if not init_data:
        print(f"[{uid}] ❌ No Chicken Land initData found.")
        return {"status": "no_token"}

    miner = ChickenLandMiner(init_data, user_id=uid)
    print(f"\n🐔 [{name} ({uid})] Checking withdrawal status...")

    # 1. Harvest & sell any newly laid eggs to maximize spendable egg balance
    sell_res = miner.rpc("/api/sell", {})
    if sell_res.get("ok"):
        sold = sell_res.get("result", {}).get("sold", 0)
        if sold > 0:
            print(f"  🧺 Converted {sold} nest eggs to spendable egg balance.")

    # 2. Query withdrawal info & limits
    info_res = miner.rpc("/api/withdraw/info", body=None)
    if not info_res.get("ok"):
        print(f"  ❌ Failed to fetch withdraw info: {info_res}")
        return {"status": "error", "response": info_res}

    res_data = info_res.get("result", {})
    lim_used = res_data.get("usedToday", 0)
    next_at = res_data.get("nextAt", 0) or 0
    now_ms = res_data.get("now", 0) or int(time.time() * 1000)

    user_info = res_data.get("user", {})
    egg_bal = int(user_info.get("eggBal", 0) or 0)
    hens = user_info.get("hens", 0)
    rate = user_info.get("rate", 0)

    min_usd = float(res_data.get("minUsd") or 0.1053)
    egg_usd = float(res_data.get("eggUsd") or 0.00000694444444444)
    gram_price = float(res_data.get("price") or 1.053)
    min_eggs = int(math.ceil(min_usd / egg_usd)) if egg_usd > 0 else 15164

    print(f"  🥚 Egg Balance: {egg_bal:,} spendable eggs (min required: {min_eggs:,})")
    print(f"  🐓 Flock: {hens} hens laying {rate:,} eggs/hr")

    # Cooldown evaluation
    if lim_used > 0 and next_at > 0 and now_ms < next_at:
        rem_ms = max(0, next_at - now_ms)
        rem_hours = rem_ms / (1000.0 * 3600.0)
        hours_int = int(rem_ms // (1000 * 3600))
        min_int = int((rem_ms % (1000 * 3600)) // (1000 * 60))
        sec_int = int((rem_ms % (1000 * 60)) // 1000)
        print(f"  ⏳ 24h Cooldown ACTIVE: {hours_int}h {min_int}m {sec_int}s remaining until next unlock.")
        hist = res_data.get("list", [])
        if hist:
            last = hist[0]
            print(f"  🧾 Today's Paid WD: {last.get('amount')} GRAM (${last.get('usd')} USD) -> Tx: {str(last.get('txHash', ''))[:20]}...")
        return {"status": "cooldown", "remaining_hours": rem_hours}

    # Balance check
    if egg_bal < min_eggs:
        print(f"  ⚠️ Balance below threshold: {egg_bal} < {min_eggs} eggs. Accumulating...")
        return {"status": "accumulating", "egg_bal": egg_bal, "min_eggs": min_eggs}

    # 3. Execute Autonomous Withdrawal
    req_id = f"w{int(time.time())}{random.randint(1000, 9999)}"
    payload = {
        "method": "gram",
        "address": TARGET_WALLET,
        "memo": memo,
        "eggs": egg_bal,
        "reqId": req_id
    }
    print(f"  🚀 EXECUTING 24H WITHDRAWAL: {egg_bal:,} eggs to {TARGET_WALLET} (memo: '{memo}')...")
    wd_res = miner.rpc("/api/withdraw", payload)
    if wd_res.get("ok"):
        w_out = wd_res.get("result", {})
        amt = w_out.get("amount", 0)
        calc_gram = (egg_bal * egg_usd) / gram_price if gram_price > 0 else 1.0
        final_amt = float(amt or calc_gram)
        print(f"  🎉 WITHDRAWAL SUCCESSFUL! Amount: {final_amt:.6f} GRAM to {TARGET_WALLET}")
        alert_msg = (
            f"🐔 <b>Chicken Land 24h Autonomous Withdrawal Executed!</b>\n"
            f"• <b>Account:</b> <code>{name} ({uid})</code>\n"
            f"• <b>Amount Paid:</b> <code>{final_amt:.6f} GRAM</code>\n"
            f"• <b>Target TON Vault:</b> <code>{TARGET_WALLET}</code>\n"
            f"• <b>Memo:</b> <code>{memo or 'None'}</code>\n"
            f"• <b>Status:</b> 🟢 On-Chain Payout Dispatched"
        )
        send_telegram_alert(alert_msg)
        return {"status": "success", "amount": final_amt, "address": TARGET_WALLET}
    else:
        desc = wd_res.get("description", "")
        code = wd_res.get("parameters", {}).get("code", "")
        print(f"  ❌ Withdrawal rejected: {desc} ({code})")
        return {"status": "rejected", "description": desc, "code": code}

def main():
    print("=" * 75)
    print("🐔 CHICKEN LAND 24-HOUR AUTONOMOUS WITHDRAWAL AUDIT & SNIPER")
    print(f"🎯 Target Wallet: {TARGET_WALLET}")
    print("=" * 75)
    tokens_db = load_tokens()

    results = {}
    for uid, info in ACCOUNTS_MAP.items():
        results[uid] = sweep_account(uid, info, tokens_db)

    print("\n" + "=" * 75)
    print("📋 SUMMARY REPORT:")
    for uid, r in results.items():
        name = ACCOUNTS_MAP[uid]["name"]
        st = r.get("status")
        if st == "success":
            print(f"  ✅ {name}: PAID {r.get('amount')} GRAM -> {TARGET_WALLET}")
        elif st == "cooldown":
            print(f"  ⏳ {name}: Cooldown active ({r.get('remaining_hours', 0):.1f}h remaining)")
        elif st == "accumulating":
            print(f"  🥚 {name}: Accumulating ({r.get('egg_bal')}/{r.get('min_eggs')} eggs)")
        else:
            print(f"  ⚠️ {name}: {st}")
    print("=" * 75)

if __name__ == "__main__":
    main()
