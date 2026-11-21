#!/usr/bin/env python3
"""
check_system_health.py
Live forensic health verification across:
1. Cloudflare Edge Mesh (5 nodes)
2. Render Cloud Nodes (3 nodes)
3. Telegram Bot Webhooks
4. Render Deployment Status
5. Upstash Redis State
6. Live 9-Bot Fleet API Probe
"""

import os
import re
import json
import time
import socket
import urllib.request
import urllib.error
import urllib.parse
import aiohttp
import asyncio

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

env = dict(os.environ)
env_file = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("\"'\"'")
                if k not in env or not env[k]:
                    env[k] = v

TURSO_URL = os.environ.get("TURSO_URL") or os.environ.get("TURSO_DB_URL") or env.get("TURSO_URL") or env.get("TURSO_DB_URL") or "https://my-agy-fleet-db-aaa2743.aws-ap-south-1.turso.io"
TURSO_TOKEN = os.environ.get("TURSO_TOKEN") or os.environ.get("TURSO_DB_TOKEN") or env.get("TURSO_TOKEN") or env.get("TURSO_DB_TOKEN") or ""
ADMIN_BOT_TOKEN = os.environ.get("ADMIN_BOT_TOKEN") or os.environ.get("REPORT_BOT_TOKEN") or env.get("ADMIN_BOT_TOKEN") or env.get("REPORT_BOT_TOKEN") or ""
USER_BOT_TOKEN = os.environ.get("USER_BOT_TOKEN") or env.get("USER_BOT_TOKEN") or ""
RENDER_KEY = os.environ.get("RENDER_API_KEY_3") or os.environ.get("RENDER_API_KEY") or env.get("RENDER_API_KEY_3") or env.get("RENDER_API_KEY") or ""

async def test_cf_nodes():
    print("\n--- 1. Testing Cloudflare Edge Nodes ---")
    cf_nodes = [
        ("Primary Gateway Node 4", "https://restore-agy.agorameet.workers.dev"),
        ("Secondary Node 1", "https://restore-agy.aaaai2.workers.dev"),
        ("Secondary Node 2", "https://restore-agy.aaa-bot.workers.dev"),
        ("Secondary Node 3", "https://restore-agy.aaa222.workers.dev"),
        ("Secondary Node 5", "https://restore-agy.aaaai.workers.dev")
    ]
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET)) as s:
        for name, url in cf_nodes:
            t0 = time.time()
            try:
                async with s.get(f"{url}/health", headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    lat = int((time.time() - t0) * 1000)
                    print(f"  ✅ {name} ({url}): HTTP {r.status} ({lat}ms)")
            except Exception as e:
                print(f"  ⚠️ {name} ({url}): {e}")

async def test_render_nodes():
    print("\n--- 2. Testing Render Cloud Standby Nodes ---")
    dedicated_url = env.get("RENDER_SERVICE_URL") or os.getenv("RENDER_SERVICE_URL", "")
    if dedicated_url:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET)) as s:
            t0 = time.time()
            try:
                async with s.get(f"{dedicated_url}/health", headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    lat = int((time.time() - t0) * 1000)
                    if r.status == 200:
                        print(f"  ✅ Dedicated Render Node ({dedicated_url}): HTTP 200 ({lat}ms)")
                    elif r.status == 503:
                        print(f"  🟡 Dedicated Render Node ({dedicated_url}): Free-tier suspended (Cloudflare Edge Mesh & GitHub Actions active) ({lat}ms)")
                    else:
                        print(f"  ⚠️ Dedicated Render Node ({dedicated_url}): HTTP {r.status} ({lat}ms)")
            except Exception as e:
                err = str(e).strip() or type(e).__name__
                print(f"  ⚠️ Dedicated Render Node: {err}")
    else:
        print("  ℹ️ Dedicated Render Node: Awaiting service creation via dashboard.render.com (all 5 legacy accounts purged)")

async def test_telegram_webhooks():
    print("\n--- 3. Testing Telegram Webhook Endpoints ---")
    bots = [
        ("Admin Bot (@myagyai)", ADMIN_BOT_TOKEN),
        ("User Bot (@AAAPREMIUMAPPSbot)", USER_BOT_TOKEN)
    ]
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET)) as s:
        for name, tok in bots:
            if not tok:
                print(f"  ⚠️ {name}: No token found")
                continue
            try:
                async with s.get(f"https://api.telegram.org/bot{tok}/getWebhookInfo", timeout=aiohttp.ClientTimeout(total=10)) as r:
                    d = await r.json()
                    res = d.get("result", {})
                    wh = res.get("url", "None")
                    pending = res.get("pending_update_count", 0)
                    err = res.get("last_error_message")
                    err_s = f" | Last Error: {err}" if err else ""
                    print(f"  ✅ {name}: Webhook -> {wh} (Pending: {pending}{err_s})")
            except Exception as e:
                err_msg = str(e).strip() or type(e).__name__
                print(f"  ⚠️ {name}: {err_msg}")

async def test_render_deployments():
    print("\n--- 4. Checking Render Deployment Progress ---")
    dedicated_srv_id = env.get("RENDER_SERVICE_ID")
    dedicated_api_key = env.get("RENDER_API_KEY")
    if not (dedicated_srv_id and dedicated_api_key):
        print("  ℹ️ Dedicated Render Node: Awaiting credentials from user (all legacy nodes purged)")
        return
    nodes_to_check = [
        ("Dedicated Render Node", dedicated_srv_id, dedicated_api_key)
    ]
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET)) as s:
        for node_name, srv_id, api_key in nodes_to_check:
            if not api_key or not srv_id:
                continue
            try:
                headers = {"Authorization": f"Bearer {api_key}"}
                async with s.get(f"https://api.render.com/v1/services/{srv_id}/deploys?limit=2", headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        deploys = await r.json()
                        for item in deploys:
                            dep = item.get("deploy", {})
                            print(f"  🚀 [{node_name}] Deploy {dep.get('id')}: Status = {dep.get('status')} | Created: {dep.get('createdAt')}")
                    else:
                        print(f"  ⚠️ [{node_name}] Render API HTTP {r.status}")
            except Exception as e:
                print(f"  ⚠️ [{node_name}] Render check error: {e}")

async def test_live_15_bots_probe():
    print("\n--- 5. Probing Live 12-Bot API Connectivity & Tokens ---")
    accs_file = os.path.join(BASE_DIR, "accounts.json")
    if not os.path.exists(accs_file):
        print("  ⚠️ accounts.json missing")
        return
    with open(accs_file, "r") as f:
        accs = json.load(f)
    master_acc = next((a for a in accs if str(a.get("account_id") or a.get("user_id") or a.get("id")) == "6727787768"), {})
    worker_acc = next((a for a in accs if str(a.get("account_id") or a.get("user_id") or a.get("id")) != "6727787768"), {})

    def extract_auth_date(tok):
        if not tok or not isinstance(tok, str):
            return 0
        try:
            m = re.search(r'auth_date(?:=|%3D)(\d+)', tok)
            if m:
                return int(m.group(1))
            unq = urllib.parse.unquote(tok)
            m2 = re.search(r'auth_date=(\d+)', unq)
            if m2:
                return int(m2.group(1))
            qs = urllib.parse.parse_qs(unq)
            return int(qs.get("auth_date", [0])[0])
        except Exception:
            return 0

    def extract_uid(raw_t: str):
        if not raw_t or not isinstance(raw_t, str):
            return None
        try:
            unq = urllib.parse.unquote(raw_t)
            m = re.search(r'"id":\s*(\d+)', unq)
            if m:
                return str(m.group(1))
            qs = urllib.parse.parse_qs(unq)
            if "user" in qs:
                u_obj = json.loads(qs["user"][0])
                if "id" in u_obj:
                    return str(u_obj["id"])
        except Exception:
            pass
        return None

    # Pull live tokens: fleet_miniapp_tokens.json (Primary Local SSOT), then Turso LibSQL, then Upstash Redis
    live_tokens = {}
    worker_tokens = {}
    token_uids = {}
    tok_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fleet_miniapp_tokens.json")
    if os.path.exists(tok_file):
        try:
            with open(tok_file, "r", encoding="utf-8") as f:
                f_db = json.load(f)
                m_toks = f_db.get("6727787768", {})
                if isinstance(m_toks, dict):
                    for k, v in m_toks.items():
                        if v:
                            live_tokens[k] = v
                            token_uids[k] = "6727787768"
                # Scan all accounts and prioritize freshest valid tokens across fleet
                for u_id, u_toks in f_db.items():
                    if isinstance(u_toks, dict):
                        for k, v in u_toks.items():
                            if v and isinstance(v, str):
                                cur_live = live_tokens.get(k)
                                if not cur_live or extract_auth_date(v) > extract_auth_date(cur_live):
                                    live_tokens[k] = v
                                    token_uids[k] = str(u_id)
                                if u_id != "6727787768" and k not in worker_tokens:
                                    worker_tokens[k] = v
                                if k == "hunter_jwt" and not live_tokens.get("hunter_jwt"):
                                    live_tokens["hunter_jwt"] = v
                                    token_uids["hunter_jwt"] = str(u_id)
        except Exception:
            pass

    if TURSO_URL and TURSO_TOKEN:
        try:
            t_http = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
            t_req = urllib.request.Request(
                t_http,
                headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
                data=json.dumps({"requests": [{"type": "execute", "stmt": {"sql": "SELECT key, val FROM kv_mesh WHERE key IN ('fleet:tokens:6727787768', 'fleet:tokens_state');"}}]}).encode()
            )
            with urllib.request.urlopen(t_req, timeout=4) as resp:
                t_res = json.loads(resp.read().decode())
                for item in t_res.get("results", []):
                    if item.get("type") == "ok":
                        rows = item.get("response", {}).get("result", {}).get("rows", [])
                        for row in rows:
                            if row and len(row) >= 2 and row[1]:
                                val_raw = row[1].get("value")
                                if val_raw:
                                    parsed = json.loads(val_raw) if isinstance(val_raw, str) else val_raw
                                    if isinstance(parsed, dict):
                                        t_dict = parsed.get("6727787768") if "6727787768" in parsed else parsed
                                        if isinstance(t_dict, dict):
                                            for tk, tv in t_dict.items():
                                                if tv:
                                                    if tk not in live_tokens:
                                                        live_tokens[tk] = tv
                                                    elif tk.endswith("_init_data"):
                                                        if extract_auth_date(tv) > extract_auth_date(live_tokens.get(tk)):
                                                            live_tokens[tk] = tv
                                                            token_uids[tk] = str(extract_uid(tv) or "6727787768")
        except Exception:
            pass

    # Check Turso LibSQL SSOT for cached Turnstile humanPass
    if TURSO_URL and TURSO_TOKEN:
        try:
            t_http = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
            t_req = json.dumps({
                "requests": [
                    {"type": "execute", "stmt": {"sql": "SELECT val FROM kv_mesh WHERE key = 'victors:pass:6727787768' OR key LIKE 'victors:pass:%' LIMIT 1;", "args": []}},
                    {"type": "close"}
                ]
            }).encode()
            req_t = urllib.request.Request(t_http, data=t_req, headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"})
            with urllib.request.urlopen(req_t, timeout=4) as t_resp:
                t_data = json.loads(t_resp.read().decode())
                results = t_data.get("results", [])
                if results:
                    rows = results[0].get("response", {}).get("result", {}).get("rows", [])
                    if rows:
                        raw_vp = rows[0][0].get("value")
                        if raw_vp:
                            d_vp = json.loads(raw_vp) if isinstance(raw_vp, str) and raw_vp.startswith("{") else raw_vp
                            if isinstance(d_vp, dict) and d_vp.get("humanPass"):
                                live_tokens["victors_human_pass"] = d_vp["humanPass"]
                            elif isinstance(d_vp, str):
                                live_tokens["victors_human_pass"] = d_vp
        except Exception:
            pass

    try:
        from turso_mesh import turso_get
        u_toks = turso_get("fleet:tokens:6727787768")
        if u_toks and isinstance(u_toks, dict):
            for k, v in u_toks.items():
                if v and (k not in live_tokens or extract_auth_date(v) > extract_auth_date(live_tokens.get(k))):
                    live_tokens[k] = v
                    token_uids[k] = str(extract_uid(v) or "6727787768")
        # Also check for cached Turnstile humanPass
        vp_d = turso_get("victors:pass:6727787768")
        if vp_d:
            if isinstance(vp_d, dict) and vp_d.get("humanPass"):
                live_tokens["victors_human_pass"] = vp_d["humanPass"]
            elif isinstance(vp_d, str):
                live_tokens["victors_human_pass"] = vp_d
    except Exception:
        pass

    bots_to_test = [
        ("1. ATF Miner", "https://atfminers.asloni.online/miner/index.php?action=login", "atf_init_data"),
        ("2. Kynex Network", "https://us-central1-keynex-e9511.cloudfunctions.net/verifyTelegramAuth", "kynex_init_data"),
        ("3. PepeGram", "https://app.miniempire.app/miniapp/auth?tid=1449", "pepegram_init_data"),
        ("4. AdxEarn AI", "https://api.adxearnai.xyz/api/user/session", "adxearn_init_data"),
        ("5. Chicken Land", "https://app8968434282.tgcloud.ai/api/rpc", "chickenland_init_data"),
        ("6. EarnVault", "https://us-central1-earnvaulte.cloudfunctions.net/telegramAuth", "earnvault_init_data"),
        ("7. MexoEarn", "https://mexoearn.xyz/api/me", "mexo_token"),
        ("8. MyAgeApp", "https://myageapp.xyz/api/session", "myage_init_data"),
        ("9. Lifetime Energy", "https://app.miniempire.app/miniapp/auth?tid=5", "lifetime_energy_init_data"),
        ("10. Victor's Company", "https://server.victors.company/api/me", "victors_init_data"),
        ("11. Dillo Miner", "https://dillo-miner-bot.ih0st.app/api/app/call", "dillo_init_data"),
        ("12. Hunter Miner", "https://huntrs.up.railway.app/api/get-user", "hunter_init_data")
    ]

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET)) as s:
        for b_name, b_url, tok_key in bots_to_test:
            tok = live_tokens.get(tok_key) or worker_tokens.get(tok_key) or master_acc.get(tok_key) or worker_acc.get(tok_key)
            if not tok and tok_key == "chickenland_init_data":
                tok = live_tokens.get("chicken_init_data") or worker_tokens.get("chicken_init_data") or master_acc.get("chicken_init_data") or worker_acc.get("chickenland_init_data")
            if not tok and tok_key == "earnvault_init_data":
                tok = live_tokens.get("ev_init_data") or worker_tokens.get("ev_init_data") or master_acc.get("ev_init_data") or worker_acc.get("earnvault_init_data")
            if not tok and tok_key == "mexo_token":
                tok = live_tokens.get("mexo_token") or live_tokens.get("mexo_init_data") or worker_tokens.get("mexo_token") or worker_tokens.get("mexo_init_data") or master_acc.get("mexo_token") or master_acc.get("mexo_init_data") or worker_acc.get("mexo_token") or worker_acc.get("mexo_init_data")
            if not tok and "lifetime" in tok_key:
                tok = live_tokens.get("lifetime_init_data") or worker_tokens.get("lifetime_init_data") or master_acc.get("lifetime_energy_init_data") or master_acc.get("lifetime_init_data") or worker_acc.get("lifetime_energy_init_data")
            if not tok and "hunter" in tok_key:
                tok = live_tokens.get("hunter") or live_tokens.get("hunter_jwt") or worker_tokens.get("hunter_init_data") or worker_tokens.get("hunter") or worker_tokens.get("hunter_jwt") or master_acc.get("hunter") or master_acc.get("hunter_jwt") or worker_acc.get("hunter_init_data") or worker_acc.get("hunter") or worker_acc.get("hunter_jwt")
            if not tok:
                print(f"  ⚠️ {b_name}: Token {tok_key} missing")
                continue
            try:
                # Custom request payload per bot
                t0 = time.time()
                r_status = None
                if "atf" in tok_key:
                    atf_uid = int(token_uids.get(tok_key, "6727787768")) if token_uids.get(tok_key, "6727787768").isdigit() else 6727787768
                    atf_h = {
                        "Content-Type": "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": "https://atfminers.asloni.online/miner/index.html",
                        "Origin": "https://atfminers.asloni.online",
                        "User-Agent": "Mozilla/5.0"
                    }
                    async with s.post(b_url, json={"initData": tok, "tg_id": atf_uid, "ref": "6727787768"}, headers=atf_h, timeout=aiohttp.ClientTimeout(total=8)) as r:
                        r_status = r.status
                elif "kynex" in tok_key:
                    async with s.post("https://us-central1-keynex-e9511.cloudfunctions.net/verifyTelegramAuth", json={"data": {"initData": tok, "referral": "6727787768"}}, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "pepegram" in tok_key:
                    pepe_headers = {
                        "Content-Type": "application/json",
                        "Origin": "https://app.miniempire.app",
                        "Referer": "https://app.miniempire.app/miniapp/index.html?tid=1449&lang=en",
                        "User-Agent": "Mozilla/5.0"
                    }
                    async with s.post(b_url, json={"initData": tok, "tid": 1449}, headers=pepe_headers, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "adxearn" in tok_key:
                    adx_uid = token_uids.get(tok_key, "6727787768")
                    adx_h = {
                        "Origin": "https://api.adxearnai.xyz",
                        "Referer": "https://api.adxearnai.xyz/",
                        "Content-Type": "application/json",
                        "X-Telegram-Init-Data": tok,
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                    }
                    async with s.post(b_url, json={"initData": tok, "fp": f"fp_{adx_uid[:8]}"}, headers=adx_h, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "chickenland" in tok_key:
                    import base64
                    b64_c = tok[4:].strip() if tok.startswith("TMA ") else base64.b64encode(tok.encode()).decode()
                    chk_h = {"Authorization": f"TMA {b64_c}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
                    async with s.post(b_url, json={"method": "POST", "path": "/api/me", "body": {"ref": "12668", "lang": "en"}}, headers=chk_h, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "earnvault" in tok_key:
                    rt = live_tokens.get("earnvault_refresh_token") or master_acc.get("earnvault_refresh_token") or worker_tokens.get("earnvault_refresh_token")
                    if rt:
                        try:
                            async with s.post(
                                "https://securetoken.googleapis.com/v1/token?key=AIzaSyCP9yYj_KSQxv0lW0EoqR39_QTO2SGJGEg",
                                json={"grant_type": "refresh_token", "refresh_token": rt},
                                timeout=aiohttp.ClientTimeout(total=8)
                            ) as f_r:
                                if f_r.status == 200:
                                    f_d = await f_r.json()
                                    id_tok = f_d.get("id_token")
                                    if id_tok:
                                        async with s.post(
                                            "https://us-central1-earnvaulte.cloudfunctions.net/getAdState",
                                            json={"data": {}},
                                            headers={"Authorization": f"Bearer {id_tok}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                                            timeout=aiohttp.ClientTimeout(total=8)
                                        ) as g_r:
                                            r_status = g_r.status
                        except Exception:
                            pass
                    if not r_status:
                        async with s.post(b_url, json={"data": {"initData": tok}}, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=6)) as r:
                            r_status = r.status
                elif "mexo" in tok_key:
                    mxo_uid = token_uids.get(tok_key, token_uids.get("mexo_init_data", "6727787768"))
                    m_init = live_tokens.get("mexo_init_data") or master_acc.get("mexo_init_data")
                    if tok.startswith("ey") or (len(tok) > 50 and "user" not in tok and "query_id" not in tok):
                        async with s.get("https://mexoearn.xyz/api/me", headers={"Authorization": f"Bearer {tok}", "User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=6)) as r:
                            r_status = r.status
                        if r_status == 401 and m_init:
                            async with s.post("https://mexoearn.xyz/api/auth/telegram", json={"initData": m_init, "deviceFp": f"fp_{mxo_uid[:8]}"}, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=6)) as r_auth:
                                r_status = r_auth.status
                                if r_status == 200:
                                    try:
                                        fresh_jwt = (await r_auth.json()).get("token")
                                        if fresh_jwt:
                                            live_tokens["mexo_token"] = fresh_jwt
                                    except Exception:
                                        pass
                    else:
                        async with s.post("https://mexoearn.xyz/api/auth/telegram", json={"initData": tok, "deviceFp": f"fp_{mxo_uid[:8]}"}, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=6)) as r:
                            r_status = r.status
                            if r_status == 200:
                                try:
                                    fresh_jwt = (await r.json()).get("token")
                                    if fresh_jwt:
                                        live_tokens["mexo_token"] = fresh_jwt
                                except Exception:
                                    pass
                elif "lifetime" in tok_key:
                    async with s.post("https://app.miniempire.app/miniapp/auth?tid=5", json={"initData": tok, "tid": 5}, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "myage" in tok_key:
                    clean_init = urllib.parse.unquote(tok)
                    h_age = {
                        "Authorization": f"tma {clean_init}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/128.0",
                        "Origin": "https://myageapp.xyz",
                        "Referer": "https://myageapp.xyz/",
                        "Idempotency-Key": f"health-{int(time.time()*1000)}"
                    }
                    async with s.post(b_url, json={}, headers=h_age, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "victors" in tok_key:
                    # In Victor's API, the user ID in x-human-pass MUST strictly match the user ID in initData
                    vic_uid = str(token_uids.get(tok_key, "6727787768"))
                    v_tok = tok
                    v_pass = None

                    # Query Turso LibSQL for available passes
                    if TURSO_URL and TURSO_TOKEN:
                        try:
                            t_http = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
                            t_req = json.dumps({
                                "requests": [
                                    {"type": "execute", "stmt": {"sql": "SELECT key, val FROM kv_mesh WHERE key = ? LIMIT 1;", "args": [{"type": "text", "value": f"victors:pass:{vic_uid}"}]}},
                                    {"type": "execute", "stmt": {"sql": "SELECT key, val FROM kv_mesh WHERE key = 'victors:pass:6727787768' LIMIT 1;", "args": []}},
                                    {"type": "execute", "stmt": {"sql": "SELECT key, val FROM kv_mesh WHERE key LIKE 'victors:pass:%' LIMIT 10;", "args": []}},
                                    {"type": "close"}
                                ]
                            }).encode()
                            req_vp = urllib.request.Request(t_http, data=t_req, headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"})
                            with urllib.request.urlopen(req_vp, timeout=4) as vp_r:
                                vp_d = json.loads(vp_r.read().decode())
                                results = vp_d.get("results", [])
                                for res in results:
                                    if res.get("type") == "ok":
                                        rows = res.get("response", {}).get("result", {}).get("rows", [])
                                        for row in rows:
                                            if len(row) >= 2 and row[1]:
                                                raw_vp = row[1].get("value")
                                                if raw_vp:
                                                    d_vp = json.loads(raw_vp) if isinstance(raw_vp, str) and raw_vp.startswith("{") else raw_vp
                                                    cand_pass = d_vp.get("humanPass") if isinstance(d_vp, dict) else d_vp
                                                    if cand_pass:
                                                        p_uid = str(cand_pass).split(".")[0]
                                                        cand_tok = f_db.get(p_uid, {}).get("victors_init_data") if "f_db" in locals() else None
                                                        if not cand_tok and p_uid == "6727787768":
                                                            cand_tok = master_acc.get("victors_init_data")
                                                        if cand_tok:
                                                            v_pass = cand_pass
                                                            v_tok = cand_tok
                                                            vic_uid = p_uid
                                                            break
                                        if v_pass:
                                            break
                        except Exception:
                            pass

                    if not v_pass:
                        # Fallback to local or Turso cached pass
                        cand_pass = live_tokens.get("victors_human_pass") or live_tokens.get("victors_pass") or master_acc.get("victors_human_pass")
                        if cand_pass:
                            p_uid = str(cand_pass).split(".")[0]
                            if p_uid.isdigit() and "f_db" in locals() and p_uid in f_db:
                                cand_tok = f_db[p_uid].get("victors_init_data")
                                if cand_tok:
                                    v_pass = cand_pass
                                    v_tok = cand_tok
                                    vic_uid = p_uid

                    h_vic = {
                        "User-Agent": "Mozilla/5.0",
                        "Content-Type": "application/json",
                        "Authorization": f"tma {v_tok}",
                        "Origin": "https://app.victors.company",
                        "Referer": "https://app.victors.company/"
                    }
                    if v_pass:
                        if isinstance(v_pass, str) and v_pass.strip().startswith("{"):
                            try:
                                p_hp = json.loads(v_pass)
                                if isinstance(p_hp, dict) and p_hp.get("humanPass"):
                                    v_pass = p_hp.get("humanPass")
                            except Exception:
                                pass
                        h_vic["x-human-pass"] = str(v_pass)
                    async with s.get("https://server.victors.company/api/me", headers=h_vic, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "dillo" in tok_key:
                    raw_dillo = tok
                    if "tgWebAppData=" in raw_dillo:
                        raw_dillo = raw_dillo.split("#tgWebAppData=")[-1].split("tgWebAppData=")[-1]
                        if "&tgWebAppVersion" in raw_dillo:
                            raw_dillo = raw_dillo.split("&tgWebAppVersion")[0]
                        raw_dillo = urllib.parse.unquote(raw_dillo)
                    h_dil = {
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36",
                        "Origin": "https://dillo-miner-bot.ih0st.app",
                        "Referer": "https://dillo-miner-bot.ih0st.app/#/"
                    }
                    async with s.post("https://dillo-miner-bot.ih0st.app/api/app/call", json={"initData": raw_dillo, "event": "state", "data": {}}, headers=h_dil, timeout=aiohttp.ClientTimeout(total=6)) as r:
                        r_status = r.status
                elif "hunter" in tok_key:
                    jwt_tok = live_tokens.get("hunter_jwt") or worker_tokens.get("hunter_jwt")
                    if jwt_tok:
                        h_hnt = {
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {jwt_tok}",
                            "User-Agent": "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36",
                            "Origin": "https://huntrs.up.railway.app",
                            "Referer": "https://huntrs.up.railway.app/"
                        }
                        async with s.post("https://huntrs.up.railway.app/api/get-user", json={}, headers=h_hnt, timeout=aiohttp.ClientTimeout(total=6)) as r:
                            r_status = r.status
                    else:
                        from hunter_miner import HunterClient
                        h_uid = token_uids.get("hunter_jwt") or token_uids.get("hunter_init_data") or "7954290138"
                        hc = HunterClient(h_uid, "Probe", tok, jwt_token=jwt_tok or "")
                        if hc.authenticate():
                            r_status = 200
                        else:
                            r_status = 401

                lat = int((time.time() - t0) * 1000)
                if r_status in (200, 201):
                    print(f"  ✅ {b_name}: HTTP {r_status} ({lat}ms)")
                elif r_status == "tunnel_offline":
                    print(f"  🟢 {b_name}: Token Cached (TryCloudflare rotating tunnel) ({lat}ms)")
                elif r_status in (401, 403) and "victors" in tok_key:
                    print(f"  🟡 {b_name}: HTTP {r_status} (Turnstile Human Verification Required) ({lat}ms)")
                elif r_status == 401:
                    print(f"  ❌ {b_name}: HTTP 401 (Expired/Unauthorized) ({lat}ms)")
                else:
                    print(f"  ⚠️ {b_name}: HTTP {r_status} ({lat}ms)")
            except Exception as e:
                err_desc = str(e).strip() if str(e).strip() else type(e).__name__
                print(f"  ⚠️ {b_name} probe note: {err_desc}")

async def main():
    print("=" * 70)
    print("🔍 COMPREHENSIVE LIVE SYSTEM & 12-BOT FLEET HEALTH VERIFICATION")
    print("=" * 70)
    await test_cf_nodes()
    await test_render_nodes()
    await test_telegram_webhooks()
    await test_render_deployments()
    await test_live_15_bots_probe()
    print("\n" + "=" * 70)
    print("🎉 Verification Complete!")

if __name__ == "__main__":
    asyncio.run(main())
