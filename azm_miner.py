#!/usr/bin/env python3
"""
azm_miner.py
=============================================================================
Autonomous Mining Engine & Harvester for AZM Miner (@AZMIR_Miner_BOT)
=============================================================================
Protocol Specifications:
  - Bot: @AZMIR_Miner_BOT (AZM Miner GRAM)
  - WebApp Base: https://amd-dot-thin-resident.trycloudflare.com/
  - Upstream API: https://amd-dot-thin-resident.trycloudflare.com/api
  - Base Mining Speed: 15.0 AZM/hour (360.0 AZM/day at Level 1)
  - Currency: AZM Token (GRAM / TON Ecosystem)
  - Referral System: 250 AZM per invited friend who connects a wallet (Ref: 6727787768)

Operations:
  1. GET /api/state — Fetch live balance, pending mined AZM, level, and checkin status.
  2. POST /api/wallet — Binds dedicated non-custodial TON wallet to activate 15 AZM/h mining.
  3. POST /api/checkin — Daily check-in (+4 AZM up to +750 AZM streak).
  4. POST /api/join/check — Verifies @AZMIR_Channel subscription.
  5. POST /api/claim — Harvests accumulated mined AZM into the in-app balance.
"""

import os
import sys
import json
import time
import asyncio
import logging
import aiohttp
from typing import Dict, Any, Optional

import urllib.request

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("azm_miner")

AZM_API_BASE_DEFAULT = "https://amd-dot-thin-resident.trycloudflare.com/api"
MASTER_UID = "6727787768"
AZM_CIRCUIT_BROKEN = False

def get_azm_api_base() -> str:
    env_base = os.getenv("AZM_API_BASE")
    if env_base:
        return env_base
    up_url = os.getenv("UPSTASH_URL") or os.getenv("UPSTASH_REDIS_REST_URL")
    up_tok = os.getenv("UPSTASH_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN")
    if up_url and up_tok:
        try:
            req = urllib.request.Request(f"{up_url}/get/fleet:azm_api_base", headers={"Authorization": f"Bearer {up_tok}"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode())
                res = d.get("result")
                if res and isinstance(res, str) and res.startswith("http"):
                    return res.strip().rstrip("/")
        except Exception:
            pass
    return AZM_API_BASE_DEFAULT

AZM_API_BASE = get_azm_api_base()

async def farm_azm_account(
    session: aiohttp.ClientSession,
    init_data: str,
    acc: dict,
    ton_wallet_addr: Optional[str] = None
) -> Dict[str, Any]:
    """
    Farms an individual account on AZM Miner.
    Performs wallet activation, daily check-in, channel verification, and harvest claim.
    """
    global AZM_CIRCUIT_BROKEN
    uid = str(acc.get("user_id") or acc.get("account_id") or "")
    name = acc.get("name") or acc.get("first_name", f"Account #{uid}")
    res = {
        "bot": "AZM Miner",
        "success": False,
        "balance": 0.0,
        "mined_claimed": 0.0,
        "daily_claimed": False,
        "active": False,
        "hourly": 15.0,
        "notes": []
    }

    if AZM_CIRCUIT_BROKEN:
        res["notes"].append("tunnel_offline_skipped")
        return res

    if not init_data:
        res["notes"].append("missing_init_data")
        return res

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36",
        "X-Init-Data": init_data,
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    async def api_post(endpoint: str, payload: dict) -> Optional[dict]:
        url = f"{AZM_API_BASE}{endpoint}"
        try:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                if resp.status in (200, 201):
                    return await resp.json()
                elif resp.status == 400:
                    try:
                        return await resp.json()
                    except Exception:
                        return {"error": "HTTP 400 Bad Request"}
                else:
                    res["notes"].append(f"http_{resp.status}_{endpoint.strip('/')}")
                    return None
        except Exception as e:
            res["notes"].append(f"err_{endpoint.strip('/')}_{str(e)[:30]}")
            return None

    # 1. Query Current State
    try:
        async with session.get(f"{AZM_API_BASE}/state", headers=headers, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status != 200:
                res["notes"].append(f"state_http_{resp.status}")
                return res
            state = await resp.json()
    except Exception as e:
        AZM_CIRCUIT_BROKEN = True
        res["notes"].append(f"state_err_{str(e)[:25]}")
        return res

    res["success"] = True
    res["balance"] = float(state.get("balance", 0.0) or 0.0)
    res["active"] = bool(state.get("active", False))
    res["hourly"] = float(state.get("hourly", 15.0) or 15.0)

    # 2. Activate Miner with TON Wallet if not active or wallet empty
    if not res["active"] or not state.get("wallet"):
        wallet_to_bind = ton_wallet_addr or acc.get("ton_wallet") or acc.get("ton")
        if wallet_to_bind:
            w_res = await api_post("/wallet", {"wallet": wallet_to_bind})
            if w_res and w_res.get("active"):
                res["active"] = True
                res["notes"].append("wallet_bound_miner_activated")
                logger.info(f"[{name}] AZM Miner activated with TON wallet: {wallet_to_bind[:8]}...")

    # 3. Daily Check-in Claim
    checkin_info = state.get("checkin", {})
    if not checkin_info.get("claimed_today"):
        ck_res = await api_post("/checkin", {})
        if ck_res and ck_res.get("checkin", {}).get("claimed_today"):
            res["daily_claimed"] = True
            res["balance"] = float(ck_res.get("balance", res["balance"]))
            res["notes"].append("daily_checkin_claimed")
            logger.info(f"[{name}] AZM Miner daily checkin claimed! New bal: {res['balance']} AZM")

    # 4. Verify Channel Membership if needed
    if not state.get("joined"):
        jn_res = await api_post("/join/check", {})
        if jn_res and jn_res.get("joined"):
            res["notes"].append("channel_join_verified")

    # 5. Harvest Accumulated Mined AZM
    pending = float(state.get("pending", 0.0) or 0.0)
    if pending > 0.01:
        cl_res = await api_post("/claim", {})
        if cl_res and cl_res.get("report"):
            rep = cl_res["report"]
            amt = float(rep.get("amount", 0.0) or 0.0)
            res["mined_claimed"] = amt
            res["balance"] = float(rep.get("balance", res["balance"]))
            res["notes"].append(f"harvested_+{amt:.4f}azm")
            logger.info(f"[{name}] AZM Miner harvest claimed: +{amt:.4f} AZM (Balance: {res['balance']} AZM)")

    return res

if __name__ == "__main__":
    print("AZM Miner Engine compiled successfully.")
