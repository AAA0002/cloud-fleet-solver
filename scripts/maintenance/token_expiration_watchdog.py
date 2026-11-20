#!/usr/bin/env python3
"""
scripts/maintenance/token_expiration_watchdog.py
Autonomous Cloud Token Expiration & Self-Healing Watchdog.
Monitors the cryptographic lifespan (24h auth_date) of WebApp tokens across all 23 accounts.
If any token is older than 16 hours (or missing), automatically triggers the cloud
MTProto collection engine on Render Oregon Node to refresh all tokens before they expire.

Complies with:
- Rule 4: Zero Phone Load Directive (100% Cloud execution)
- Rule 9: 24-Hour WebApp Token Lifetime & Autonomous Cloud Refresher
- Rule 5: Zero-Trust Ephemeral Infrastructure
"""

import os
import sys
import time
import json
import argparse
from urllib.parse import parse_qs, unquote, urlencode
import urllib.request
import urllib.error
import zipfile
import io
import ssl
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple

try:
    import requests
except ImportError:
    requests = None

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_TOKEN", "")
TURSO_URL = os.getenv("TURSO_URL") or os.getenv("TURSO_DB_URL", "libsql://aaa-fleet-db-aaa0002.turso.io")
TURSO_TOKEN = os.getenv("TURSO_TOKEN") or os.getenv("TURSO_DB_TOKEN", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_NOTIFICATION_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
DEFAULT_RENDER_URL = os.getenv("STANDBY_NODE_URL", "")

# Maximum allowable token age before triggering cloud auto-heal (hours)
MAX_TOKEN_AGE_HOURS = 16.0


def turso_execute(sql: str, args: list = None):
    """Synchronous Turso LibSQL pipeline execution."""
    if not TURSO_TOKEN:
        return None
    endpoint = TURSO_URL.rstrip("/").replace("libsql://", "https://") + "/v2/pipeline"
    headers = {"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"}
    formatted_args = []
    if args:
        for a in args:
            if isinstance(a, int):
                formatted_args.append({"type": "integer", "value": str(a)})
            else:
                formatted_args.append({"type": "text", "value": str(a)})
    req_body = {
        "requests": [
            {"type": "execute", "stmt": {"sql": sql, "args": formatted_args}}
        ]
    }
    try:
        req = urllib.request.Request(endpoint, data=json.dumps(req_body).encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if results and results[0].get("type") == "ok":
                rows = results[0].get("response", {}).get("result", {}).get("rows", [])
                return [[c.get("value") for c in r] for r in rows]
    except Exception:
        pass
    return None


def turso_get(key: str):
    rows = turso_execute("SELECT val FROM kv_mesh WHERE key = ? LIMIT 1", [key])
    if rows and len(rows) > 0 and len(rows[0]) > 0:
        return rows[0][0]
    return None


def robust_json_loads(val, default=None):
    """Recursively unpack double/triple-serialized JSON strings from Redis."""
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


class SafeResponse:
    def __init__(self, status_code: int, data: Any = None, text: str = ""):
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self):
        if isinstance(self._data, (dict, list)):
            return self._data
        try:
            return json.loads(self.text)
        except Exception:
            return {}


def safe_get(url: str, headers: dict = None, timeout: int = 8) -> SafeResponse:
    if requests is not None:
        try:
            r = requests.get(url, headers=headers or {}, timeout=timeout)
            return SafeResponse(r.status_code, None, r.text)
        except Exception as e:
            return SafeResponse(0, {}, str(e))
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return SafeResponse(resp.status, None, raw)
    except urllib.error.HTTPError as he:
        err = he.read().decode("utf-8") if he.fp else str(he)
        return SafeResponse(he.code, None, err)
    except Exception as e:
        return SafeResponse(0, {}, str(e))


def safe_post(url: str, headers: dict = None, json_data: dict = None, form_data: dict = None, timeout: int = 15) -> SafeResponse:
    if requests is not None:
        try:
            if json_data is not None:
                r = requests.post(url, headers=headers or {}, json=json_data, timeout=timeout)
            elif form_data is not None:
                r = requests.post(url, headers=headers or {}, data=form_data, timeout=timeout)
            else:
                r = requests.post(url, headers=headers or {}, timeout=timeout)
            return SafeResponse(r.status_code, None, r.text)
        except Exception as e:
            return SafeResponse(0, {}, str(e))
    h = headers.copy() if headers else {}
    body = None
    if json_data is not None:
        body = json.dumps(json_data).encode("utf-8")
        h["Content-Type"] = "application/json"
    elif form_data is not None:
        body = urlencode(form_data).encode("utf-8")
        h["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return SafeResponse(resp.status, None, raw)
    except urllib.error.HTTPError as he:
        err = he.read().decode("utf-8") if he.fp else str(he)
        return SafeResponse(he.code, None, err)
    except Exception as e:
        return SafeResponse(0, {}, str(e))


def get_active_render_node() -> str:
    """Retrieve the primary Render node URL from Upstash Redis or use default."""
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            url = f"{UPSTASH_URL}/get/fleet:render_primary_url"
            r = safe_get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=5)
            if r.status_code == 200:
                dyn = r.json().get("result")
                if dyn and isinstance(dyn, str) and dyn.startswith("http"):
                    return dyn.rstrip("/")
        except Exception:
            pass
    return DEFAULT_RENDER_URL


def load_accounts() -> List[Dict[str, Any]]:
    """Load accounts from local accounts.json, Turso LibSQL SSOT, Cloudflare backup.zip, or Upstash Redis."""
    acc_path = os.path.join(BASE_DIR, "accounts.json")
    if os.path.exists(acc_path):
        try:
            with open(acc_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Cloud fallback 1: Turso LibSQL SSOT (25M ops/mo, 100% free)
    try:
        t_accs = turso_get("fleet:accounts")
        if t_accs:
            parsed = robust_json_loads(t_accs)
            if isinstance(parsed, list) and parsed:
                return parsed
    except Exception:
        pass

    # Cloud fallback 1: Cloudflare Edge mesh backup.zip (runs on ephemeral cloud runners)
    ssl_ctx = ssl._create_unverified_context()
    for cf_url in [
        "https://restore-agy.aaaai2.workers.dev",
        "https://restore-agy.aaa-bot.workers.dev",
        "https://restore-agy.aaa222.workers.dev",
        "https://restore-agy.aaaai.workers.dev",
        "https://restore-agy.agorameet.workers.dev"
    ]:
        try:
            req = urllib.request.Request(f"{cf_url}/backup.zip", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10, context=ssl_ctx) as resp:
                if resp.status == 200:
                    zip_bytes = resp.read()
                    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                        if "accounts.json" in zf.namelist():
                            cf_accs = json.loads(zf.read("accounts.json").decode("utf-8"))
                            if isinstance(cf_accs, list) and cf_accs:
                                return cf_accs
        except Exception:
            continue

    # Cloud fallback 2: Fetch from fleet:wallets_vault:* in Upstash Redis
    u_url = UPSTASH_URL.rstrip("/")
    if u_url and UPSTASH_TOKEN:
        try:
            url = f"{u_url}/keys/fleet:wallets_vault:*"
            r = safe_get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=8)
            if r.status_code == 200:
                keys = r.json().get("result", [])
                uids = sorted(list(set([k.split(":")[-1] for k in keys if k.split(":")[-1].isdigit() and len(k.split(":")[-1]) >= 9])))
                if uids:
                    return [{"user_id": uid, "name": f"Account {uid}"} for uid in uids]
        except Exception as e:
            print(f"⚠️ Upstash wallets_vault query note: {e}")

        # Cloud fallback 3: Fetch from fleet:tokens:* in Upstash Redis
        try:
            url = f"{u_url}/keys/fleet:tokens:*"
            r = safe_get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=8)
            if r.status_code == 200:
                keys = r.json().get("result", [])
                uids = sorted(list(set([k.split(":")[-1] for k in keys if k.split(":")[-1].isdigit() and len(k.split(":")[-1]) >= 9])))
                if uids:
                    return [{"user_id": uid, "name": f"Account {uid}"} for uid in uids]
        except Exception as e:
            print(f"⚠️ Upstash fleet:tokens query note: {e}")

    # Cloud fallback 4: Autonomous static SSOT of the 23 verified fleet accounts
    FLEET_23_UIDS = [
        "6727787768", "7487048946", "7648254021", "7734849205", "7749125802",
        "7954290138", "8025472383", "8190649727", "8203342513", "8450010161",
        "8609131528", "8669217201", "8727040932", "8741547543", "8782429452",
        "8826375659", "8841038141", "8851426148", "8937908003", "8972499908",
        "8975442879", "9106746905", "9272631973"
    ]
    return [{"user_id": uid, "name": f"Account {uid}"} for uid in FLEET_23_UIDS]


def inspect_account_tokens(uid: str) -> Dict[str, Any]:
    """Retrieve and inspect token age for a specific account UID with multi-cloud failover."""
    data = None

    # 1. Primary: Upstash Redis (if available and within quota)
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            url = f"{UPSTASH_URL}/get/fleet:tokens:{uid}"
            r = safe_get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=5)
            if r.status_code == 200 and r.json().get("result"):
                raw = r.json().get("result")
                parsed = robust_json_loads(raw)
                if isinstance(parsed, dict) and parsed:
                    data = parsed
        except Exception:
            pass

    # 2. Secondary: Turso LibSQL SSOT (25M ops/mo, 100% free, zero quota limits)
    if not data or not isinstance(data, dict):
        try:
            t_raw = turso_get(f"fleet:tokens:{uid}")
            if t_raw:
                parsed = robust_json_loads(t_raw)
                if isinstance(parsed, dict) and parsed:
                    data = parsed
        except Exception:
            pass

    # 3. Tertiary: Local fleet_miniapp_tokens.json fallback
    if not data or not isinstance(data, dict):
        fpath = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    local_tokens = json.load(f)
                    data = local_tokens.get(str(uid)) or local_tokens.get(int(uid))
            except Exception:
                pass

    if not data or not isinstance(data, dict):
        return {"status": "MISSING_KEY", "age_hours": 999.0}

    try:
        now = time.time()
        synced_at = data.get("synced_at") or data.get("updated_at") or 0

        # Extract auth_date from primary tokens
        sample_tokens = [
            data.get("atf_init_data"),
            data.get("victors_init_data"),
            data.get("kynex_init_data"),
            data.get("chickenland_init_data"),
            data.get("cherry_init_data"),
            data.get("dillo_init_data"),
            data.get("hunter_init_data")
        ]

        auth_dates = []
        for tok in sample_tokens:
            if tok and isinstance(tok, str) and "auth_date=" in tok:
                qs = parse_qs(tok)
                ad_list = qs.get("auth_date", [])
                if ad_list:
                    try:
                        ad = int(ad_list[0])
                        if ad > 0:
                            auth_dates.append(ad)
                    except ValueError:
                        pass

        if auth_dates:
            newest_auth = max(auth_dates)
            age_hours = round((now - newest_auth) / 3600.0, 2)
            remaining_hours = max(0.0, round(24.0 - age_hours, 2))
        elif synced_at > 0:
            age_hours = round((now - synced_at) / 3600.0, 2)
            remaining_hours = max(0.0, round(24.0 - age_hours, 2))
        else:
            age_hours = 999.0
            remaining_hours = 0.0

        if age_hours <= 16.0:
            status = "FRESH"
        elif age_hours <= 20.0:
            status = "EXPIRING_SOON"
        else:
            status = "CRITICAL_EXPIRED"

        return {
            "status": status,
            "age_hours": age_hours,
            "remaining_hours": remaining_hours,
            "has_atf": bool(data.get("atf_init_data")),
            "has_victors": bool(data.get("victors_init_data")),
            "has_cherry": bool(data.get("cherry_init_data")),
            "has_dillo": bool(data.get("dillo_init_data")),
            "has_hunter": bool(data.get("hunter_init_data")),
            "name": data.get("name", "Unknown")
        }
    except Exception as e:
        return {"status": f"ERROR: {e}", "age_hours": 999.0, "remaining_hours": 0.0}


def trigger_cloud_token_refresh(render_node: str) -> bool:
    """Trigger the autonomous token collection engine (100% Free Cloud Native Architecture)."""
    print("🚀 Initiating Cloud Token Refresh (100% Free GitHub & Cloudflare Architecture)...")

    # 1. Native Cloud Runner Execution (Direct on GitHub Actions or Cloud Runner VM with Zero Phone Overhead)
    refresher_path = os.path.join(BASE_DIR, "refresh_cloud_tokens.py")
    is_cloud_runner = os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("ALLOW_LOCAL_MTPROTO") == "1"
    if is_cloud_runner and os.path.exists(refresher_path):
        print(f"⚡ Running native cloud token refresher: {refresher_path}...")
        try:
            import subprocess
            proc = subprocess.run([sys.executable, refresher_path], timeout=600)
            if proc.returncode == 0:
                print("✅ Native cloud token collection completed successfully.")
                return True
        except Exception as pe:
            print(f"⚠️ Native token refresh note: {pe}")

    # 2. GitHub Actions Cloud Workflow Trigger (Unlimited Free Compute & Zero Bandwidth Limits)
    try:
        import subprocess
        cmd = ["gh", "workflow", "run", "proactive_token_refresher.yml", "--repo", "AAA0002/cloud-fleet-ops"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode != 0:
            cmd = ["gh", "workflow", "run", "proactive_token_refresher.yml", "--repo", "AAA0002/cloud-fleet-solver"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            print("✅ Successfully dispatched GitHub Actions token refresher (unlimited free compute).")
            return True
    except Exception as ge:
        print(f"⚠️ GitHub Actions workflow dispatch note: {ge}")

    # 3. Cloudflare Worker Edge Trigger Fallback
    try:
        cf_url = "https://restore-agy.agorameet.workers.dev/api/tokens/refresh"
        headers = {
            "Authorization": f"Bearer {SECRET_KEY}",
            "Content-Type": "application/json"
        }
        r_cf = safe_post(cf_url, headers=headers, timeout=15)
        if r_cf.status_code == 200:
            print("✅ Triggered token collection via Cloudflare Edge failover.")
            return True
    except Exception as cfe:
        print(f"⚠️ Cloudflare fallback note: {cfe}")

    # 4. Optional Render Fallback (if active & not suspended)
    if render_node and not render_node.endswith(".onrender.com"):
        try:
            url = f"{render_node}/collect-tokens"
            headers = {"Authorization": f"Bearer {SECRET_KEY}", "Content-Type": "application/json"}
            r = safe_post(url, headers=headers, json_data={"reason": "watchdog_auto_heal"}, timeout=30)
            if r.status_code == 200:
                print("✅ Cloud token collection accepted by secondary standby node.")
                return True
        except Exception:
            pass

    return False


def send_watchdog_alert(
    total_audited: int,
    fresh_count: int,
    expiring_count: int,
    critical_count: int,
    avg_age: float,
    healed: bool,
    target_node: str
):
    """Dispatches a clean operational card to the Admin Notification Channel (-1004358687850)."""
    if not REPORT_BOT_TOKEN or not ADMIN_NOTIFICATION_CHANNEL_ID:
        print("ℹ️ Telegram alerting skipped (REPORT_BOT_TOKEN not configured).")
        return

    now_utc = datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")

    if critical_count > 0 or expiring_count > 0:
        headline = "⚠️ <b>AAA FLEET TOKEN LIFESPAN ALERT</b>"
        action_text = f"🔄 <b>Auto-Healing Triggered:</b> Dispatched collector request to {target_node}" if healed else "❌ Auto-healing request could not be delivered to Render node."
    else:
        headline = "🛡️ <b>AAA FLEET TOKENS HEALTHY & SYNCHRONIZED</b>"
        action_text = "✅ <b>Lifespan Nominal:</b> All tokens actively accumulating inside 24h cryptographic window."

    text = (
        f"{headline}\n\n"
        f"📊 <b>Fleet Coverage:</b> {total_audited} Accounts Audited\n"
        f"• 🟢 <b>Fresh (&lt;16h):</b> {fresh_count}/{total_audited}\n"
        f"• 🟡 <b>Expiring Soon (16–20h):</b> {expiring_count}\n"
        f"• 🔴 <b>Critical / Expired (&gt;20h):</b> {critical_count}\n"
        f"• ⏱️ <b>Avg Lifespan Used:</b> {avg_age:.1f}h / 24.0h ({(24.0 - avg_age):.1f}h remaining)\n\n"
        f"{action_text}\n"
        f"⏱️ <i>Timestamp: {now_utc}</i>"
    )

    url = f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendMessage"
    try:
        data = {
            "chat_id": ADMIN_NOTIFICATION_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        safe_post(url, form_data=data, timeout=10)
        print("✅ Alert card dispatched to Admin Notification Channel.")
    except Exception as e:
        print(f"⚠️ Failed to send alert card: {e}")


def main():
    parser = argparse.ArgumentParser(description="Autonomous Token Expiration Watchdog")
    parser.add_argument("--force-refresh", action="store_true", help="Force trigger token refresh regardless of age")
    parser.add_argument("--no-notify", action="store_true", help="Suppress Telegram alert channel notification")
    parser.add_argument("--check-only", action="store_true", help="Audit token freshness without re-triggering cloud refresher")
    args = parser.parse_args()

    print("🔍 Starting Autonomous Cloud Token Expiration Audit...")
    accounts = load_accounts()
    if not accounts:
        print("⚠️ No accounts found locally; fetching from Upstash Redis...")
        try:
            url = f"{UPSTASH_URL}/keys/fleet:tokens:*"
            r = safe_get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=8)
            keys = [k for k in r.json().get("result", []) if k.count(":") == 2]
            accounts = [{"user_id": k.split(":")[-1], "name": f"Account {k.split(':')[-1]}"} for k in keys]
        except Exception:
            pass

    if not accounts:
        print("❌ Error: No fleet accounts discovered. Check UPSTASH_URL / accounts.json.")
        sys.exit(1)

    print(f"📋 Auditing {len(accounts)} accounts across Upstash Redis...")

    fresh = 0
    expiring = 0
    critical = 0
    total_age = 0.0
    valid_ages = 0

    expiring_uids = []

    for acc in accounts:
        uid = str(acc.get("user_id") or acc.get("account_id") or "")
        if not uid or not uid.isdigit() or len(uid) < 9:
            continue

        res = inspect_account_tokens(uid)
        st = res.get("status")
        age = res.get("age_hours", 999.0)

        if age < 900.0:
            total_age += age
            valid_ages += 1

        if st == "FRESH":
            fresh += 1
        elif st == "EXPIRING_SOON":
            expiring += 1
            expiring_uids.append(uid)
        else:
            critical += 1
            expiring_uids.append(uid)

    total_audited = fresh + expiring + critical
    avg_age = round(total_age / valid_ages, 2) if valid_ages > 0 else 0.0

    print(f"\n📊 Audit Results across {total_audited} Accounts:")
    print(f"• 🟢 Fresh (<16h old): {fresh}")
    print(f"• 🟡 Expiring Soon (16–20h): {expiring}")
    print(f"• 🔴 Critical/Expired (>20h): {critical}")
    print(f"• ⏱️ Average Token Age: {avg_age} hours (Remaining lifespan: {max(0.0, 24.0 - avg_age):.2f} hours)")

    render_node = get_active_render_node()
    healed = False

    needs_refresh = (critical > 0) or (expiring >= 3) or (avg_age >= MAX_TOKEN_AGE_HOURS) or args.force_refresh

    if needs_refresh:
        if args.check_only:
            print(f"\n⚠️ Token audit note: {critical} critical, {expiring} expiring (check-only mode; auto-refresh skipped).")
        else:
            print(f"\n🚨 Token expiration threshold exceeded ({critical} critical, {expiring} expiring).")
            healed = trigger_cloud_token_refresh(render_node)
    else:
        print("\n🎉 All tokens are within safe operating lifespan. No cloud refresh required.")

    if not args.no_notify:
        send_watchdog_alert(
            total_audited=total_audited,
            fresh_count=fresh,
            expiring_count=expiring,
            critical_count=critical,
            avg_age=avg_age,
            healed=healed,
            target_node=render_node
        )

    sys.exit(0)


if __name__ == "__main__":
    main()
