#!/usr/bin/env python3
"""
cherry_miner.py
=============================================================================
AAA AUTO — Cherry Play (@CherryPlayBot) Autonomous Cloud Protocol Engine
=============================================================================
Protocol: Cherry Play / Cherry Earn (Web3 Gaming, Tasks, Daily Streaks & Referrals)
Telegram Bot: @CherryPlayBot
WebApp URL: https://cherryearn.site
API Base: https://api.cherryearn.site/api
Master Referral: r9bsfi3a8c (https://t.me/CherryPlayBot/play?startapp=r9bsfi3a8c)

Capabilities:
1. Profile bootstrap & balance sync (Cherries, Tickets, Level, Referrals)
2. Auto-onboarding & preferences synchronization
3. Daily check-in streak harvest (/streak/claim)
4. Free Lucky Wheel spin execution (/spin)
5. Referral commission claim & sweep (/friends/claim)
6. Autonomous task execution (start, verify, claim)
7. Non-custodial TON wallet binding (/wallet)
8. Multi-account batch farming across all 23 fleet accounts
=============================================================================
"""

import os
import sys
import time
import json
import random
import hashlib
import urllib.parse
import asyncio
import aiohttp
from typing import Dict, List, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_TOKEN", "")
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_NOTIFICATION_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")

MASTER_TON_VAULT = "UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT"
CHERRY_REF_CODE = "r9bsfi3a8c"
API_BASE = "https://api.cherryearn.site/api"
CF_SECRET_KEY = os.getenv("CF_SECRET_KEY") or os.getenv("SECRET_KEY", "agy_cf_secret_7d36994e_2026")
CF_PROXY_NODES = [
    "https://restore-agy.agorameet.workers.dev/api/cherry-proxy",
    "https://restore-agy.aaa-bot.workers.dev/api/cherry-proxy",
    "https://restore-agy.aaaai2.workers.dev/api/cherry-proxy",
    "https://restore-agy.aaa222.workers.dev/api/cherry-proxy",
    "https://restore-agy.aaaai.workers.dev/api/cherry-proxy"
]

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://cherryearn.site",
    "Referer": "https://cherryearn.site/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site"
}


def robust_json_loads(val: Any) -> Any:
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


def generate_device_id(uid: str) -> str:
    """Generates a stable 36-char hex device ID per account to comply with server X-Device header."""
    return hashlib.sha256(f"cherry_dev_{uid}".encode()).hexdigest()[:36]


class CherryClient:
    def __init__(self, uid: str, name: str, init_data: str, ton_wallet: str = MASTER_TON_VAULT):
        self.uid = str(uid)
        self.name = name
        self.init_data = init_data
        self.ton_wallet = ton_wallet
        self.device_id = generate_device_id(self.uid)
        self.balance = 0
        self.tickets = 0
        self.streak_day = 0
        self.today_earned = 0
        self.total_earned = 0
        self.referrals_count = 0
        self.ref_code = ""

    def get_headers(self) -> Dict[str, str]:
        clean_init = self.init_data
        if clean_init.startswith("tma "):
            clean_init = clean_init[4:].strip()
        return {
            **BROWSER_HEADERS,
            "Authorization": f"tma {clean_init}",
            "Content-Type": "application/json",
            "X-Device": self.device_id
        }

    async def call_api(self, session: aiohttp.ClientSession, path: str, body: Optional[Dict[str, Any]] = None, method: str = "GET") -> Optional[Dict[str, Any]]:
        url = f"{API_BASE}{path}"
        headers = self.get_headers()
        try:
            if body is not None or method == "POST":
                payload = json.dumps(body if body is not None else {})
                async with session.post(url, data=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in [200, 201]:
                        return await resp.json()
                    elif resp.status == 401:
                        return {"error": "unauthorized", "status": 401}
                    elif resp.status in (423, 429):
                        return await self._call_proxy(session, path, body, method, headers)
                    else:
                        txt = await resp.text()
                        return {"error": f"http_{resp.status}", "detail": txt[:200], "status": resp.status}
            else:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status in [200, 201]:
                        return await resp.json()
                    elif resp.status == 401:
                        return {"error": "unauthorized", "status": 401}
                    elif resp.status in (423, 429):
                        return await self._call_proxy(session, path, body, method, headers)
                    else:
                        txt = await resp.text()
                        return {"error": f"http_{resp.status}", "detail": txt[:200], "status": resp.status}
        except Exception as e:
            return await self._call_proxy(session, path, body, method, headers)

    async def _call_proxy(self, session: aiohttp.ClientSession, path: str, body: Optional[Dict[str, Any]], method: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Rotates request across Cloudflare Edge proxy nodes when IP-gated."""
        if not CF_PROXY_NODES:
            return {"error": "http_423", "detail": "IP rate limit, no proxy configured", "status": 423}
        start_idx = (int(self.uid) if self.uid.isdigit() else 0) % len(CF_PROXY_NODES)
        ordered_proxies = CF_PROXY_NODES[start_idx:] + CF_PROXY_NODES[:start_idx]
        for p_url in ordered_proxies:
            try:
                p_payload = {
                    "path": path,
                    "method": method,
                    "headers": headers,
                    "data": body if body is not None else {}
                }
                p_headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    "Authorization": f"Bearer {CF_SECRET_KEY}"
                }
                async with session.post(p_url, json=p_payload, headers=p_headers, timeout=aiohttp.ClientTimeout(total=8)) as p_resp:
                    if p_resp.status == 200:
                        p_d = await p_resp.json()
                        if p_d.get("ok"):
                            return p_d.get("data")
            except Exception:
                continue
        return {"error": "http_423", "detail": "All Cloudflare proxy nodes attempted", "status": 423}

    async def bootstrap(self, session: aiohttp.ClientSession) -> Optional[Dict[str, Any]]:
        """Fetch full user state, tasks, streak, friends from /bootstrap."""
        res = await self.call_api(session, "/bootstrap", method="GET")
        if not res or res.get("error"):
            return res

        user = res.get("user", {})
        self.balance = float(user.get("balance", 0) or 0)
        self.tickets = int(user.get("tickets", 0) or 0)
        self.today_earned = float(user.get("today", 0) or 0)
        self.total_earned = float(user.get("total_earned", 0) or 0)
        
        streak = res.get("streak", {})
        self.streak_day = int(streak.get("day", 0) or 0)

        friends = res.get("friends", {})
        self.referrals_count = int(friends.get("count", 0) or len(friends.get("list", [])))
        self.ref_code = user.get("ref_code") or friends.get("code") or ""
        return res

    async def complete_onboarding(self, session: aiohttp.ClientSession) -> bool:
        """Mark onboarding as complete to unlock all game and task rewards."""
        res = await self.call_api(session, "/prefs", body={"onboarded": True}, method="POST")
        return bool(res and not res.get("error"))

    async def claim_streak(self, session: aiohttp.ClientSession) -> Dict[str, Any]:
        """Claim daily check-in streak cherries."""
        res = await self.call_api(session, "/streak/claim", body={}, method="POST")
        return res or {}

    async def claim_spin(self, session: aiohttp.ClientSession) -> Dict[str, Any]:
        """Execute free Lucky Wheel spin."""
        res = await self.call_api(session, "/spin", body={}, method="POST")
        return res or {}

    async def claim_referrals(self, session: aiohttp.ClientSession) -> Dict[str, Any]:
        """Claim accumulated referral commissions."""
        res = await self.call_api(session, "/friends/claim", body={}, method="POST")
        return res or {}

    async def bind_ton_wallet(self, session: aiohttp.ClientSession, addr: str) -> Dict[str, Any]:
        """Bind non-custodial TON wallet address."""
        if not addr:
            return {}
        res = await self.call_api(session, "/wallet", body={"method": "ton", "addr": addr}, method="POST")
        return res or {}

    async def harvest_tasks(self, session: aiohttp.ClientSession, tasks_data: List[Dict[str, Any]]) -> List[str]:
        """Iterate through available tasks: start, verify, and claim completed ones."""
        claimed = []
        if not tasks_data:
            return claimed

        for t in tasks_data:
            tid = t.get("id")
            title = t.get("title", f"Task {tid}")
            state = t.get("state", "new")
            reward = t.get("reward", 0)
            kind = t.get("kind", "")

            # Skip invite/deposit/telegram-bio prerequisites if not met
            if kind in ["invite", "deposit", "bio"]:
                continue

            try:
                if state == "new":
                    # Start task
                    await self.call_api(session, f"/tasks/{tid}/start", body={}, method="POST")
                    await asyncio.sleep(0.5)
                    # Verify task
                    await self.call_api(session, f"/tasks/{tid}/verify", body={}, method="POST")
                    await asyncio.sleep(0.5)
                    # Claim task
                    c_res = await self.call_api(session, f"/tasks/{tid}/claim", body={}, method="POST")
                    if c_res and not c_res.get("error"):
                        claimed.append(f"{title} (+{reward} 🍒)")

                elif state in ["wait", "verify"]:
                    # Verify & claim
                    await self.call_api(session, f"/tasks/{tid}/verify", body={}, method="POST")
                    await asyncio.sleep(0.5)
                    c_res = await self.call_api(session, f"/tasks/{tid}/claim", body={}, method="POST")
                    if c_res and not c_res.get("error"):
                        claimed.append(f"{title} (+{reward} 🍒)")

                elif state == "claim":
                    c_res = await self.call_api(session, f"/tasks/{tid}/claim", body={}, method="POST")
                    if c_res and not c_res.get("error"):
                        claimed.append(f"{title} (+{reward} 🍒)")
            except Exception:
                pass

        return claimed

    async def run_full_farming_cycle(self, session: aiohttp.ClientSession) -> Dict[str, Any]:
        """Executes the complete Cherry Play harvest cycle for this account."""
        report = {
            "uid": self.uid,
            "name": self.name,
            "balance": 0,
            "tickets": 0,
            "actions": [],
            "status": "active"
        }

        # 1. Bootstrap profile
        b_data = await self.bootstrap(session)
        if not b_data or b_data.get("error"):
            report["status"] = b_data.get("error", "bootstrap_failed") if b_data else "bootstrap_failed"
            return report

        report["balance"] = self.balance
        report["tickets"] = self.tickets

        # 2. Complete onboarding if needed
        user = b_data.get("user", {})
        if user.get("onboarded") is False:
            if await self.complete_onboarding(session):
                report["actions"].append("Onboarded ✅")

        # 3. Daily check-in streak
        streak = b_data.get("streak", {})
        if streak.get("can_claim"):
            s_res = await self.claim_streak(session)
            if s_res and not s_res.get("error"):
                rew = s_res.get("reward", 0)
                report["actions"].append(f"Streak Day {s_res.get('day', self.streak_day)} (+{rew} 🍒)")
                self.balance = float(s_res.get("balance", self.balance) or self.balance)

        # 4. Free Spin
        user = b_data.get("user", {})
        if user.get("free_spins", 0) > 0 or user.get("spins", 0) > 0:
            sp_res = await self.claim_spin(session)
            if sp_res and not sp_res.get("error"):
                p_text = sp_res.get("prize", {}).get("text", "Prizes")
                report["actions"].append(f"Spin Won: {p_text} 🎡")

        # 5. Sweep referral commissions
        friends = b_data.get("friends", {})
        if float(friends.get("unearned_commission", 0) or 0) > 0:
            f_res = await self.claim_referrals(session)
            if f_res and not f_res.get("error"):
                c_amt = f_res.get("claimed", 0)
                report["actions"].append(f"Referrals Swept (+{c_amt} 🍒)")

        # 6. Tasks
        tasks_list = b_data.get("tasks", [])
        if tasks_list:
            claimed_tasks = await self.harvest_tasks(session, tasks_list)
            for ct in claimed_tasks:
                report["actions"].append(f"Task: {ct}")

        # 7. Bind TON wallet if not yet bound
        wallets = user.get("wallets", {})
        if not wallets.get("ton") and self.ton_wallet:
            w_res = await self.bind_ton_wallet(session, self.ton_wallet)
            if w_res and not w_res.get("error"):
                report["actions"].append(f"Bound TON Wallet {self.ton_wallet[:6]}... ✅")

        report["balance"] = self.balance
        return report


async def harvest_all_cherry_fleet():
    """Runs autonomous Cherry Play farming across all 23 fleet accounts."""
    accs_file = os.path.join(BASE_DIR, "accounts.json")
    tokens_file = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")
    accs = []
    if os.path.exists(accs_file):
        try:
            with open(accs_file, "r", encoding="utf-8") as f:
                accs = [a for a in json.load(f) if str(a.get("user_id") or a.get("id")) != "8881914294"]
        except Exception:
            pass

    # Cloud fallback for accounts
    if not accs and UPSTASH_URL and UPSTASH_TOKEN:
        try:
            import urllib.request
            req = urllib.request.Request(f"{UPSTASH_URL}/get/fleet:accounts", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode())
                res = robust_json_loads(d.get("result"))
                if isinstance(res, list):
                    accs = [a for a in res if str(a.get("user_id") or a.get("id")) != "8881914294"]
        except Exception:
            pass

    if not accs:
        print("❌ No accounts loaded from local or cloud stores.")
        return

    tokens_db = {}
    if os.path.exists(tokens_file):
        try:
            with open(tokens_file, "r", encoding="utf-8") as f:
                tokens_db = json.load(f)
        except Exception:
            pass

    # Cloud fallback for tokens
    if not tokens_db and UPSTASH_URL and UPSTASH_TOKEN:
        try:
            import urllib.request
            req = urllib.request.Request(f"{UPSTASH_URL}/get/miniapp_tokens", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode())
                res = robust_json_loads(d.get("result"))
                if isinstance(res, dict):
                    tokens_db = res
        except Exception:
            pass

    # Load TON wallets
    ton_wallets_file = os.path.join(BASE_DIR, "fleet_ton_wallets.json")
    ton_map = {}
    if os.path.exists(ton_wallets_file):
        try:
            with open(ton_wallets_file, "r", encoding="utf-8") as f:
                ton_map = json.load(f)
        except Exception:
            pass

    if not ton_map and UPSTASH_URL and UPSTASH_TOKEN:
        try:
            import urllib.request
            req = urllib.request.Request(f"{UPSTASH_URL}/get/fleet:wallets:ton", headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode())
                res = robust_json_loads(d.get("result"))
                if isinstance(res, dict):
                    ton_map = res
        except Exception:
            pass

    print("=" * 75)
    print(f"🍒 AAA AUTO — CHERRY PLAY AUTONOMOUS FLEET FARMING ({len(accs)} ACCOUNTS)")
    print("=" * 75)

    total_cherries = 0.0
    active_count = 0

    async with aiohttp.ClientSession() as session:
        for idx, acc in enumerate(accs):
            uid = str(acc.get("user_id") or acc.get("account_id") or acc.get("id"))
            name = acc.get("name") or acc.get("first_name") or f"UID {uid}"
            t_data = tokens_db.get(uid, {})
            
            init_data = (
                t_data.get("cherry_init_data")
                or t_data.get("cherryplay_init_data")
                or t_data.get("cherry")
                or acc.get("cherry_init_data")
                or acc.get("cherry")
            )

            ton_addr = (
                (ton_map.get(uid, {}).get("address") if isinstance(ton_map.get(uid), dict) else ton_map.get(uid))
                or (acc.get("ton_wallet", {}).get("address") if isinstance(acc.get("ton_wallet"), dict) else acc.get("ton_wallet"))
                or MASTER_TON_VAULT
            )

            if not init_data:
                print(f"[{idx+1}/{len(accs)}] 🍒 [{name}] Token missing — awaiting cloud token refresher")
                continue

            client = CherryClient(uid=uid, name=name, init_data=init_data, ton_wallet=ton_addr)
            res = await client.run_full_farming_cycle(session)
            bal = res.get("balance", 0)
            total_cherries += bal
            active_count += 1

            actions_str = " • ".join(res.get("actions", [])) or "Harvested / In Sync"
            print(f"[{idx+1}/{len(accs)}] 🍒 [{name}] Balance: {bal:,.0f} 🍒 | Actions: {actions_str}")
            await asyncio.sleep(0.5)

    print("=" * 75)
    print(f"🎉 Cherry Play Fleet Farm Complete: {active_count}/{len(accs)} Accounts Active | Total Treasury: {total_cherries:,.0f} 🍒")
    print("=" * 75)


def main():
    asyncio.run(harvest_all_cherry_fleet())


if __name__ == "__main__":
    main()
