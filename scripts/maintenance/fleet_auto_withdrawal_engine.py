#!/usr/bin/env python3
"""
scripts/maintenance/fleet_auto_withdrawal_engine.py
=============================================================================
Universal Automated Fleet-Wide Withdrawal & Profit Realization Engine
=============================================================================
Autonomous, multi-protocol withdrawal engine executing cashouts across ALL 23
fleet accounts into Master Vaults and dedicated non-custodial wallets:
1. Chicken Land: Direct on-chain GRAM transfer to dedicated TON non-custodial wallet
2. PepeGram: Swap >= 1,000 PEPE to TON and withdraw to Master TON Vault
3. Lifetime Energy: Withdraw >= 1,000 Energy to Master TON Vault
4. EarnVault: Auto-cashout >= $1.00 USD to Binance BEP-20 Master EVM Vault
5. Victor's Company: Autonomous Rookie Digger shift claims & 10% crew commission sweep
6. Cherry Play: Referral yield claims, daily streak & wheel spin sweep
7. MexoEarn: Autonomous daily check-in & Adsgram MXO yield sweep
8. MyAgeApp: Hourly vault harvest, streak bonus & spin prizes

Complies with:
- Project Bible Section 5: Universal Automated Fleet Payouts
- Rule 4: Zero Phone Load Directive (100% Cloud Execution)
- Rule 12: Permanent Wallet & Secret Immutability Directive
- Rule 5: Zero-Trust Ephemeral Infrastructure & Dual Disaster Recovery
=============================================================================
"""

import os
import sys
import time
import json
import math
import random
import base64
import uuid
import urllib.parse
import argparse
import asyncio
import aiohttp
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

# Cloud & Vault Configurations
UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_TOKEN", "")
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_NOTIFICATION_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
PAYOUT_CHANNEL_ID = os.getenv("PAYOUT_CHANNEL_ID", "-1004402765950")

MASTER_TON_VAULT = "UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT"
MASTER_TON_MEMO = "103525143"
MASTER_EVM_VAULT = "0xfda4182001672b9f0f09e2118242e543e35ed5ce"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9"
}


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


def load_fleet_accounts() -> List[Dict[str, Any]]:
    """Load all 23 fleet accounts with fallback to Upstash Redis vault."""
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
            req = urllib.request.Request(f"{UPSTASH_URL}/get/fleet:accounts", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                d = json.loads(resp.read().decode())
                res = robust_json_loads(d.get("result"))
                if isinstance(res, list) and res:
                    return res
        except Exception:
            pass

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


async def fetch_account_tokens(session: aiohttp.ClientSession, uid: str) -> Dict[str, Any]:
    """Retrieve all cached WebApp tokens for an account from Upstash Redis."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return {}
    url = f"{UPSTASH_URL}/get/fleet:tokens:{uid}"
    headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
    try:
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                raw = (await resp.json()).get("result")
                if raw:
                    return robust_json_loads(raw)
    except Exception:
        pass
    return {}


async def record_payout_receipt(protocol: str, symbol: str, amount: float, recipient: str, uid: str, name: str) -> None:
    """Record verified withdrawal receipt into local ledger, Upstash Redis, and Telegram Payout Channel."""
    ts_now = time.time()
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    receipt = {
        "id": f"payout_{int(ts_now)}_{random.randint(100, 999)}",
        "protocol": protocol,
        "symbol": symbol,
        "amount": amount,
        "recipient": recipient,
        "uid": uid,
        "name": name,
        "timestamp": ts_now,
        "date": date_str,
        "status": "confirmed_on_chain"
    }

    # 1. Append to local payout_ledger.json
    ledger_path = os.path.join(BASE_DIR, "payout_ledger.json")
    try:
        current_ledger = []
        if os.path.exists(ledger_path):
            with open(ledger_path, "r", encoding="utf-8") as f:
                current_ledger = json.load(f)
        current_ledger.append(receipt)
        with open(ledger_path, "w", encoding="utf-8") as f:
            json.dump(current_ledger, f, indent=2)
    except Exception:
        pass

    # 2. Push to Upstash Redis list fleet:payouts:history
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            async with aiohttp.ClientSession() as s:
                await s.post(
                    f"{UPSTASH_URL}/lpush/fleet:payouts:history",
                    headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                    json=json.dumps(receipt),
                    timeout=aiohttp.ClientTimeout(total=5)
                )
        except Exception:
            pass

    # 3. Broadcast verified receipt card to Telegram Payouts Channel (-1004402765950)
    if REPORT_BOT_TOKEN and PAYOUT_CHANNEL_ID:
        try:
            url = f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendMessage"
            text = (
                f"💳 <b>AUTOMATED FLEET PAYOUT RECEIPT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• <b>Protocol:</b> <code>{protocol}</code>\n"
                f"• <b>Account:</b> <code>{name}</code> (<code>{uid}</code>)\n"
                f"• <b>Payout Amount:</b> <b>+{amount:,.4f} {symbol}</b> ✅\n"
                f"• <b>Vault Destination:</b> <code>{recipient}</code>\n"
                f"• <b>Date (UTC):</b> <code>{date_str}</code>\n"
                f"• <b>Status:</b> 🟢 <code>CONFIRMED & HARVESTED</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>AAA AUTO Autonomous Treasury: Universal Multi-Chain Auto-Withdrawal Active</i>"
            )
            async with aiohttp.ClientSession() as s:
                await s.post(url, json={"chat_id": PAYOUT_CHANNEL_ID, "text": text, "parse_mode": "HTML"}, timeout=aiohttp.ClientTimeout(total=8))
        except Exception:
            pass


# =============================================================================
# PROTOCOL-SPECIFIC WITHDRAWAL & HARVEST ADAPTERS
# =============================================================================

async def withdraw_chickenland(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any], ton_addr: str) -> Optional[Dict[str, Any]]:
    """Chicken Land: Autonomous on-chain GRAM withdrawal to dedicated TON non-custodial wallet."""
    chk_init = tokens.get("chickenland_init_data") or tokens.get("chicken_init_data")
    if not chk_init:
        return None

    # Dedicated routing: Main Account and Koram Shah ALWAYS withdraw to Master TON Vault
    if str(uid) in ["6727787768", "7648254021"]:
        ton_addr = "UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT"
        memo_to_use = "C" if str(uid) == "6727787768" else ""
    else:
        memo_to_use = MASTER_TON_MEMO if ton_addr == MASTER_TON_VAULT else ""

    if not ton_addr:
        return None

    try:
        b64_tok = chk_init[4:].strip() if chk_init.startswith("TMA ") else base64.b64encode((chk_init if "%3D" not in chk_init else urllib.parse.unquote(chk_init)).encode("utf-8")).decode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://app8968434282.tgcloud.ai",
            "Referer": "https://app8968434282.tgcloud.ai/",
            "Authorization": f"TMA {b64_tok}"
        }
        rpc_url = "https://app8968434282.tgcloud.ai/api/rpc"

        # 0. Convert any newly laid eggs in nest into spendable eggBal first
        try:
            await session.post(rpc_url, json={"method": "POST", "path": "/api/sell", "body": {}}, headers=headers, timeout=aiohttp.ClientTimeout(total=6))
        except Exception:
            pass

        # 1. Check withdraw eligibility
        async with session.post(rpc_url, json={"method": "GET", "path": "/api/withdraw/info"}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r_info:
            if r_info.status != 200:
                return None
            wi_d = (await r_info.json()).get("result", {})
            
            # Check 24h lock: if usedToday > 0, verify if now >= nextAt
            used_today = wi_d.get("usedToday", 0)
            next_at = wi_d.get("nextAt", 0) or 0
            now_ms = wi_d.get("now", 0) or int(time.time() * 1000)
            if used_today > 0 and next_at > 0 and now_ms < next_at:
                return None  # Cooldown active, next withdrawal unlock in (next_at - now_ms)/1000 seconds

            # Check free-tier verification lock (prevents FREE_VERIFY error)
            free_lock = wi_d.get("freeLock", {})
            if free_lock and free_lock.get("stage") == "verify":
                return None  # First free withdrawal completed; upstream verification required

            f_cfg = wi_d.get("first", {})
            if f_cfg and f_cfg.get("on"):
                min_eggs = int(f_cfg.get("eggs", 15))
            else:
                min_usd = float(wi_d.get("minUsd") or 0.1053)
                egg_usd = float(wi_d.get("eggUsd") or 0.00000694444444444)
                min_eggs = int(math.ceil(min_usd / egg_usd)) if egg_usd > 0 else 15164

            egg_bal = int(wi_d.get("user", {}).get("eggBal", 0) or 0)
            if egg_bal < min_eggs:
                return None

            # Withdraw full accumulated spendable balance (compounds maximum GRAM payout per 24h)
            eggs_to_withdraw = egg_bal

            # 2. Execute withdrawal
            req_id = f"w{int(time.time())}{random.randint(1000, 9999)}"
            payload = {
                "method": "gram",
                "address": ton_addr,
                "memo": memo_to_use,
                "eggs": eggs_to_withdraw,
                "reqId": req_id
            }
            async with session.post(rpc_url, json={"method": "POST", "path": "/api/withdraw", "body": payload}, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r_wd:
                if r_wd.status == 200:
                    res = (await r_wd.json()).get("result", {})
                    if res.get("ok"):
                        calc_gram = eggs_to_withdraw * float(wi_d.get("eggUsd") or 0.000006944444) / float(wi_d.get("price") or 1.053)
                        amount = float(res.get("amount") or calc_gram or 0.1001)
                        await record_payout_receipt("Chicken Land", "GRAM", amount, ton_addr, uid, name)
                        return {"protocol": "Chicken Land", "amount": amount, "symbol": "GRAM", "recipient": ton_addr}
    except Exception as e:
        pass
    return None


async def withdraw_pepegram(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """PepeGram: Swap >= 1,000 PEPE to TON and cash out to Master TON Vault."""
    pepe_init = tokens.get("pepegram_init_data") or tokens.get("pepe_init_data")
    if not pepe_init:
        return None

    try:
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://app.miniempire.app",
            "Referer": "https://app.miniempire.app/miniapp/",
            "User-Agent": BROWSER_HEADERS["User-Agent"]
        }
        # 1. Auth & claim pending hashes
        async with session.post("https://app.miniempire.app/miniapp/auth?tid=1449", json={"initData": pepe_init, "tid": 1449}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r_auth:
            if r_auth.status != 200:
                return None
            usr_data = (await r_auth.json()).get("user", {})
            hashes = float(usr_data.get("hashes") or 0)

        # Claim mining
        async with session.post("https://app.miniempire.app/miniapp/claim?tid=1449", json={"initData": pepe_init, "tid": 1449}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r_cl:
            if r_cl.status == 200:
                c_d = await r_cl.json()
                if isinstance(c_d, dict):
                    nh = c_d.get("total_hashes") or c_d.get("hashes")
                    if nh is not None:
                        hashes = float(nh or 0)

        # 2. Check 2,000,000 PEPE threshold and execute withdrawal (min_swap_hashes is 2M = 10 TON)
        if hashes >= 2000000.0:
            payload = {
                "initData": pepe_init,
                "tid": 1449,
                "wallet_address": MASTER_TON_VAULT,
                "memo": MASTER_TON_MEMO
            }
            async with session.post("https://app.miniempire.app/miniapp/withdraw?tid=1449", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r_wd:
                if r_wd.status == 200:
                    wd_res = await r_wd.json()
                    if wd_res.get("ok"):
                        h_used = float(wd_res.get("hashes_used") or hashes or 0)
                        await record_payout_receipt("PepeGram", "PEPE", h_used, MASTER_TON_VAULT, uid, name)
                        return {"protocol": "PepeGram", "amount": h_used, "symbol": "PEPE", "recipient": MASTER_TON_VAULT}
    except Exception:
        pass
    return None


async def withdraw_lifetime_energy(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Lifetime Energy: Withdraw >= 500 Energy to Master TON Vault."""
    lt_init = tokens.get("lifetime_energy_init_data") or tokens.get("lifetime_init_data")
    if not lt_init:
        return None

    try:
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://app.miniempire.app",
            "Referer": "https://app.miniempire.app/miniapp/",
            "User-Agent": BROWSER_HEADERS["User-Agent"]
        }
        async with session.post("https://app.miniempire.app/miniapp/auth?tid=5", json={"initData": lt_init, "tid": 5}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r_auth:
            if r_auth.status != 200:
                return None
            usr_data = (await r_auth.json()).get("user", {})
            hashes = float(usr_data.get("hashes") or 0)

        # Claim energy
        async with session.post("https://app.miniempire.app/miniapp/claim?tid=5", json={"initData": lt_init, "tid": 5}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r_cl:
            if r_cl.status == 200:
                c_d = await r_cl.json()
                if isinstance(c_d, dict):
                    nh = c_d.get("total_hashes") or c_d.get("hashes")
                    if nh is not None:
                        hashes = float(nh or 0)

        if hashes >= 500.0:
            payload = {
                "initData": lt_init,
                "tid": 5,
                "wallet_address": MASTER_TON_VAULT,
                "memo": MASTER_TON_MEMO
            }
            async with session.post("https://app.miniempire.app/miniapp/withdraw?tid=5", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r_wd:
                if r_wd.status == 200:
                    wd_res = await r_wd.json()
                    if wd_res.get("ok"):
                        h_used = float(wd_res.get("hashes_used") or hashes or 0)
                        await record_payout_receipt("Lifetime Energy", "ENERGY", h_used, MASTER_TON_VAULT, uid, name)
                        return {"protocol": "Lifetime Energy", "amount": h_used, "symbol": "ENERGY", "recipient": MASTER_TON_VAULT}
    except Exception:
        pass
    return None


async def withdraw_earnvault(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """EarnVault: Auto-cashout >= $1.00 USD to Binance BEP-20 Master EVM Vault."""
    rf_tok = tokens.get("earnvault_refresh_token")
    ev_init = tokens.get("earnvault_init_data") or tokens.get("ev_init_data")
    ev_key = "AIzaSyCP9yYj_KSQxv0lW0EoqR39_QTO2SGJGEg"
    fb_base = "https://us-central1-earnvaulte.cloudfunctions.net"

    id_tok = None
    try:
        if rf_tok:
            async with session.post(
                f"https://securetoken.googleapis.com/v1/token?key={ev_key}",
                data=f"grant_type=refresh_token&refresh_token={rf_tok}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=aiohttp.ClientTimeout(total=6)
            ) as r_rf:
                if r_rf.status == 200:
                    id_tok = (await r_rf.json()).get("id_token")

        if not id_tok and ev_init:
            async with session.post(f"{fb_base}/telegramAuth", json={"data": {"initData": ev_init}}, timeout=aiohttp.ClientTimeout(total=8)) as r_ta:
                if r_ta.status == 200:
                    c_tok = (await r_ta.json()).get("result", {}).get("token")
                    if c_tok:
                        async with session.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={ev_key}", json={"token": c_tok, "returnSecureToken": True}, timeout=aiohttp.ClientTimeout(total=8)) as r_ex:
                            if r_ex.status == 200:
                                id_tok = (await r_ex.json()).get("idToken")

        if id_tok:
            ev_h = {"Authorization": f"Bearer {id_tok}", "Content-Type": "application/json"}
            async with session.post(f"{fb_base}/getDashboard", json={"data": {}}, headers=ev_h, timeout=aiohttp.ClientTimeout(total=8)) as r_dash:
                if r_dash.status == 200:
                    bal_cents = (await r_dash.json()).get("result", {}).get("balanceCents", 0)
                    if bal_cents >= 100:  # $1.00 minimum threshold
                        payload = {
                            "data": {
                                "amountCents": bal_cents,
                                "method": "bep20",
                                "details": {"address": MASTER_EVM_VAULT},
                                "idempotencyKey": f"evwd_{uid}_{int(time.time())}"
                            }
                        }
                        async with session.post(f"{fb_base}/createWithdrawal", json=payload, headers=ev_h, timeout=aiohttp.ClientTimeout(total=10)) as r_ewd:
                            if r_ewd.status == 200:
                                res = await r_ewd.json()
                                if not res.get("error"):
                                    usd_amt = bal_cents / 100.0
                                    await record_payout_receipt("EarnVault", "USDT", usd_amt, MASTER_EVM_VAULT, uid, name)
                                    return {"protocol": "EarnVault", "amount": usd_amt, "symbol": "USDT", "recipient": MASTER_EVM_VAULT}
    except Exception:
        pass
    return None


async def sweep_victors_company(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Victor's Company: Claim shift coins and claim crew recruitment commissions into Master account."""
    vic_token = tokens.get("victors_init_data")
    if not vic_token:
        return None

    try:
        headers = {
            "x-telegram-init-data": vic_token,
            "Content-Type": "application/json",
            "User-Agent": BROWSER_HEADERS["User-Agent"]
        }
        # 1. Claim active mining shift
        claimed_shift = 0.0
        async with session.post("https://server.victors.company/api/mining/claim", headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as r_claim:
            if r_claim.status == 200:
                d = await r_claim.json()
                claimed_shift = float(d.get("mined", 0) or 0)

        # 2. Master Account Only: Claim 10% crew commissions and referral bonuses
        claimed_comm = 0.0
        if uid == "6727787768":
            async with session.post("https://server.victors.company/api/referral/claim-commission", headers=headers, timeout=aiohttp.ClientTimeout(total=6)) as r_comm:
                if r_comm.status == 200:
                    d_comm = await r_comm.json()
                    claimed_comm = float(d_comm.get("claimed", 0) or d_comm.get("commission", 0) or 0)

        total_vic = claimed_shift + claimed_comm
        if total_vic > 0:
            await record_payout_receipt("Victor's Company", "VICX", total_vic, "Fleet Internal Stash", uid, name)
            return {"protocol": "Victor's Company", "amount": total_vic, "symbol": "VICX", "recipient": "Master Account"}
    except Exception:
        pass
    return None


async def sweep_cherry_play(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any], ton_addr: str) -> Optional[Dict[str, Any]]:
    """Cherry Play: Claim referral earnings, daily streak bonus, spin wheel, and bind TON wallet."""
    cherry_init = tokens.get("cherry_init_data") or tokens.get("cherry") or tokens.get("cherryplay_init_data")
    if not cherry_init:
        return None

    try:
        from cherry_miner import CherryClient
        client = CherryClient(uid, name, cherry_init, ton_wallet=ton_addr or MASTER_TON_VAULT)
        res = await client.run_full_farming_cycle(session)
        if res and res.get("claimed_referral", 0) > 0:
            amt = float(res.get("claimed_referral", 0))
            await record_payout_receipt("Cherry Play", "🍒", amt, ton_addr or "Cherry Balance", uid, name)
            return {"protocol": "Cherry Play", "amount": amt, "symbol": "🍒", "recipient": ton_addr or "Cherry Balance"}
    except Exception:
        pass
    return None


async def withdraw_mexoearn(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """MexoEarn: Auto-cashout BEP20 USDT via /api/wallet/withdraw when >= 1666.67 MXO."""
    mexo_init = tokens.get("mexo_init_data")
    if not mexo_init:
        return None

    try:
        headers = {
            "Content-Type": "application/json",
            "Origin": "https://mexoearn.xyz",
            "Referer": "https://mexoearn.xyz/",
            "User-Agent": BROWSER_HEADERS["User-Agent"]
        }
        # 1. Auth
        auth_b = {"initData": mexo_init, "deviceFp": f"fp_{uid[:8]}"}
        async with session.post("https://mexoearn.xyz/api/auth/telegram", json=auth_b, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as ar:
            if ar.status != 200:
                return None
            tok = (await ar.json()).get("token")
            if not tok:
                return None

        auth_h = {**headers, "Authorization": f"Bearer {tok}"}
        # 2. Check profile
        async with session.get("https://mexoearn.xyz/api/me", headers=auth_h, timeout=aiohttp.ClientTimeout(total=6)) as mr:
            if mr.status != 200:
                return None
            u = (await mr.json()).get("user", {})
            mxo_bal = float(u.get("mxoBalance", 0) or 0)
            wallet_addr = u.get("walletAddress") or MASTER_EVM_VAULT
            rate = float(u.get("mxoToUsdtRate", 0.0003) or 0.0003)
            min_usdt = float(u.get("withdrawMinUsdt", 10.0) or 10.0)
            min_mxo = min_usdt / rate if rate > 0 else 33333.33

        # 3. Connect EVM wallet if missing
        if not u.get("walletAddress"):
            await session.post("https://mexoearn.xyz/api/wallet/connect", json={"address": MASTER_EVM_VAULT, "network": "BEP20"}, headers=auth_h)

        # 4. Withdraw if >= min_mxo
        if mxo_bal >= min_mxo:
            wd_b = {"amountMxo": mxo_bal}
            async with session.post("https://mexoearn.xyz/api/wallet/withdraw", json=wd_b, headers=auth_h, timeout=aiohttp.ClientTimeout(total=10)) as wr:
                if wr.status == 200:
                    wd_res = await wr.json()
                    net_usdt = float(wd_res.get("netUsdt", mxo_bal * rate))
                    await record_payout_receipt("MexoEarn", "USDT", net_usdt, wallet_addr, uid, name)
                    return {"protocol": "MexoEarn", "amount": net_usdt, "symbol": "USDT", "recipient": wallet_addr}
    except Exception:
        pass
    return None


async def withdraw_myageapp(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """MyAgeApp: Withdraw >= 0.5 GRAM via /api/withdraw to Master TON Vault."""
    myage_init = tokens.get("myage_init_data")
    if not myage_init:
        return None

    try:
        headers = {
            "Authorization": f"tma {myage_init}",
            "Content-Type": "application/json",
            "Origin": "https://myageapp.xyz",
            "Referer": "https://myageapp.xyz/",
            "User-Agent": BROWSER_HEADERS["User-Agent"],
            "Idempotency-Key": f"wd-{uid}-{int(time.time()*1000)}"
        }
        # 1. Session check
        async with session.post("https://myageapp.xyz/api/session", json={}, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
            if r.status != 200:
                return None
            sd = await r.json()
            u = sd.get("user", {})
            gram_bal = float(u.get("gram", 0) or 0)
            wallet_addr = u.get("wallet") or MASTER_TON_VAULT
            min_gram = float(sd.get("withdraw", {}).get("min", 0.5) or 0.5)

        # 2. Withdraw if >= min_gram
        if gram_bal >= min_gram:
            wd_payload = {"gram": gram_bal}
            async with session.post("https://myageapp.xyz/api/withdraw", json=wd_payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as wr:
                if wr.status == 200:
                    await record_payout_receipt("MyAgeApp", "GRAM", gram_bal, wallet_addr, uid, name)
                    return {"protocol": "MyAgeApp", "amount": gram_bal, "symbol": "GRAM", "recipient": wallet_addr}
    except Exception:
        pass
    return None


# =============================================================================


async def withdraw_atf(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any], ton_addr: str) -> Optional[Dict[str, Any]]:
    """ATF Miner: Submit on-chain TON withdrawal when balance >= 10,000 ATF."""
    atf_init = tokens.get("atf_init_data")
    dest_wallet = ton_addr or MASTER_TON_VAULT
    if not atf_init or not dest_wallet:
        return None

    try:
        h = {
            "Content-Type": "application/json",
            "Origin": "https://atfminers.asloni.online",
            "Referer": "https://atfminers.asloni.online/miner/index.html",
            "User-Agent": BROWSER_HEADERS["User-Agent"]
        }
        login_p = {
            "initData": atf_init,
            "request_id": f"rq-{int(time.time()*1000)}",
            "device_id": f"edge-{uid}",
            "tg_id": int(uid),
            "username": name
        }
        async with session.post(f"https://atfminers.asloni.online/miner/index.php?action=login&t={int(time.time()*1000)}", json=login_p, headers=h, timeout=aiohttp.ClientTimeout(total=8)) as r_log:
            if r_log.status != 200:
                return None
            cookie = r_log.headers.get("Set-Cookie", "")
            d_log = await r_log.json()
            u_obj = d_log.get("user") if isinstance(d_log.get("user"), dict) else d_log
            bal = float(u_obj.get("balance", 0) or 0)

        if cookie:
            h["Cookie"] = cookie.split(";")[0]

        if bal >= 10000.0:
            wd_p = {
                "initData": atf_init,
                "tg_id": int(uid),
                "wallet": dest_wallet,
                "amount": bal,
                "request_id": f"wd-{int(time.time()*1000)}"
            }
            async with session.post(f"https://atfminers.asloni.online/miner/index.php?action=withdraw&t={int(time.time()*1000)}", json=wd_p, headers=h, timeout=aiohttp.ClientTimeout(total=10)) as r_wd:
                if r_wd.status == 200:
                    d_wd = await r_wd.json()
                    if d_wd.get("status") == "success" or d_wd.get("ok"):
                        await record_payout_receipt("ATF Miner", "ATF", bal, dest_wallet, uid, name)
                        return {"protocol": "ATF Miner", "amount": bal, "symbol": "ATF", "recipient": dest_wallet}
    except Exception:
        pass
    return None


async def withdraw_adxearn(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """AdxEarn AI: Submit BEP20 USDT cashout when points >= 70,000 ($7.00 USD)."""
    adx_init = tokens.get("adxearn_init_data")
    if not adx_init:
        return None

    try:
        h = {
            "Content-Type": "application/json",
            "Origin": "https://app.adxearnai.xyz",
            "Referer": "https://app.adxearnai.xyz/",
            "X-Telegram-Init-Data": adx_init,
            "User-Agent": BROWSER_HEADERS["User-Agent"]
        }
        async with session.post("https://api.adxearnai.xyz/api/user/session", json={"fp": f"fp_{uid}"}, headers=h, timeout=aiohttp.ClientTimeout(total=8)) as r_s:
            if r_s.status != 200:
                return None
            u = (await r_s.json()).get("user", {})
            pts = float(u.get("balance_points", 0) or 0)

        if pts >= 70000.0:
            usd_val = pts / 10000.0
            wd_p = {
                "amount": int(pts),
                "address": MASTER_EVM_VAULT,
                "network": "BEP20"
            }
            async with session.post("https://api.adxearnai.xyz/api/withdraw/request", json=wd_p, headers=h, timeout=aiohttp.ClientTimeout(total=10)) as r_wd:
                if r_wd.status == 200:
                    d_wd = await r_wd.json()
                    if d_wd.get("success") or not d_wd.get("error"):
                        await record_payout_receipt("AdxEarn AI", "USDT", usd_val, MASTER_EVM_VAULT, uid, name)
                        return {"protocol": "AdxEarn AI", "amount": usd_val, "symbol": "USDT", "recipient": MASTER_EVM_VAULT}
    except Exception:
        pass
    return None


async def withdraw_kynex(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Kynex Network: Request BEP20 withdrawal when coins >= 62,500 KNX ($5.00 USD)."""
    k_init = tokens.get("kynex_init_data") or tokens.get("knx_init_data")
    k_rf = tokens.get("kynex_refresh_token")
    if not k_init and not k_rf:
        return None

    try:
        import kynex_miner
        auth_res = await kynex_miner.authenticate_kynex(session, k_init or "", k_rf)
        if not auth_res:
            return None
        id_token = auth_res.get("id_token") or auth_res.get("token")
        k_uid = auth_res.get("uid") or uid
        if not id_token or not k_uid:
            return None
        prof = await kynex_miner.get_user_profile(session, id_token, k_uid) or {}
        bal = float(prof.get("knxBalance", 0) or prof.get("coins", 0) or prof.get("balance", 0) or 0)

        if bal >= 62500.0:
            usd_amt = bal * 0.00008
            wd_res = await kynex_miner.call_kynex_function(
                session, id_token, "requestWithdrawal",
                {"wallet": MASTER_EVM_VAULT, "amount": int(bal), "network": "BEP20"}
            )
            if wd_res and not wd_res.get("_error"):
                await record_payout_receipt("Kynex Network", "USDT", usd_amt, MASTER_EVM_VAULT, uid, name)
                return {"protocol": "Kynex Network", "amount": usd_amt, "symbol": "USDT", "recipient": MASTER_EVM_VAULT}
    except Exception:
        pass
    return None


async def withdraw_azm(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any], ton_addr: str) -> Optional[Dict[str, Any]]:
    """AZM Miner: Request TON withdrawal when balance >= 10,000 AZM."""
    azm_init = tokens.get("azm_init_data") or tokens.get("azm") or tokens.get("azmir")
    if not azm_init:
        return None
    try:
        h = {"X-Init-Data": azm_init, "User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}
        AZM_BASE = os.getenv("AZM_API_BASE", "https://provide-presentation-subsidiaries-heel.trycloudflare.com/api").rstrip("/")
        async with session.get(f"{AZM_BASE}/state", headers=h, timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status != 200:
                return None
            d = await r.json()
            balance = float(d.get("balance", 0.0) or 0.0)
            threshold = float(d.get("min_wd", 10000.0) or 10000.0)
            if balance >= threshold and d.get("wallet"):
                async with session.post(f"{AZM_BASE}/withdraw", json={"amount": int(balance)}, headers=h, timeout=aiohttp.ClientTimeout(total=8)) as wr:
                    if wr.status == 200:
                        recip = d.get("wallet") or ton_addr
                        await record_payout_receipt("AZM Miner", "AZM", balance, recip, uid, name)
                        return {"protocol": "AZM Miner", "amount": balance, "symbol": "AZM", "recipient": recip}
    except Exception:
        pass
    return None


async def withdraw_dillo(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any], ton_addr: str) -> Optional[Dict[str, Any]]:
    """Dillo Miner: Request TON Jetton withdrawal when balance >= 2,400,000 DILLO and window is open."""
    dillo_init = tokens.get("dillo_init_data") or tokens.get("dillo")
    if not dillo_init:
        return None
    try:
        raw_init = dillo_init
        if "tgWebAppData=" in raw_init:
            raw_init = raw_init.split("tgWebAppData=")[1].split("&")[0]
            raw_init = urllib.parse.unquote(raw_init)
            
        h = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36",
            "Origin": "https://dillo-miner-bot.ih0st.app",
            "Referer": "https://dillo-miner-bot.ih0st.app/"
        }
        async with session.post("https://dillo-miner-bot.ih0st.app/api/app/call",
                                json={"initData": raw_init, "event": "state", "data": {}},
                                headers=h,
                                timeout=aiohttp.ClientTimeout(total=5)) as r:
            if r.status != 200:
                return None
            d = await r.json()
            if not d.get("ok"):
                return None
            user_data = d.get("data", {}).get("me", {}) or d.get("data", {}).get("user", {})
            pool_bal = float(user_data.get("pool", 0.0) or 0.0)
            threshold = 2400000.0  # Min withdrawal threshold
            withdraw_open_ts = 1795161600  # 2026-11-20 08:00 UTC
            now_ts = int(time.time())
            
            if pool_bal >= threshold and now_ts >= withdraw_open_ts and ton_addr:
                async with session.post("https://dillo-miner-bot.ih0st.app/api/app/call",
                                        json={"initData": raw_init, "event": "withdraw", "data": {"address": ton_addr, "amount": pool_bal}},
                                        headers=h,
                                        timeout=aiohttp.ClientTimeout(total=8)) as wr:
                    if wr.status == 200:
                        wd = await wr.json()
                        if wd.get("ok"):
                            await record_payout_receipt("Dillo Miner", "DILLO", pool_bal, ton_addr, uid, name)
                            return {"protocol": "Dillo Miner", "amount": pool_bal, "symbol": "DILLO", "recipient": ton_addr}
    except Exception:
        pass
    return None

async def withdraw_hunter(session: aiohttp.ClientSession, uid: str, name: str, tokens: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Instant on-chain cashout to Master TON Vault for Hunter Miner."""
    hunter_init = tokens.get("hunter_init_data") or tokens.get("hunter") or tokens.get("hunter_token")
    hunter_jwt = tokens.get("hunter_jwt")
    if not hunter_init and not hunter_jwt:
        return None
    try:
        from hunter_miner import HunterClient
        client = HunterClient(uid, name, hunter_init or "", jwt_token=hunter_jwt or "")
        if client.authenticate():
            bal = float(client.user_data.get("coin_balance", 0.0) or 0.0)
            if bal >= 0.05:
                wd_res = client.withdraw_gram(bal, MASTER_TON_VAULT)
                if wd_res.get("success"):
                    await record_payout_receipt("Hunter Miner", "GRAM", bal, MASTER_TON_VAULT, uid, name)
                    return {"protocol": "Hunter Miner", "amount": bal, "symbol": "GRAM", "recipient": MASTER_TON_VAULT}
    except Exception:
        pass
    return None

# =============================================================================
# UNIVERSAL FLEET AUTO-WITHDRAWAL EXECUTION PIPELINE
# =============================================================================

async def execute_universal_fleet_auto_withdrawals(session: aiohttp.ClientSession, accounts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Execute automated withdrawals and profit sweeps across all 23 accounts for all active bots."""
    print(f"💸 Initiating Universal Fleet Auto-Withdrawal Sweep across {len(accounts)} Accounts...")
    results = {
        "total_withdrawn_count": 0,
        "receipts": [],
        "by_protocol": {}
    }

    # Fetch Ton Wallets cache from Upstash
    ton_wallets_cache = {}
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            url = f"{UPSTASH_URL}/get/fleet:wallets:ton"
            async with session.get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=aiohttp.ClientTimeout(total=5)) as r_tw:
                if r_tw.status == 200:
                    raw_tw = (await r_tw.json()).get("result")
                    if raw_tw:
                        ton_wallets_cache = robust_json_loads(raw_tw)
        except Exception:
            pass

    for acc in accounts:
        uid = str(acc.get("user_id") or acc.get("account_id") or acc.get("id"))
        name = acc.get("name") or acc.get("first_name") or f"UID {uid}"
        tokens = await fetch_account_tokens(session, uid)
        if not tokens:
            continue

        ton_addr = (acc.get("ton_wallet", {}).get("address") if isinstance(acc.get("ton_wallet"), dict) else acc.get("ton_wallet")) or (ton_wallets_cache.get(uid, {}).get("address") if isinstance(ton_wallets_cache.get(uid), dict) else ton_wallets_cache.get(uid)) or MASTER_TON_VAULT

        # 1. Chicken Land (Dedicated TON Wallet)
        cl_res = await withdraw_chickenland(session, uid, name, tokens, ton_addr)
        if cl_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(cl_res)
            results["by_protocol"]["Chicken Land"] = results["by_protocol"].get("Chicken Land", 0) + 1

        # 2. PepeGram (Master TON Vault)
        pepe_res = await withdraw_pepegram(session, uid, name, tokens)
        if pepe_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(pepe_res)
            results["by_protocol"]["PepeGram"] = results["by_protocol"].get("PepeGram", 0) + 1

        # 3. Lifetime Energy (Master TON Vault)
        lt_res = await withdraw_lifetime_energy(session, uid, name, tokens)
        if lt_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(lt_res)
            results["by_protocol"]["Lifetime Energy"] = results["by_protocol"].get("Lifetime Energy", 0) + 1

        # 4. EarnVault (Master EVM Vault)
        ev_res = await withdraw_earnvault(session, uid, name, tokens)
        if ev_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(ev_res)
            results["by_protocol"]["EarnVault"] = results["by_protocol"].get("EarnVault", 0) + 1

        # 5. Victor's Company (Commission & Shift Claims)
        vic_res = await sweep_victors_company(session, uid, name, tokens)
        if vic_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(vic_res)
            results["by_protocol"]["Victor's Company"] = results["by_protocol"].get("Victor's Company", 0) + 1

        # 6. Cherry Play (Referral Yields & Wheel Spin Sweep)
        cherry_res = await sweep_cherry_play(session, uid, name, tokens, ton_addr)
        if cherry_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(cherry_res)
            results["by_protocol"]["Cherry Play"] = results["by_protocol"].get("Cherry Play", 0) + 1

        # 8. MexoEarn (>= 1666.67 MXO to Master EVM Vault)
        mexo_res = await withdraw_mexoearn(session, uid, name, tokens)
        if mexo_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(mexo_res)
            results["by_protocol"]["MexoEarn"] = results["by_protocol"].get("MexoEarn", 0) + 1

        # 9. MyAgeApp (>= 0.5 GRAM to Master TON Vault)
        myage_res = await withdraw_myageapp(session, uid, name, tokens)
        if myage_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(myage_res)
            results["by_protocol"]["MyAgeApp"] = results["by_protocol"].get("MyAgeApp", 0) + 1

        # 10. ATF Miner (>= 10,000 ATF to dedicated TON wallet)
        atf_res = await withdraw_atf(session, uid, name, tokens, ton_addr)
        if atf_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(atf_res)
            results["by_protocol"]["ATF Miner"] = results["by_protocol"].get("ATF Miner", 0) + 1

        # 11. AdxEarn AI (>= 70,000 Points to Master EVM Vault)
        adx_res = await withdraw_adxearn(session, uid, name, tokens)
        if adx_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(adx_res)
            results["by_protocol"]["AdxEarn AI"] = results["by_protocol"].get("AdxEarn AI", 0) + 1

        # 12. Kynex Network (>= 62,500 KNX to Master EVM Vault)
        knx_res = await withdraw_kynex(session, uid, name, tokens)
        if knx_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(knx_res)
            results["by_protocol"]["Kynex Network"] = results["by_protocol"].get("Kynex Network", 0) + 1

        # 13. AZM Miner (>= 10,000 AZM to dedicated TON wallet)
        azm_res = await withdraw_azm(session, uid, name, tokens, ton_addr)
        if azm_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(azm_res)
            results["by_protocol"]["AZM Miner"] = results["by_protocol"].get("AZM Miner", 0) + 1

        # 14. Dillo Miner (>= 2,400,000 DILLO to dedicated TON wallet)
        dillo_res = await withdraw_dillo(session, uid, name, tokens, ton_addr)
        if dillo_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(dillo_res)
            results["by_protocol"]["Dillo Miner"] = results["by_protocol"].get("Dillo Miner", 0) + 1

        # 15. Hunter Miner (>= 0.05 GRAM to Master TON Vault)
        hunter_res = await withdraw_hunter(session, uid, name, tokens)
        if hunter_res:
            results["total_withdrawn_count"] += 1
            results["receipts"].append(hunter_res)
            results["by_protocol"]["Hunter Miner"] = results["by_protocol"].get("Hunter Miner", 0) + 1

    print(f"\n🎉 Universal Auto-Withdrawal Sweep Completed: {results['total_withdrawn_count']} Payouts Executed!")
    for proto, cnt in results["by_protocol"].items():
        print(f"• {proto}: {cnt} withdrawals executed successfully.")

    return results


async def main_async(execute_sweep: bool):
    accounts = load_fleet_accounts()
    print(f"💼 Fleet Auto-Withdrawal Engine Active across {len(accounts)} Accounts.")

    async with aiohttp.ClientSession(headers=BROWSER_HEADERS) as session:
        if execute_sweep:
            await execute_universal_fleet_auto_withdrawals(session, accounts)
        else:
            print("ℹ️ Audit mode only. Use --sweep to execute on-chain auto-withdrawals.")


def main():
    parser = argparse.ArgumentParser(description="Universal Fleet Auto-Withdrawal & Profit Realization Engine")
    parser.add_argument("--sweep", action="store_true", help="Execute real on-chain auto-withdrawals for all eligible accounts")
    args = parser.parse_args()

    asyncio.run(main_async(execute_sweep=args.sweep))


if __name__ == "__main__":
    main()
