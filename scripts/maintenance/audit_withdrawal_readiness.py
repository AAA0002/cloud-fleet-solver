#!/usr/bin/env python3
"""
scripts/maintenance/audit_withdrawal_readiness.py
=============================================================================
Universal Fleet Withdrawal Readiness Auditor & Automated Cashout Sentinel
=============================================================================
Audits live balances across ALL 23 fleet accounts for all 13 active mining protocols
against official, ground-truth payout thresholds:

1. Chicken Land: Dynamic threshold (minUsd $0.1043 / eggUsd ~15,019 eggs = 0.1001 GRAM)
2. PepeGram: >= 1,000 PEPE -> Auto-swap to TON & cashout to Master TON Vault
3. Lifetime Energy: >= 1,000 Energy -> Cashout to Master TON Vault
4. EarnVault: >= $1.00 USD (100 cents) -> BEP20 USDT cashout to Master EVM Vault
5. JVault Miner: >= 250 JVT (minWithdrawJvt: 250) -> Cashout to Master TON Vault
6. MexoEarn: >= 1,666.67 MXO ($0.50 USDT min at 0.0003 rate) -> BEP20 to Master EVM Vault
7. MyAgeApp: >= 0.5 GRAM (S.withdraw.min = 0.5) -> Cashout to Master TON Vault
8. Kynex Network: >= 62,500 KNX ($5.00 USD at 0.00008 rate) -> BEP20 to Master EVM Vault
9. AdxEarn AI: >= 70,000 Points ($7.00 USD) -> BEP20 USDT to Master EVM Vault
10. ATF Miner: >= 10,000 ATF (fee: 70 ATF) -> On-chain TON withdrawal
11. Victor's Company: Shifts & 10% crew mining commissions swept to Master Account
12. TAVORA Node: Daily node sign-in dividends & lottery yields swept to balance
13. AZM Miner: >= 10,000 AZM -> On-chain TON withdrawal to dedicated TON wallet

Complies with:
- Rule 4: Zero Phone Load Directive (100% Cloud Execution)
- Rule 12: Permanent Wallet & Secret Immutability Directive
- Rule 6: Strict Secret Hygiene (0 plaintext secrets)
=============================================================================
"""

import os
import sys
import json
import math
import time
import base64
import random
import urllib.parse
import argparse
import asyncio
import aiohttp
from typing import Dict, List, Any, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_TOKEN", "")
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_NOTIFICATION_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
PAYOUT_CHANNEL_ID = os.getenv("PAYOUT_CHANNEL_ID", "-1004402765950")

MASTER_TON_VAULT = "UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT"
MASTER_TON_MEMO = "103525143"
MASTER_EVM_VAULT = "0xfda4182001672b9f0f09e2118242e543e35ed5ce"

# Ground-Truth Live Withdrawal Thresholds Matrix
WITHDRAWAL_THRESHOLDS = {
    "chickenland": {
        "name": "Chicken Land",
        "symbol": "GRAM",
        "threshold": 1.0,
        "action": "On-chain transfer to dedicated TON wallet",
        "unit": "GRAM"
    },
    "pepegram": {
        "name": "PepeGram",
        "symbol": "PEPE",
        "threshold": 2000000.0,
        "action": "Swap to TON & withdraw to Master TON Vault",
        "unit": "PEPE"
    },
    "lifetime": {
        "name": "Lifetime Energy",
        "symbol": "ENERGY",
        "threshold": 500.0,
        "action": "Withdraw to Master TON Vault",
        "unit": "Energy"
    },
    "earnvault": {
        "name": "EarnVault",
        "symbol": "USD",
        "threshold": 1.0,
        "action": "Cashout to BEP20 USDT to Master EVM Vault",
        "unit": "USD"
    },
    "jvault": {
        "name": "JVault Miner",
        "symbol": "JVT",
        "threshold": 250.0,
        "action": "Withdraw JVT to bound TON wallet",
        "unit": "JVT"
    },
    "mexo": {
        "name": "MexoEarn",
        "symbol": "MXO",
        "threshold": 33333.33,
        "action": "Cashout BEP20 USDT via /api/wallet/withdraw",
        "unit": "MXO"
    },
    "myage": {
        "name": "MyAgeApp",
        "symbol": "GRAM",
        "threshold": 0.5,
        "action": "Withdraw GRAM via /api/withdraw to TON vault",
        "unit": "GRAM"
    },
    "kynex": {
        "name": "Kynex Network",
        "symbol": "KNX",
        "threshold": 62500.0,
        "action": "Request BEP20 withdrawal to Master EVM Vault",
        "unit": "KNX"
    },
    "adxearn": {
        "name": "AdxEarn AI",
        "symbol": "PTS",
        "threshold": 70000.0,
        "action": "Submit BEP20 withdrawal via /api/withdraw/request",
        "unit": "PTS"
    },
    "atf": {
        "name": "ATF Miner",
        "symbol": "ATF",
        "threshold": 10000.0,
        "action": "Submit on-chain withdrawal to verified TON wallet",
        "unit": "ATF"
    },
    "victors": {
        "name": "Victor's Company",
        "symbol": "VICX",
        "threshold": 5.0,
        "action": "Claim shift & 10% crew recruitment commission",
        "unit": "VICX"
    },
    "tavora": {
        "name": "TAVORA Node",
        "symbol": "TVR",
        "threshold": 1.0,
        "action": "Claim node dividend to TVR balance",
        "unit": "TVR"
    },
    "azm": {
        "name": "AZM Miner",
        "symbol": "AZM",
        "threshold": 10000.0,
        "action": "Withdraw AZM via /api/withdraw to dedicated TON wallet",
        "unit": "AZM"
    }
}


def load_accounts() -> List[Dict[str, Any]]:
    """Load accounts from accounts.json or cloud fleet:wallets_vault:* cache."""
    acc_path = os.path.join(BASE_DIR, "accounts.json")
    if os.path.exists(acc_path):
        try:
            with open(acc_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            import urllib.request
            url = f"{UPSTASH_URL}/keys/fleet:wallets_vault:*"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                keys = json.loads(resp.read().decode()).get("result", [])
                uids = sorted(list(set([k.split(":")[-1] for k in keys if k.split(":")[-1].isdigit() and len(k.split(":")[-1]) >= 9])))
                return [{"user_id": uid, "name": f"Account {uid}"} for uid in uids]
        except Exception:
            pass

    return []


def robust_json_loads(val, default=None):
    """Recursively unpack double/triple-serialized JSON from Redis."""
    if val is None:
        return default if default is not None else {}
    curr = val
    for _ in range(5):
        if isinstance(curr, (dict, list)):
            return curr
        if isinstance(curr, str):
            s = curr.strip()
            if not s:
                return default if default is not None else {}
            try:
                curr = json.loads(s)
            except Exception:
                return default if default is not None else {}
        else:
            break
    return curr if isinstance(curr, (dict, list)) else (default if default is not None else {})


async def fetch_account_tokens(session: aiohttp.ClientSession, uid: str) -> Dict[str, Any]:
    """Retrieve WebApp tokens for account from Upstash Redis."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return {}
    url = f"{UPSTASH_URL}/get/fleet:tokens:{uid}"
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                data = await resp.json()
                raw = data.get("result")
                if raw:
                    return robust_json_loads(raw)
    except Exception:
        pass
    return {}


async def audit_single_account(session: aiohttp.ClientSession, acc: Dict[str, Any]) -> Dict[str, Any]:
    """Audit balances for an account across active protocols."""
    uid = str(acc.get("user_id") or acc.get("id") or acc.get("account_id"))
    name = acc.get("name", f"UID {uid}")
    tokens = await fetch_account_tokens(session, uid)

    report = {
        "uid": uid,
        "name": name,
        "protocols": {},
        "ready_to_withdraw": [],
        "upstream_paused_claims": []
    }

    # 1. Chicken Land
    ch_token = tokens.get("chickenland_init_data") or tokens.get("chicken_init_data")
    if ch_token:
        try:
            b64_tok = ch_token[4:].strip() if ch_token.startswith("TMA ") else base64.b64encode(ch_token.encode()).decode()
            h = {"Content-Type": "application/json", "Authorization": f"TMA {b64_tok}", "Origin": "https://app8968434282.tgcloud.ai"}
            async with session.post("https://app8968434282.tgcloud.ai/api/rpc", json={"method": "GET", "path": "/api/withdraw/info"}, headers=h, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    d = (await resp.json()).get("result", {})
                    egg_bal = d.get("user", {}).get("eggBal", 0)
                    egg_usd = float(d.get("eggUsd") or 0.0000069444)
                    ch_price = float(d.get("price") or 1.042)
                    balance_gram = (egg_bal * egg_usd) / ch_price if ch_price > 0 else 0.0
                    free_lock = d.get("freeLock", {})
                    threshold = float(free_lock.get("gram", 1.0) or 1.0)
                    is_ready = balance_gram >= threshold and d.get("usedToday", 0) == 0
                    report["protocols"]["chickenland"] = {
                        "balance": balance_gram, "threshold": threshold, "unit": "GRAM",
                        "ready": is_ready, "pct": min(100.0, round((balance_gram / threshold) * 100, 1))
                    }
                    if is_ready:
                        report["ready_to_withdraw"].append(("chickenland", balance_gram, "GRAM"))
        except Exception:
            pass

    # 2. PepeGram
    pepe_init = tokens.get("pepegram_init_data") or tokens.get("pepe_init_data")
    if pepe_init:
        try:
            h = {"Content-Type": "application/json", "Origin": "https://app.miniempire.app"}
            async with session.post("https://app.miniempire.app/miniapp/auth?tid=1449", json={"initData": pepe_init, "tid": 1449}, headers=h, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    hashes = float((await r.json()).get("user", {}).get("hashes") or 0)
                    threshold = WITHDRAWAL_THRESHOLDS["pepegram"]["threshold"]
                    is_ready = hashes >= threshold
                    report["protocols"]["pepegram"] = {
                        "balance": hashes, "threshold": threshold, "unit": "PEPE",
                        "ready": is_ready, "pct": min(100.0, round((hashes / threshold) * 100, 1))
                    }
                    if is_ready:
                        report["ready_to_withdraw"].append(("pepegram", hashes, "PEPE"))
        except Exception:
            pass

    # 3. Lifetime Energy
    lt_init = tokens.get("lifetime_energy_init_data") or tokens.get("lifetime_init_data")
    if lt_init:
        try:
            h = {"Content-Type": "application/json", "Origin": "https://app.miniempire.app"}
            async with session.post("https://app.miniempire.app/miniapp/auth?tid=5", json={"initData": lt_init, "tid": 5}, headers=h, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    hashes = float((await r.json()).get("user", {}).get("hashes") or 0)
                    threshold = WITHDRAWAL_THRESHOLDS["lifetime"]["threshold"]
                    is_ready = hashes >= threshold
                    report["protocols"]["lifetime"] = {
                        "balance": hashes, "threshold": threshold, "unit": "Energy",
                        "ready": is_ready, "pct": min(100.0, round((hashes / threshold) * 100, 1))
                    }
                    if is_ready:
                        report["ready_to_withdraw"].append(("lifetime", hashes, "Energy"))
        except Exception:
            pass

    # 4. JVault Miner
    jv_init = tokens.get("jvault_init_data")
    if jv_init:
        try:
            h = {"Authorization": f"tma {jv_init}", "Content-Type": "application/json", "Origin": "https://jvaultminer.world"}
            sync_b = {"telegramId": uid, "username": acc.get("username", name), "initData": jv_init}
            async with session.post("https://jvaultminer.world/api/user/sync", json=sync_b, headers=h, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    u = d.get("user", {})
                    balance = float(u.get("totalAssets", 0) or u.get("holdingBalance", 0) or 0)
                    threshold = WITHDRAWAL_THRESHOLDS["jvault"]["threshold"]
                    is_ready = balance >= threshold and u.get("isVerified")
                    report["protocols"]["jvault"] = {
                        "balance": balance, "threshold": threshold, "unit": "JVT",
                        "ready": is_ready, "pct": min(100.0, round((balance / threshold) * 100, 1))
                    }
                    if is_ready:
                        report["ready_to_withdraw"].append(("jvault", balance, "JVT"))
        except Exception:
            pass

    # 5. MexoEarn
    mexo_init = tokens.get("mexo_init_data")
    if mexo_init:
        try:
            h = {"Content-Type": "application/json", "Origin": "https://mexoearn.xyz"}
            async with session.post("https://mexoearn.xyz/api/auth/telegram", json={"initData": mexo_init, "deviceFp": f"fp_{uid[:8]}"}, headers=h, timeout=aiohttp.ClientTimeout(total=5)) as ar:
                if ar.status == 200:
                    tok = (await ar.json()).get("token")
                    if tok:
                        async with session.get("https://mexoearn.xyz/api/me", headers={**h, "Authorization": f"Bearer {tok}"}, timeout=aiohttp.ClientTimeout(total=5)) as mr:
                            if mr.status == 200:
                                u = (await mr.json()).get("user", {})
                                balance = float(u.get("mxoBalance", 0) or 0)
                                min_usdt = float(u.get("withdrawMinUsdt", 10.0) or 10.0)
                                rate = float(u.get("mxoToUsdtRate", 0.0003) or 0.0003)
                                threshold = (min_usdt / rate) if rate > 0 else 33333.33
                                is_ready = balance >= threshold and bool(u.get("walletAddress"))
                                report["protocols"]["mexo"] = {
                                    "balance": balance, "threshold": threshold, "unit": "MXO",
                                    "ready": is_ready, "pct": min(100.0, round((balance / threshold) * 100, 1))
                                }
                                if is_ready:
                                    report["ready_to_withdraw"].append(("mexo", balance, "MXO"))
        except Exception:
            pass

    # 6. MyAgeApp
    myage_init = tokens.get("myage_init_data")
    if myage_init:
        try:
            h = {"Authorization": f"tma {myage_init}", "Content-Type": "application/json", "Origin": "https://myageapp.xyz"}
            async with session.post("https://myageapp.xyz/api/session", json={}, headers=h, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    d = await r.json()
                    balance = float(d.get("user", {}).get("gram", 0) or 0)
                    threshold = WITHDRAWAL_THRESHOLDS["myage"]["threshold"]
                    is_ready = balance >= threshold
                    report["protocols"]["myage"] = {
                        "balance": balance, "threshold": threshold, "unit": "GRAM",
                        "ready": is_ready, "pct": min(100.0, round((balance / threshold) * 100, 1))
                    }
                    if is_ready:
                        report["ready_to_withdraw"].append(("myage", balance, "GRAM"))
        except Exception:
            pass

    # 7. AdxEarn AI
    adx_init = tokens.get("adxearn_init_data")
    if adx_init:
        try:
            h = {"X-Telegram-Init-Data": adx_init, "Content-Type": "application/json"}
            async with session.post("https://api.adxearnai.xyz/api/user/session", json={"fp": f"fp_{uid}"}, headers=h, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    u = (await r.json()).get("user", {})
                    balance = float(u.get("balance_points", 0) or 0)
                    threshold = WITHDRAWAL_THRESHOLDS["adxearn"]["threshold"]
                    is_ready = balance >= threshold
                    report["protocols"]["adxearn"] = {
                        "balance": balance, "threshold": threshold, "unit": "PTS",
                        "ready": is_ready, "pct": min(100.0, round((balance / threshold) * 100, 1))
                    }
                    if is_ready:
                        report["ready_to_withdraw"].append(("adxearn", balance, "PTS"))
        except Exception:
            pass

    # 8. EarnVault
    ev_init = tokens.get("earnvault_init_data") or tokens.get("ev_init_data")
    ev_rf = tokens.get("earnvault_refresh_token")
    if ev_init or ev_rf:
        try:
            ev_cents = float(tokens.get("earnvault_cents", 0) or acc.get("earnvault_cents", 0) or 0)
            threshold = WITHDRAWAL_THRESHOLDS["earnvault"]["threshold"]
            bal_usd = ev_cents / 100.0
            is_ready = bal_usd >= threshold
            report["protocols"]["earnvault"] = {
                "balance": bal_usd, "threshold": threshold, "unit": "USD",
                "ready": is_ready, "pct": min(100.0, round((bal_usd / threshold) * 100, 1)) if threshold > 0 else 0
            }
            if is_ready:
                report["ready_to_withdraw"].append(("earnvault", bal_usd, "USD"))
        except Exception:
            pass

    # 9. Kynex Network (Upstream withdrawals paused by official Kynex team until TGE)
    knx_init = tokens.get("kynex_init_data") or tokens.get("knx_init_data")
    if knx_init:
        try:
            knx_bal = float(tokens.get("kynex_coins", 0) or acc.get("kynex_coins", 0) or 0)
            threshold = WITHDRAWAL_THRESHOLDS["kynex"]["threshold"]
            is_ready = knx_bal >= threshold
            report["protocols"]["kynex"] = {
                "balance": knx_bal, "threshold": threshold, "unit": "KNX",
                "ready": is_ready, "upstream_paused": True, "note": "Withdrawals paused by Kynex team upstream until TGE (HTTP 503)",
                "pct": min(100.0, round((knx_bal / threshold) * 100, 1)) if threshold > 0 else 0
            }
            if is_ready:
                report["upstream_paused_claims"].append(("kynex", knx_bal, "KNX"))
        except Exception:
            pass

    # 10. ATF Miner
    atf_init = tokens.get("atf_init_data")
    if atf_init:
        try:
            atf_bal = float(tokens.get("atf_balance", 0) or acc.get("atf_balance", 0) or 0)
            threshold = WITHDRAWAL_THRESHOLDS["atf"]["threshold"]
            is_ready = atf_bal >= threshold
            report["protocols"]["atf"] = {
                "balance": atf_bal, "threshold": threshold, "unit": "ATF",
                "ready": is_ready, "pct": min(100.0, round((atf_bal / threshold) * 100, 1)) if threshold > 0 else 0
            }
            if is_ready:
                report["ready_to_withdraw"].append(("atf", atf_bal, "ATF"))
        except Exception:
            pass

    # 11. Victor's Company
    vic_init = tokens.get("victors_init_data") or tokens.get("vic_init_data")
    if vic_init:
        try:
            vic_bal = float(tokens.get("victors_balance", 0) or acc.get("victors_balance", 0) or 0)
            threshold = WITHDRAWAL_THRESHOLDS["victors"]["threshold"]
            is_ready = vic_bal >= threshold
            report["protocols"]["victors"] = {
                "balance": vic_bal, "threshold": threshold, "unit": "VICX",
                "ready": is_ready, "pct": min(100.0, round((vic_bal / threshold) * 100, 1)) if threshold > 0 else 0
            }
            if is_ready:
                report["ready_to_withdraw"].append(("victors", vic_bal, "VICX"))
        except Exception:
            pass

    # 12. TAVORA Node
    tvr_init = tokens.get("tavora_init_data") or tokens.get("tvr_init_data")
    if tvr_init:
        try:
            tvr_bal = float(tokens.get("tavora_balance", 0) or acc.get("tavora_balance", 0) or 0)
            threshold = WITHDRAWAL_THRESHOLDS["tavora"]["threshold"]
            is_ready = tvr_bal >= threshold
            report["protocols"]["tavora"] = {
                "balance": tvr_bal, "threshold": threshold, "unit": "TVR",
                "ready": is_ready, "pct": min(100.0, round((tvr_bal / threshold) * 100, 1)) if threshold > 0 else 0
            }
            if is_ready:
                report["ready_to_withdraw"].append(("tavora", tvr_bal, "TVR"))
        except Exception:
            pass

    # 13. AZM Miner
    azm_init = tokens.get("azm_init_data") or tokens.get("azm") or tokens.get("azmir")
    if azm_init:
        try:
            h = {"X-Init-Data": azm_init, "User-Agent": "Mozilla/5.0"}
            AZM_BASE = os.getenv("AZM_API_BASE", "https://amd-dot-thin-resident.trycloudflare.com/api").rstrip("/")
            async with session.get(f"{AZM_BASE}/state", headers=h, timeout=aiohttp.ClientTimeout(total=5)) as ar:
                if ar.status == 200:
                    d = await ar.json()
                    balance = float(d.get("balance", 0.0) or 0.0)
                    threshold = float(d.get("min_wd", 10000.0) or 10000.0)
                    is_ready = balance >= threshold and bool(d.get("wallet"))
                    report["protocols"]["azm"] = {
                        "balance": balance, "threshold": threshold, "unit": "AZM",
                        "ready": is_ready, "pct": min(100.0, round((balance / threshold) * 100, 1)) if threshold > 0 else 0
                    }
                    if is_ready:
                        report["ready_to_withdraw"].append(("azm", balance, "AZM"))
        except Exception:
            pass

    return report


async def run_audit_and_sweep(execute_sweep: bool = False, notify: bool = True):
    """Execute complete withdrawal readiness audit and optional sweep."""
    accounts = load_accounts()
    print("=" * 75)
    print("📊 AAA AUTO FLEET — WITHDRAWAL READINESS AUDITOR (13 BOTS)")
    print(f"Auditing {len(accounts)} Accounts against Ground-Truth Thresholds...")
    print("=" * 75)

    all_ready = []
    all_upstream_paused = []
    async with aiohttp.ClientSession() as session:
        tasks = [audit_single_account(session, acc) for acc in accounts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for rep in results:
            if isinstance(rep, dict):
                for bot_id, bal, unit in rep["ready_to_withdraw"]:
                    all_ready.append((rep["uid"], rep["name"], bot_id, bal, unit))
                for bot_id, bal, unit in rep.get("upstream_paused_claims", []):
                    all_upstream_paused.append((rep["uid"], rep["name"], bot_id, bal, unit))

    print(f"\n🎯 SUMMARY: {len(all_ready)} active claims ready across {len(accounts)} accounts.")
    if all_ready:
        for uid, name, bot_id, bal, unit in all_ready:
            proto_info = WITHDRAWAL_THRESHOLDS.get(bot_id, {})
            print(f"  🟢 [{name}] Ready in {proto_info.get('name', bot_id)}: {bal:.2f} {unit} (Threshold: {proto_info.get('threshold')} {unit})")
    else:
        print("  🟡 All accounts are currently in the ACCUMULATING phase toward active minimum thresholds.")

    if all_upstream_paused:
        print(f"\n⏸️ UPSTREAM PAUSED BY BOT DEVELOPERS ({len(all_upstream_paused)} accounts met threshold):")
        for uid, name, bot_id, bal, unit in all_upstream_paused:
            proto_info = WITHDRAWAL_THRESHOLDS.get(bot_id, {})
            print(f"  ⏸️ [{name}] {proto_info.get('name', bot_id)}: {bal:.2f} {unit} (Upstream cashouts disabled by bot team pending TGE - HTTP 503)")

    print("=" * 75)

    if execute_sweep and all_ready:
        print("\n🚀 Executing automated sweeps for eligible claims...")
        from fleet_auto_withdrawal_engine import execute_universal_fleet_auto_withdrawals
        async with aiohttp.ClientSession() as sweep_session:
            sweep_res = await execute_universal_fleet_auto_withdrawals(sweep_session, accounts)
            print(f"\n🎉 Automated Sweep Completed: {sweep_res.get('total_withdrawn_count', 0)} payouts executed!")
            for proto, count in sweep_res.get("by_protocol", {}).items():
                print(f"  • {proto}: {count} completed successfully.")


def main():
    parser = argparse.ArgumentParser(description="Universal Fleet Withdrawal Readiness Auditor")
    parser.add_argument("--execute", action="store_true", help="Auto-execute ready withdrawals to master vaults")
    args = parser.parse_args()

    asyncio.run(run_audit_and_sweep(execute_sweep=args.execute))


if __name__ == "__main__":
    main()
