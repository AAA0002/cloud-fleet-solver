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
import requests
from urllib.parse import parse_qs, unquote
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

UPSTASH_URL = os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_URL", "")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_TOKEN", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_NOTIFICATION_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
DEFAULT_RENDER_URL = "https://my-agy-fleet-standby-060h.onrender.com"

# Maximum allowable token age before triggering cloud auto-heal (hours)
MAX_TOKEN_AGE_HOURS = 16.0


def get_active_render_node() -> str:
    """Retrieve the primary Render node URL from Upstash Redis or use default."""
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            url = f"{UPSTASH_URL}/get/fleet:render_primary_url"
            r = requests.get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=5)
            if r.status_code == 200:
                dyn = r.json().get("result")
                if dyn and isinstance(dyn, str) and dyn.startswith("http"):
                    return dyn.rstrip("/")
        except Exception:
            pass
    return DEFAULT_RENDER_URL


def load_accounts() -> List[Dict[str, Any]]:
    """Load accounts from local accounts.json or cloud fleet:wallets_vault:* cache."""
    acc_path = os.path.join(BASE_DIR, "accounts.json")
    if os.path.exists(acc_path):
        try:
            with open(acc_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Cloud fallback: Fetch exactly the 23 legitimate accounts from fleet:wallets_vault:*
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            url = f"{UPSTASH_URL}/keys/fleet:wallets_vault:*"
            r = requests.get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=8)
            if r.status_code == 200:
                keys = r.json().get("result", [])
                uids = sorted(list(set([k.split(":")[-1] for k in keys if k.split(":")[-1].isdigit() and len(k.split(":")[-1]) >= 9])))
                accounts = []
                for uid in uids:
                    accounts.append({"user_id": uid, "name": f"Account {uid}"})
                return accounts
        except Exception:
            pass

    return []


def inspect_account_tokens(uid: str) -> Dict[str, Any]:
    """Retrieve and inspect token age for a specific account UID."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return {"status": "ERROR_NO_REDIS", "age_hours": 999.0}

    try:
        url = f"{UPSTASH_URL}/get/fleet:tokens:{uid}"
        r = requests.get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=8)
        if r.status_code != 200 or not r.json().get("result"):
            return {"status": "MISSING_KEY", "age_hours": 999.0}

        raw = r.json().get("result")
        data = json.loads(raw) if isinstance(raw, str) else raw

        now = time.time()
        synced_at = data.get("synced_at") or data.get("updated_at") or 0

        # Extract auth_date from primary tokens
        sample_tokens = [
            data.get("atf_init_data"),
            data.get("victors_init_data"),
            data.get("kynex_init_data"),
            data.get("chickenland_init_data"),
            data.get("jvault_init_data")
        ]

        min_auth_date = 0
        for tok in sample_tokens:
            if tok and isinstance(tok, str) and "auth_date=" in tok:
                qs = parse_qs(tok)
                ad_list = qs.get("auth_date", [])
                if ad_list:
                    try:
                        ad = int(ad_list[0])
                        if min_auth_date == 0 or ad < min_auth_date:
                            min_auth_date = ad
                    except ValueError:
                        pass

        if min_auth_date > 0:
            age_hours = round((now - min_auth_date) / 3600.0, 2)
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
            "has_jvault": bool(data.get("jvault_init_data")),
            "has_tavora": bool(data.get("tavora_init_data")),
            "name": data.get("name", "Unknown")
        }
    except Exception as e:
        return {"status": f"ERROR: {e}", "age_hours": 999.0, "remaining_hours": 0.0}


def trigger_cloud_token_refresh(render_node: str) -> bool:
    """Trigger the autonomous token collection engine on the cloud Render Node."""
    print(f"🚀 Triggering Cloud Token Refresher on {render_node}/collect-tokens...")
    url = f"{render_node}/collect-tokens"
    headers = {
        "Authorization": f"Bearer {SECRET_KEY}",
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, headers=headers, json={"reason": "watchdog_auto_heal"}, timeout=45)
        if r.status_code == 200:
            print("✅ Cloud token collection request accepted by Render node.")
            return True
        else:
            print(f"⚠️ Render response {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ Error contacting Render node: {e}")

    # Fallback to Cloudflare Worker trigger endpoint
    try:
        cf_url = "https://restore-agy.agorameet.workers.dev/api/tokens/refresh"
        r_cf = requests.post(cf_url, headers=headers, timeout=15)
        if r_cf.status_code == 200:
            print("✅ Triggered token collection via Cloudflare Edge failover.")
            return True
    except Exception as cfe:
        print(f"⚠️ Cloudflare fallback failed: {cfe}")

    return False


def send_watchdog_alert(
    total_audited: int,
    fresh_count: int,
    expiring_count: int,
    critical_count: int,
    auto_heal_triggered: bool,
    avg_age: float
) -> None:
    """Dispatch concise operational status card to Admin Notification Channel (-1004358687850)."""
    if not REPORT_BOT_TOKEN or not ADMIN_NOTIFICATION_CHANNEL_ID:
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    heal_status = "🚀 Auto-Heal Triggered (Cloud Collection Active)" if auto_heal_triggered else "✅ Healthy (No Refresh Needed)"
    health_icon = "🟢" if critical_count == 0 and expiring_count == 0 else ("🟡" if critical_count == 0 else "🔴")

    text = (
        f"🔄 <b>Autonomous Cloud Token Sentinel</b>\n"
        f"📅 <i>{ts}</i>\n\n"
        f"• <b>Fleet Health:</b> {health_icon} <code>{fresh_count}/{total_audited} Fresh (&lt;16h)</code>\n"
        f"• <b>Average Token Age:</b> <code>{avg_age:.1f} hours</code> (Remaining: <code>{max(0.0, 24.0 - avg_age):.1f}h</code>)\n"
        f"• <b>Expiring Soon (&gt;16h):</b> <code>{expiring_count} Accounts</code>\n"
        f"• <b>Critical / Expired:</b> <code>{critical_count} Accounts</code>\n"
        f"• <b>Action:</b> <code>{heal_status}</code>\n\n"
        f"<i>Zero Phone Load Directive: 100% Cloud MTProto execution on Render Oregon Node.</i>"
    )

    try:
        url = f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendMessage"
        data = {
            "chat_id": ADMIN_NOTIFICATION_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        requests.post(url, data=data, timeout=10)
        print("✅ Alert card dispatched to Admin Notification Channel.")
    except Exception as e:
        print(f"⚠️ Failed to send alert card: {e}")


def run_watchdog(force_refresh: bool = False, notify: bool = True) -> Dict[str, Any]:
    """Execute complete token expiration audit and self-healing loop."""
    print("🔍 Starting Autonomous Cloud Token Expiration Audit...")
    accounts = load_accounts()
    if not accounts:
        print("⚠️ No accounts found locally; fetching from Upstash Redis...")
        try:
            url = f"{UPSTASH_URL}/keys/fleet:tokens:*"
            r = requests.get(url, headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}, timeout=8)
            keys = [k for k in r.json().get("result", []) if k.count(":") == 2]
            accounts = [{"user_id": k.split(":")[-1], "name": f"Account {k.split(':')[-1]}"} for k in keys]
        except Exception:
            pass

    print(f"📋 Auditing {len(accounts)} accounts across Upstash Redis...")

    fresh_count = 0
    expiring_count = 0
    critical_count = 0
    total_ages = []
    account_reports = []

    for acc in accounts:
        uid = str(acc.get("user_id") or acc.get("id") or acc.get("account_id"))
        rep = inspect_account_tokens(uid)
        rep["uid"] = uid
        rep["account_name"] = acc.get("name", f"UID {uid}")
        account_reports.append(rep)

        if rep["status"] == "FRESH":
            fresh_count += 1
            total_ages.append(rep["age_hours"])
        elif rep["status"] == "EXPIRING_SOON":
            expiring_count += 1
            total_ages.append(rep["age_hours"])
        else:
            critical_count += 1
            total_ages.append(rep["age_hours"])

    avg_age = sum(total_ages) / len(total_ages) if total_ages else 0.0

    print(f"\n📊 Audit Results across {len(accounts)} Accounts:")
    print(f"• 🟢 Fresh (<16h old): {fresh_count}")
    print(f"• 🟡 Expiring Soon (16–20h): {expiring_count}")
    print(f"• 🔴 Critical/Expired (>20h): {critical_count}")
    print(f"• ⏱️ Average Token Age: {avg_age:.2f} hours (Remaining lifespan: {max(0.0, 24.0 - avg_age):.2f} hours)\n")

    auto_heal_needed = force_refresh or (expiring_count > 0) or (critical_count > 0)
    auto_heal_triggered = False

    if auto_heal_needed:
        print("⚠️ Token age threshold exceeded or refresh forced! Initiating Autonomous Cloud Self-Heal...")
        render_node = get_active_render_node()
        auto_heal_triggered = trigger_cloud_token_refresh(render_node)
    else:
        print("🎉 All tokens are within safe operating lifespan. No cloud refresh required.")

    if notify:
        send_watchdog_alert(
            total_audited=len(accounts),
            fresh_count=fresh_count,
            expiring_count=expiring_count,
            critical_count=critical_count,
            auto_heal_triggered=auto_heal_triggered,
            avg_age=avg_age
        )

    return {
        "total_audited": len(accounts),
        "fresh_count": fresh_count,
        "expiring_count": expiring_count,
        "critical_count": critical_count,
        "avg_age_hours": avg_age,
        "auto_heal_triggered": auto_heal_triggered,
        "reports": account_reports
    }


def main():
    parser = argparse.ArgumentParser(description="Autonomous Cloud Token Expiration & Self-Healing Watchdog")
    parser.add_argument("--force", action="store_true", help="Force cloud token collection regardless of current age")
    parser.add_argument("--no-notify", action="store_true", help="Do not send Telegram notification card")

    args = parser.parse_args()
    run_watchdog(force_refresh=args.force, notify=not args.no_notify)


if __name__ == "__main__":
    main()
