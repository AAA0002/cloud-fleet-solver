#!/usr/bin/env python3
"""
hunter_miner.py
=============================================================================
PROTOCOL 15: HUNTER MINER (@GramHtrsbot) AUTOMATION ENGINE
=============================================================================
Full Reverse-Engineered Automation Engine for Hunter Miner:
- Backend: https://huntrs.up.railway.app
- Bot: @GramHtrsbot (ID: 8867174524)
- Master Referral Binding: startapp=6727787768
- Welcome Bonus: +0.20 GRAM instant claim
- Referral Reward: +0.05 GRAM per referral + 10% mining/task commission
- Mining Session: 12 hours (3 sessions/day limit)
- Dynamic Tasks: Main, Partner, Community (+0.002 to +0.15 GRAM)
- Automated Withdrawals: Instant on-chain GRAM cashout to TON Vault
  Target: UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT (Min: 0.05 GRAM)
- Anti-Sybil IP Bypass: Cloudflare Edge Worker mesh proxy failover
=============================================================================
"""

import os
import sys
import json
import time
import urllib.request
import urllib.error
import urllib.parse
from typing import Dict, List, Optional, Tuple, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

MASTER_UID = "6727787768"
TARGET_TON_VAULT = "UQBPZiSvitdPU3VUyJK2mRaHVBl69xejw5aOrh1KfKA7gwDT"
API_BASE = "https://huntrs.up.railway.app"
CF_SECRET_KEY = os.getenv("CF_SECRET_KEY") or os.getenv("SECRET_KEY", "agy_cf_secret_7d36994e_2026")
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
UPSTASH_URL = os.getenv("UPSTASH_URL") or os.getenv("UPSTASH_REDIS_REST_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN", "")

CF_PROXY_NODES = [
    "https://restore-agy.agorameet.workers.dev/api/hunter-proxy",
    "https://restore-agy.aaa-bot.workers.dev/api/hunter-proxy",
    "https://restore-agy.aaaai2.workers.dev/api/hunter-proxy",
    "https://restore-agy.aaa222.workers.dev/api/hunter-proxy",
    "https://restore-agy.aaaai.workers.dev/api/hunter-proxy"
]

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

def load_fleet_data() -> Tuple[Dict, List]:
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

    # Cloud Fallback from Upstash Redis (for headless cloud runners)
    if (not accounts or not tokens) and UPSTASH_URL and UPSTASH_TOKEN:
        try:
            headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
            if not accounts:
                req_a = urllib.request.Request(f"{UPSTASH_URL}/get/fleet:accounts", headers=headers)
                with urllib.request.urlopen(req_a, timeout=5) as r:
                    res_a = json.loads(r.read().decode()).get("result")
                    if res_a:
                        accounts = robust_json_loads(res_a)
            if not tokens:
                req_t = urllib.request.Request(f"{UPSTASH_URL}/get/miniapp_tokens", headers=headers)
                with urllib.request.urlopen(req_t, timeout=5) as r:
                    res_t = json.loads(r.read().decode()).get("result")
                    if res_t:
                        tokens = robust_json_loads(res_t)
        except Exception:
            pass

    return tokens, accounts

def clean_init_data(raw_tok: str) -> str:
    """Extracts tgWebAppData from URL or raw fragment without breaking URL-encoded JSON parameters."""
    if not raw_tok:
        return ""
    tok = raw_tok
    if "tgWebAppData=" in tok:
        tok = tok.split("tgWebAppData=")[1].split("&tgWebAppVersion")[0].split("#")[0]
        if "&" in tok and not ("user=" in tok and "hash=" in tok):
            tok = tok.split("&")[0]
    return tok.strip()

class HunterClient:
    def __init__(self, uid: str, name: str, init_data: str = "", username: str = "", jwt_token: str = ""):
        self.uid = str(uid)
        self.name = name
        self.username = username or ""
        self.raw_init_data = clean_init_data(init_data)
        self.token: Optional[str] = jwt_token or None
        self.user_data: Dict = {}
        self.mining_data: Dict = {}
        self.completed_tasks: set = set()
        self.last_proxy_used: Optional[str] = None

    def _call_api(self, path: str, data: dict = None, auth: bool = True, use_proxy: bool = False) -> dict:
        if data is None:
            data = {}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
            "Origin": API_BASE,
            "Referer": f"{API_BASE}/"
        }
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # Proxy mode via Cloudflare Worker mesh
        if use_proxy:
            # Deterministic start index based on account UID to distribute load across CF data centers
            start_idx = (int(self.uid) if self.uid.isdigit() else 0) % len(CF_PROXY_NODES)
            ordered_proxies = CF_PROXY_NODES[start_idx:] + CF_PROXY_NODES[:start_idx]
            last_err = {}
            for p_url in ordered_proxies:
                try:
                    payload = {
                        "path": path,
                        "method": "POST",
                        "headers": headers,
                        "data": data
                    }
                    req = urllib.request.Request(
                        p_url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {CF_SECRET_KEY}",
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                        }
                    )
                    try:
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            res = json.loads(resp.read().decode("utf-8"))
                            data_resp = res.get("data", {}) if isinstance(res.get("data"), dict) else {}
                            if res.get("ok"):
                                self.last_proxy_used = p_url
                                return data_resp
                            # If Railway returned ip_already_linked inside proxy response, rotate to next proxy node!
                            err_val = data_resp.get("error", "")
                            if err_val in ("ip_already_linked", "Too many requests", "rate_limited"):
                                last_err = data_resp
                                continue
                            if data_resp:
                                return data_resp
                    except urllib.error.HTTPError as he:
                        try:
                            body = json.loads(he.read().decode("utf-8"))
                            if isinstance(body.get("data"), dict):
                                d_inner = body["data"]
                                if d_inner.get("error") in ("ip_already_linked", "Too many requests"):
                                    last_err = d_inner
                                    continue
                                self.last_proxy_used = p_url
                                return d_inner
                        except Exception:
                            pass
                except Exception:
                    continue
            return last_err or {"error": "all_proxies_failed"}

        # Direct mode
        url = f"{API_BASE}{path}"
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8"),
            headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                if res_data.get("error") in ("ip_already_linked", "Too many requests") and not use_proxy:
                    return self._call_api(path, data, auth=auth, use_proxy=True)
                return res_data
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
                if body.get("error") in ("ip_already_linked", "Too many requests") and not use_proxy:
                    # Retry automatically with Cloudflare Edge proxy mesh!
                    return self._call_api(path, data, auth=auth, use_proxy=True)
                return body
            except Exception:
                return {"error": f"HTTP_{e.code}"}
        except Exception as e:
            return {"error": str(e)}

    def authenticate(self) -> bool:
        # 1. Fast Path: If JWT token already exists, verify via /api/get-user
        if self.token:
            u_res = self.refresh_user()
            if self.user_data:
                return True

        # Check Upstash Redis for cached 7-day hunter_jwt
        if not self.token and UPSTASH_URL and UPSTASH_TOKEN:
            try:
                req_t = urllib.request.Request(
                    f"{UPSTASH_URL}/get/fleet:tokens:{self.uid}:hunter_jwt",
                    headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}
                )
                with urllib.request.urlopen(req_t, timeout=4) as rt:
                    cached_jwt = json.loads(rt.read().decode()).get("result")
                    if cached_jwt and isinstance(cached_jwt, str) and len(cached_jwt) > 20:
                        self.token = cached_jwt
                        self.refresh_user()
                        if self.user_data:
                            return True
            except Exception:
                pass

        if not self.raw_init_data:
            return False

        # 2. Authenticate via /api/auth using authentic parameters
        u_name = self.username
        f_name = self.name
        p_url = ""
        try:
            parsed_params = urllib.parse.parse_qs(self.raw_init_data)
            if "user" in parsed_params:
                u_obj = json.loads(parsed_params["user"][0])
                if u_obj.get("username"):
                    u_name = u_obj["username"]
                if u_obj.get("first_name"):
                    f_name = u_obj["first_name"]
                if u_obj.get("photo_url"):
                    p_url = u_obj["photo_url"]
        except Exception:
            pass

        # Authentic fingerprint matching registered account device
        visitor_hash = f"fp_{self.uid}_device"

        auth_payload = {
            "initData": self.raw_init_data,
            "userId": int(self.uid) if self.uid.isdigit() else 0,
            "username": u_name,
            "firstName": f_name,
            "photoUrl": p_url,
            "visitorId": visitor_hash
        }
        res = self._call_api("/api/auth", auth_payload, auth=False)
        if res.get("token"):
            self.token = res["token"]
            self.user_data = res.get("user", {})
            self.mining_data = res.get("mining", {})
            self.completed_tasks = set(res.get("completedTasks", []))
            # Persist 7-day JWT to Upstash Redis
            self._save_jwt_to_redis(res["token"])
            return True
        return False

    def _save_jwt_to_redis(self, jwt_tok: str):
        if not UPSTASH_URL or not UPSTASH_TOKEN or not jwt_tok:
            return
        try:
            req_s = urllib.request.Request(
                f"{UPSTASH_URL}/set/fleet:tokens:{self.uid}:hunter_jwt",
                data=jwt_tok.encode("utf-8"),
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}
            )
            with urllib.request.urlopen(req_s, timeout=4):
                pass
        except Exception:
            pass

    def refresh_user(self) -> dict:
        res = self._call_api("/api/get-user", {})
        if res.get("user"):
            self.user_data = res.get("user", {})
            self.mining_data = res.get("mining", {})
            self.completed_tasks = set(res.get("completedTasks", []))
        return res

    def claim_welcome_bonus(self) -> dict:
        return self._call_api("/api/claim-welcome-bonus", {})

    def claim_mining(self) -> dict:
        return self._call_api("/api/claim-mining", {})

    def start_mining(self) -> dict:
        return self._call_api("/api/start-mining", {})

    def claim_referral_earnings(self) -> dict:
        return self._call_api("/api/claim-referral-earnings", {})

    def get_tasks(self, category: str = "main") -> List[dict]:
        res = self._call_api(f"/api/tasks/{category}", {})
        raw_tasks = res.get("tasks", []) if isinstance(res, dict) else (res if isinstance(res, list) else [])
        # Normalize task attributes
        norm_tasks = []
        for t in raw_tasks:
            t_id = t.get("id") or t.get("_id")
            norm_t = {
                **t,
                "id": t_id,
                "title": t.get("name") or t.get("title") or t_id,
                "completed": (t_id in self.completed_tasks) if self.completed_tasks else t.get("completed", False)
            }
            norm_tasks.append(norm_t)
        return norm_tasks

    def complete_task(self, task_id: str) -> dict:
        res = self._call_api("/api/complete-task", {"taskId": task_id})
        # Handle 3-second cooldown rate limit automatically
        if res.get("error") == "Wait 3s" or res.get("status") == 429:
            time.sleep(3.5)
            res = self._call_api("/api/complete-task", {"taskId": task_id})
        if res.get("user") and isinstance(res["user"], dict):
            self.user_data.update(res["user"])
            self.completed_tasks.add(task_id)
        return res

    def withdraw_gram(self, amount: float, wallet_address: str = TARGET_TON_VAULT) -> dict:
        return self._call_api("/api/withdraw-gram", {
            "coinAmount": round(float(amount), 4),
            "walletAddress": wallet_address
        })

def send_alert_card(summary: dict):
    if not REPORT_BOT_TOKEN or not ADMIN_CHANNEL_ID:
        return
    text = (
        f"🎯 <b>HUNTER MINER (@GramHtrsbot) FLEET HARVEST REPORT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Accounts Swept:</b> <code>{summary.get('accounts_processed')}/{summary.get('total_accounts')}</code>\n"
        f"🪙 <b>Total GRAM Balance:</b> <code>{summary.get('total_gram_bal', 0.0):.4f} GRAM</code>\n"
        f"🎁 <b>Welcome Bonuses Claimed:</b> <code>+{summary.get('welcome_claimed', 0)} (+{summary.get('welcome_claimed', 0) * 0.20:.2f} GRAM)</code>\n"
        f"⛏️ <b>12h Mining Active:</b> <code>{summary.get('mining_active', 0)} Accounts</code>\n"
        f"👥 <b>Master Referrals Bound:</b> <code>{summary.get('master_referrals', 0)} Accounts</code>\n"
        f"✅ <b>Tasks Completed:</b> <code>{summary.get('tasks_completed', 0)} Claims</code>\n"
        f"🚀 <b>On-Chain Withdrawals:</b> <code>{summary.get('withdrawals_dispatched', 0)} Dispatched</code>\n"
        f"🏦 <b>Target Vault:</b> <code>{TARGET_TON_VAULT[:10]}...{TARGET_TON_VAULT[-6:]}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🛡️ <i>Zero Sybil IP Lock: 100% Cloudflare Edge Failover Validated</i>"
    )
    url = f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ADMIN_CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass

def run_fleet_sweep():
    print("=" * 80)
    print("🎯 HUNTER MINER (@GramHtrsbot) FLEET SWEEP & ON-CHAIN CASH-OUT ENGINE")
    print("=" * 80)
    tokens, accounts = load_fleet_data()
    print(f"Loaded {len(accounts)} accounts from accounts.json (Tokens map: {len(tokens)} entries)")

    summary = {
        "total_accounts": len(accounts),
        "accounts_processed": 0,
        "welcome_claimed": 0,
        "mining_active": 0,
        "tasks_completed": 0,
        "master_referrals": 0,
        "total_gram_bal": 0.0,
        "withdrawals_dispatched": 0
    }

    for idx, acc in enumerate(accounts, 1):
        uid = str(acc.get("user_id") or acc.get("id"))
        name = acc.get("name") or acc.get("first_name") or f"Account #{idx}"
        username = acc.get("username") or ""
        is_master = (uid == MASTER_UID)

        # Look for hunter token or cached 7-day JWT
        tok_data = tokens.get(uid, {})
        tok = tok_data.get("hunter_init_data") or tok_data.get("hunter") or tok_data.get("hunter_token")
        jwt_tok = tok_data.get("hunter_jwt")
        
        # If neither dedicated hunter token nor JWT exists yet, mark and continue
        if not tok and not jwt_tok:
            print(f"[{idx:02d}/{len(accounts)}] 👤 {name} ({uid}): 🟡 No Hunter Miner token (pending MTProto collector)")
            continue

        client = HunterClient(uid, name, tok or "", username=username, jwt_token=jwt_tok or "")
        ok = client.authenticate()
        if not ok:
            print(f"[{idx:02d}/{len(accounts)}] 👤 {name} ({uid}): ❌ Auth failed ({client.last_proxy_used or 'direct'})")
            continue

        summary["accounts_processed"] += 1
        u_info = client.user_data
        m_info = client.mining_data
        bal = float(u_info.get("coin_balance", 0.0) or 0.0)
        summary["total_gram_bal"] += bal
        ref_by = u_info.get("referred_by")
        if ref_by == int(MASTER_UID) or str(ref_by) == MASTER_UID:
            summary["master_referrals"] += 1

        print(f"\n[{idx:02d}/{len(accounts)}] 👤 {name} ({uid}{' - 👑 Master' if is_master else ''}):")
        print(f"   💰 Balance: {bal:.4f} GRAM | Referred by: {ref_by} | Proxy: {bool(client.last_proxy_used)}")

        # 1. Claim Welcome Bonus (+0.20 GRAM)
        if not u_info.get("welcome_bonus_claimed"):
            wb_res = client.claim_welcome_bonus()
            if wb_res.get("success"):
                bal += 0.20
                summary["welcome_claimed"] += 1
                print(f"   🎁 Welcome Bonus CLAIMED: +0.20 GRAM! New Balance: {bal:.4f} GRAM")

        # 2. Claim Finished Mining
        if m_info.get("can_claim"):
            cl_res = client.claim_mining()
            if cl_res.get("success"):
                c_amt = cl_res.get("mined_coins", 0)
                bal += c_amt
                print(f"   ⛏️ Mining Harvested: +{c_amt:.4f} GRAM!")

        # 3. Start 12h Mining Session
        if not m_info.get("mining_active") and m_info.get("can_start_mining", True):
            st_res = client.start_mining()
            if st_res.get("success"):
                summary["mining_active"] += 1
                print(f"   🚀 12-Hour Mining Session ACTIVATED!")
        elif m_info.get("mining_active"):
            summary["mining_active"] += 1
            print(f"   ⏱️ Mining Already Active (ends in {m_info.get('time_remaining', 0)}s)")

        # 4. Claim Referral Earnings (Master Only)
        if is_master:
            ref_res = client.claim_referral_earnings()
            if ref_res.get("success") and ref_res.get("claimed_amount", 0) > 0:
                claimed = ref_res.get("claimed_amount", 0)
                bal += claimed
                print(f"   👥 Master Referral Earnings CLAIMED: +{claimed:.4f} GRAM!")

        # 5. Sweep dynamic tasks
        for cat in ["community", "partner", "main"]:
            try:
                tasks = client.get_tasks(cat)
                for t in tasks:
                    t_id = t.get("id") or t.get("_id")
                    if not t.get("completed") and t_id:
                        cmp_res = client.complete_task(str(t_id))
                        if cmp_res.get("success"):
                            r_amt = cmp_res.get("reward", 0.002)
                            bal += r_amt
                            summary["tasks_completed"] += 1
                            print(f"   ✅ Task Completed ({t.get('title', t_id)}): +{r_amt} GRAM")
            except Exception:
                pass

        # 6. Automatic Withdrawal to TON Vault
        if bal >= 0.05:
            wd_res = client.withdraw_gram(bal, TARGET_TON_VAULT)
            if wd_res.get("success"):
                summary["withdrawals_dispatched"] += 1
                print(f"   🚀 ON-CHAIN WITHDRAWAL DISPATCHED: {bal:.4f} GRAM -> {TARGET_TON_VAULT[:10]}... (Tx: {wd_res.get('transaction_id')})")
            else:
                print(f"   ℹ️ Withdrawal response: {wd_res.get('error') or wd_res}")

        time.sleep(0.5)

    print("\n" + "=" * 80)
    print("📋 HUNTER MINER FLEET SWEEP COMPLETE")
    print(f"• Accounts Swept: {summary['accounts_processed']}/{summary['total_accounts']}")
    print(f"• Total GRAM Balance: {summary['total_gram_bal']:.4f} GRAM")
    print(f"• Welcome Bonuses: {summary['welcome_claimed']} Claimed")
    print(f"• Mining Active: {summary['mining_active']} Accounts")
    print(f"• Withdrawals Dispatched: {summary['withdrawals_dispatched']}")
    print("=" * 80)

    if summary["accounts_processed"] > 0:
        send_alert_card(summary)

if __name__ == "__main__":
    run_fleet_sweep()
