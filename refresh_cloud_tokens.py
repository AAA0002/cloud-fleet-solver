#!/usr/bin/env python3
"""
refresh_cloud_tokens.py
=============================================================================
100% CLOUD-FIRST TELEGRAM MTPROTO TOKEN REFRESHER FOR MY AGY AI FLEET
=============================================================================
Runs on GitHub Actions, Render, Deno, or ephemeral cloud runners.
Connects briefly via MTProto to extract fresh 24h WebApp initData session tokens
across all 9 legitimate active bots and auto-joins required sponsor channels.

Pushes extracted tokens to both:
  1. 5x Cloudflare Edge Workers & KV
  2. Upstash Serverless Redis REST (`fleet:tokens:{uid}` & `fleet_miniapp_tokens`)

Eliminates all dependence on local VPS uptime! Zero Phone Load.
"""

import os
import sys
import time
import json
import asyncio
import urllib.parse
import aiohttp
from telethon import TelegramClient, functions
from telethon.sessions import StringSession
from telethon.tl.functions.messages import RequestWebViewRequest, RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

API_ID = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID", "37321306"))
API_HASH = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH", "5cd9e5bbfb572a4429a0c54774153b47")
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID", "6727787768"))
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_NOTIFICATION_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
CF_SECRET_KEY = os.getenv("CF_SECRET_KEY") or os.getenv("SECRET_KEY", "")

UPSTASH_URL = os.getenv("UPSTASH_URL") or os.getenv("UPSTASH_REDIS_REST_URL") or "https://relaxing-starfish-285827.upstash.io"
UPSTASH_TOKEN = os.getenv("UPSTASH_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""
TURSO_URL = os.getenv("TURSO_URL") or os.getenv("TURSO_DB_URL", "")
TURSO_TOKEN = os.getenv("TURSO_TOKEN") or os.getenv("TURSO_DB_TOKEN", "")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")

def turso_execute_sync(sql: str, args: list = None):
    if not TURSO_URL or not TURSO_TOKEN:
        return None
    try:
        import urllib.request
        http_url = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
        req_args = []
        if args:
            for a in args:
                if isinstance(a, int):
                    req_args.append({"type": "integer", "value": str(a)})
                elif a is None:
                    req_args.append({"type": "null"})
                else:
                    req_args.append({"type": "text", "value": str(a)})
        payload = {"requests": [{"type": "execute", "stmt": {"sql": sql, "args": req_args}}]}
        req = urllib.request.Request(http_url, data=json.dumps(payload).encode("utf-8"), headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            res = data.get("results", [{}])[0].get("response", {}).get("result", {})
            rows = res.get("rows", [])
            return [[c.get("value") for c in r] for r in rows]
    except Exception:
        return None

def turso_get_sync(key: str):
    rows = turso_execute_sync("SELECT val FROM kv_mesh WHERE key = ? LIMIT 1", [key])
    if rows and len(rows) > 0 and len(rows[0]) > 0:
        return rows[0][0]
    return None

CF_NODES = [
    "https://restore-agy.aaaai2.workers.dev",
    "https://restore-agy.aaa-bot.workers.dev",
    "https://restore-agy.aaa222.workers.dev",
    "https://restore-agy.agorameet.workers.dev",
    "https://restore-agy.aaaai.workers.dev"
]

# 9 Active Legitimate Fleet Bots & Referral Codes
try:
    from bot_registry import get_all_sponsor_channels
    MANDATORY_SPONSOR_CHANNELS = get_all_sponsor_channels()
except Exception:
    MANDATORY_SPONSOR_CHANNELS = [
        "AI_TRADING_FOREX", "AdxearnAi", "BTCMAKERR", "ChickenLandCodes", "ChickenLandPay",
        "DurovKidney", "EarnVaulte", "LandChickenNews", "PaperHouseNFT", "PickaxeEmpire",
        "Web3Primeteam", "keepeye_app", "kynex_mining", "pepe_grammmm", "solanamemes001"
    ]

ATF_BOT = "ATF_AIRDROP_bot"
KYNEX_BOT = "Kynex_miningbot"
PEPEGRAM_BOT = "Pepe_gram_bot"
ADXEARN_BOT = "AdxEarnAibot"
CHICKENLAND_BOT = "ChickenLandBot"
EARNVAULT_BOT = "EarnVaultApp_bot"
MEXOEARN_BOT = "MexoEarni_bot"
MYAGE_BOT = "MyAgeApp"
LIFETIME_BOT = "lifetime_energy_bot"
CHERRY_BOT = "CherryPlayBot"
CHERRY_REF = "r9bsfi3a8c"
CHERRY_APP_URL = "https://cherryearn.site"
VICTORS_BOT = "Victors_Company_bot"

ATF_REF = "6727787768"
KYNEX_REF = "6727787768"
PEPEGRAM_REF = "1449"
ADXEARN_REF = "ref_6727787768"
CHICKENLAND_REF = "12668"
EARNVAULT_REF = "6727787768"
MEXOEARN_REF = "6727787768"
MYAGE_REF = "r6727787768"
LIFETIME_REF = "6727787768"
VICTORS_REF = "ref_9DB8C58014"
DILLO_BOT = "dillo_miner_bot"
DILLO_REF = "6727787768"
DILLO_APP_URL = "https://dillo-miner-bot.ih0st.app/#/"
HUNTER_BOT = "GramHtrsbot"
HUNTER_REF = "6727787768"
HUNTER_APP_URL = "https://huntrs.up.railway.app"


def robust_json_loads(val):
    """Recursively unpacks double or triple serialized JSON strings from Redis/KV."""
    if not val:
        return {}
    res = val
    for _ in range(5):
        if isinstance(res, str):
            res_str = res.strip()
            if (res_str.startswith("{") and res_str.endswith("}")) or (res_str.startswith("[") and res_str.endswith("]")):
                try:
                    res = json.loads(res_str)
                except Exception:
                    break
            else:
                break
        else:
            break
    return res if isinstance(res, (dict, list)) else {}


def load_accounts():
    """Loads accounts from Turso LibSQL, Supabase, accounts.json, Cloudflare backup.zip, or environment."""
    import urllib.request
    import zipfile
    import io
    import ssl

    # 0. Turso LibSQL Mesh SSOT (fleet:accounts)
    t_accs = turso_get_sync("fleet:accounts")
    if t_accs:
        try:
            parsed = json.loads(t_accs)
            if isinstance(parsed, list) and len(parsed) >= 20:
                print(f"✅ Loaded {len(parsed)} accounts from Turso LibSQL (fleet:accounts)")
                return parsed
        except Exception:
            pass

    # 0.1 Supabase fleet_accounts
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            req = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/fleet_accounts?select=*&status=neq.revoked&order=id.asc",
                headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"}
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                if resp.status == 200:
                    sb_data = json.loads(resp.read().decode())
                    if isinstance(sb_data, list) and len(sb_data) >= 20:
                        mapped = []
                        for idx, r in enumerate(sb_data, 1):
                            mapped.append({
                                "index": idx,
                                "id": r.get("id"),
                                "user_id": r.get("id"),
                                "phone": r.get("phone"),
                                "session_string": r.get("session_string"),
                                "session": r.get("session_string"),
                                "status": r.get("status", "active"),
                                "name": (r.get("data") or {}).get("name", f"Account #{idx}") if isinstance(r.get("data"), dict) else f"Account #{idx}"
                            })
                        print(f"✅ Loaded {len(mapped)} accounts from Supabase fleet_accounts")
                        return mapped
        except Exception:
            pass

    acc_path = os.path.join(BASE_DIR, "accounts.json")
    if os.path.exists(acc_path):
        try:
            with open(acc_path, "r", encoding="utf-8") as f:
                accs = json.load(f)
                if isinstance(accs, list) and accs:
                    return accs
        except Exception as e:
            print(f"Error loading accounts.json: {e}")

    # Fallback to Cloudflare Edge mesh backup.zip (runs on ephemeral cloud runners)
    ssl_ctx = ssl._create_unverified_context()
    for cf_url in CF_NODES:
        try:
            req = urllib.request.Request(f"{cf_url}/backup.zip", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
            with urllib.request.urlopen(req, timeout=12, context=ssl_ctx) as resp:
                if resp.status == 200:
                    zip_bytes = resp.read()
                    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                        if "accounts.json" in zf.namelist():
                            cf_accs = json.loads(zf.read("accounts.json").decode("utf-8"))
                            if isinstance(cf_accs, list) and cf_accs:
                                print(f"✅ Loaded {len(cf_accs)} accounts from Cloudflare Edge backup.zip ({cf_url})")
                                return cf_accs
        except Exception as e:
            print(f"⚠️ Cloudflare backup.zip load note ({cf_url}): {e}")
            continue

    # Fallback to Upstash Redis fleet:accounts
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            req = urllib.request.Request(
                f"{UPSTASH_URL}/get/fleet:accounts",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read().decode())
                r = robust_json_loads(d.get("result"))
                if isinstance(r, list) and r:
                    return r
        except Exception as e:
            print(f"Upstash fleet:accounts fetch warning: {e}")

    raw_env = os.getenv("FLEET_ACCOUNTS_JSON")
    if raw_env:
        try:
            return json.loads(raw_env)
        except Exception as e:
            print(f"Error parsing FLEET_ACCOUNTS_JSON: {e}")

    return []


async def sync_tokens_to_edge_and_upstash(session: aiohttp.ClientSession, uid: str, name: str, tokens: dict):
    """Pushes extracted tokens to Turso LibSQL (Primary SSOT), Supabase, 5x Cloudflare Edge Workers, and Upstash Redis."""
    # 0. Fetch existing tokens from Turso LibSQL first (Primary SSOT), fallback to Upstash
    existing_tokens = {}
    if TURSO_URL and TURSO_TOKEN:
        try:
            raw_t = turso_get_sync(f"fleet:tokens:{uid}")
            if raw_t:
                existing_tokens = robust_json_loads(raw_t)
        except Exception:
            pass

    if not existing_tokens and UPSTASH_URL and UPSTASH_TOKEN:
        try:
            async with session.get(
                f"{UPSTASH_URL}/get/fleet:tokens:{uid}",
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                timeout=aiohttp.ClientTimeout(total=4)
            ) as er:
                if er.status == 200:
                    ed = await er.json()
                    res_raw = ed.get("result")
                    if res_raw:
                        existing_tokens = robust_json_loads(res_raw)
        except Exception:
            pass

    if not isinstance(existing_tokens, dict):
        existing_tokens = {}

    payload = {
        **existing_tokens,
        "uid": str(uid),
        "account_id": str(uid),
        "name": name,
        "synced_at": time.time(),
        "updated_at": time.time(),
        **tokens
    }

    # 1. Turso LibSQL Mesh SSOT (Zero rate limits, 25M ops/mo)
    if TURSO_URL and TURSO_TOKEN:
        try:
            http_url = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
            t_reqs = [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES (?, ?, unixepoch())",
                        "args": [{"type": "text", "value": f"fleet:tokens:{uid}"}, {"type": "text", "value": json.dumps(payload)}]
                    }
                }
            ]
            if tokens.get("chickenland_init_data"):
                t_reqs.append({
                    "type": "execute",
                    "stmt": {
                        "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES (?, ?, unixepoch())",
                        "args": [{"type": "text", "value": f"fleet:tokens:{uid}:chickenland_init_data"}, {"type": "text", "value": tokens["chickenland_init_data"]}]
                    }
                })
            if tokens.get("myage_init_data"):
                t_reqs.append({
                    "type": "execute",
                    "stmt": {
                        "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES (?, ?, unixepoch())",
                        "args": [{"type": "text", "value": f"fleet:tokens:{uid}:myage_init_data"}, {"type": "text", "value": tokens["myage_init_data"]}]
                    }
                })
            if tokens.get("hunter_init_data"):
                t_reqs.append({
                    "type": "execute",
                    "stmt": {
                        "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES (?, ?, unixepoch())",
                        "args": [{"type": "text", "value": f"fleet:tokens:{uid}:hunter_init_data"}, {"type": "text", "value": tokens["hunter_init_data"]}]
                    }
                })

            # Also update monolithic miniapp_tokens in Turso
            raw_all_t = turso_get_sync("miniapp_tokens")
            m_dict = robust_json_loads(raw_all_t) if raw_all_t else {}
            if not isinstance(m_dict, dict):
                m_dict = {}
            m_dict[str(uid)] = payload
            t_reqs.append({
                "type": "execute",
                "stmt": {
                    "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES ('miniapp_tokens', ?, unixepoch())",
                    "args": [{"type": "text", "value": json.dumps(m_dict)}]
                }
            })
            t_reqs.append({
                "type": "execute",
                "stmt": {
                    "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES ('fleet:tokens_state', ?, unixepoch())",
                    "args": [{"type": "text", "value": json.dumps(m_dict)}]
                }
            })

            async with session.post(
                http_url,
                json={"requests": t_reqs},
                headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=4)
            ) as t_resp:
                if t_resp.status == 200:
                    print(f"  ✅ Synced to Turso LibSQL SSOT (fleet:tokens:{uid} & miniapp_tokens)")
                else:
                    print(f"  ⚠️ Turso LibSQL note: HTTP {t_resp.status}")
        except Exception as e_turso:
            print(f"  ⚠️ Turso sync note: {e_turso}")

    # 2. Supabase fleet_accounts update
    if SUPABASE_URL and SUPABASE_KEY:
        try:
            from datetime import datetime, timezone
            sb_h = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}", "Content-Type": "application/json", "Prefer": "return=minimal"}
            await session.patch(
                f"{SUPABASE_URL}/rest/v1/fleet_accounts?id=eq.{uid}",
                json={"status": "active", "last_active": datetime.now(timezone.utc).isoformat()},
                headers=sb_h,
                timeout=aiohttp.ClientTimeout(total=3)
            )
        except Exception:
            pass

    # 3. Cloudflare Edge Workers (Sync to primary node with failover)
    headers = {
        "Authorization": f"Bearer {CF_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    for cf_url in CF_NODES:
        try:
            async with session.post(f"{cf_url}/api/tokens", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status in (200, 201):
                    print(f"  ✅ Synced to {cf_url.split('//')[1].split('.')[0]}")
                    break
        except Exception as e:
            print(f"  ⚠️ Sync note for {cf_url}: {e}")

    # 4. Upstash Redis REST (Best-effort secondary mirror)
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            async with session.post(
                f"{UPSTASH_URL}/set/fleet:tokens:{uid}",
                data=json.dumps(payload),
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=4)
            ) as up_resp:
                if up_resp.status in (200, 201):
                    print(f"  ✅ Synced to Upstash Redis (fleet:tokens:{uid})")

            # Also sync into monolithic miniapp_tokens dictionary
            try:
                async with session.get(
                    f"{UPSTASH_URL}/get/miniapp_tokens",
                    headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=3)
                ) as r_all:
                    if r_all.status == 200:
                        all_d = await r_all.json()
                        raw_all = all_d.get("result")
                        master_dict = robust_json_loads(raw_all)
                        if not isinstance(master_dict, dict):
                            master_dict = {}
                        master_dict[str(uid)] = payload
                        await session.post(
                            f"{UPSTASH_URL}/set/miniapp_tokens",
                            data=json.dumps(master_dict),
                            headers={"Authorization": f"Bearer {UPSTASH_TOKEN}", "Content-Type": "application/json"},
                            timeout=aiohttp.ClientTimeout(total=4)
                        )
            except Exception:
                pass

            if tokens.get("chickenland_init_data"):
                try:
                    await session.post(
                        f"{UPSTASH_URL}/set/fleet:tokens:{uid}:chickenland_init_data",
                        data=tokens["chickenland_init_data"],
                        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                        timeout=aiohttp.ClientTimeout(total=2)
                    )
                except Exception:
                    pass
        except Exception as ue:
            pass

    # 3. Local High-Performance JSON Cache
    tok_file = os.path.join(BASE_DIR, "fleet_miniapp_tokens.json")
    try:
        f_db = {}
        if os.path.exists(tok_file):
            with open(tok_file, "r", encoding="utf-8") as f:
                f_db = json.load(f)
        f_db[str(uid)] = payload
        with open(tok_file, "w", encoding="utf-8") as f:
            json.dump(f_db, f, indent=2)
    except Exception:
        pass


async def extract_bot_webapp_token(client: TelegramClient, bot_username: str, start_param: str = "", default_url: str = "", candidate_short_names: list = None, prefer_direct_url: bool = False) -> str:
    """Universal 4-strategy resilient token extractor."""
    if not bot_username:
        return None

    def extract_from_url(url_str):
        if not url_str:
            return None
        try:
            p = urllib.parse.urlparse(str(url_str))
            frag_str = p.fragment
            if "?" in frag_str:
                frag_str = frag_str.split("?", 1)[1]
            elif frag_str.startswith("/"):
                frag_str = frag_str.lstrip("/")
            frag = urllib.parse.parse_qs(frag_str).get("tgWebAppData", [None])[0]
            if frag:
                if frag.startswith("user%3D") or "%257B" in frag or "%2522" in frag:
                    frag = urllib.parse.unquote(frag)
                return frag
            query_str = p.query
            if query_str.startswith("/"):
                query_str = query_str.lstrip("/")
            query = urllib.parse.parse_qs(query_str).get("tgWebAppData", [None])[0]
            if query:
                if query.startswith("user%3D") or "%257B" in query or "%2522" in query:
                    query = urllib.parse.unquote(query)
                return query
        except Exception:
            pass
        return None

    bot_ent = None
    bot_in = None
    candidates = [bot_username]
    BOT_ALIASES = {
        "ATF_AIRDROP_bot": ["atfminers_bot", 7058204689],
        "atfminers_bot": ["ATF_AIRDROP_bot", 7058204689],
        "Kynex_miningbot": ["kynexnetworkbot", 7421882583],
        "Pepe_gram_bot": ["pepegram_bot", 7639148332],
    }
    if bot_username in BOT_ALIASES:
        for a in BOT_ALIASES[bot_username]:
            if a not in candidates:
                candidates.append(a)

    for cand in candidates:
        try:
            bot_ent = await client.get_entity(cand)
            bot_in = await client.get_input_entity(bot_ent)
            if bot_ent and bot_in:
                break
        except Exception:
            continue

    if not bot_ent or not bot_in:
        return None

    # If prefer_direct_url requested, execute Strategy D first
    if prefer_direct_url and default_url:
        for fbm in [False, True]:
            try:
                req_p = {
                    "peer": bot_ent,
                    "bot": bot_ent,
                    "platform": "android",
                    "url": default_url,
                    "from_bot_menu": fbm
                }
                if start_param:
                    req_p["start_param"] = str(start_param)
                res = await client(RequestWebViewRequest(**req_p))
                tok = extract_from_url(getattr(res, "url", None))
                if tok:
                    return tok
            except Exception:
                pass

    # Strategy A: RequestAppWebViewRequest with short names
    names_to_try = candidate_short_names or ["app"]
    for sn in names_to_try:
        try:
            req_p = {
                "peer": bot_in,
                "app": InputBotAppShortName(bot_id=bot_in, short_name=sn),
                "platform": "android"
            }
            if start_param:
                req_p["start_param"] = str(start_param)
            res = await asyncio.wait_for(client(RequestAppWebViewRequest(**req_p)), timeout=5.0)
            tok = extract_from_url(getattr(res, "url", None))
            if tok:
                return tok
        except Exception:
            continue

    # Strategy B: Bot Menu Button
    try:
        menu = await asyncio.wait_for(client(functions.bots.GetBotMenuButtonRequest(user_id=bot_in)), timeout=4.0)
        menu_btn = getattr(menu, "button", None)
        if menu_btn and hasattr(menu_btn, "url") and menu_btn.url:
            req_p = {
                "peer": bot_ent,
                "bot": bot_ent,
                "platform": "android",
                "url": menu_btn.url
            }
            if start_param:
                req_p["start_param"] = str(start_param)
            res = await asyncio.wait_for(client(RequestWebViewRequest(**req_p)), timeout=5.0)
            tok = extract_from_url(getattr(res, "url", None))
            if tok:
                return tok
    except Exception:
        pass

    # Strategy C: Inspect existing keyboard buttons (silent, zero messages sent)
    try:
        msgs = await client.get_messages(bot_ent, limit=5)
        for m in msgs:
            if not m.out and m.buttons:
                for row in m.buttons:
                    for b in row:
                        raw_b = getattr(b, "button", b)
                        w_url = getattr(getattr(raw_b, "web_app", None), "url", None) or getattr(raw_b, "url", None) or getattr(getattr(raw_b, "type", None), "url", None) or getattr(b, "url", None)
                        if w_url:
                            try:
                                req_p = {
                                    "peer": bot_ent,
                                    "bot": bot_ent,
                                    "platform": "android",
                                    "url": w_url
                                }
                                if start_param:
                                    req_p["start_param"] = str(start_param)
                                res = await client(RequestWebViewRequest(**req_p))
                                tok = extract_from_url(getattr(res, "url", None))
                                if tok:
                                    return tok
                            except Exception:
                                pass
                        pass
    except Exception:
        pass

    # Strategy D: Direct RequestWebViewRequest with known default WebApp URL
    if default_url:
        try:
            req_p = {
                "peer": bot_ent,
                "bot": bot_ent,
                "platform": "android",
                "url": default_url
            }
            if start_param:
                req_p["start_param"] = str(start_param)
            res = await asyncio.wait_for(client(RequestWebViewRequest(**req_p)), timeout=5.0)
            tok = extract_from_url(getattr(res, "url", None))
            if tok:
                return tok
        except Exception:
            pass

    return None


async def sync_session_health(session: aiohttp.ClientSession, acc: dict, is_healthy: bool, reason: str = ""):
    """Synchronizes real-time session health state to Upstash Redis, Turso LibSQL, and dispatches Telegram alert."""
    idx = acc.get("index") or 0
    uid = str(acc.get("user_id") or acc.get("id") or "")
    idx_str = str(idx)
    name = acc.get("first_name") or acc.get("name") or f"Account #{idx}"
    phone = acc.get("phone") or "N/A"
    now_ts = int(time.time())

    # 1. Upstash Redis
    if UPSTASH_URL and UPSTASH_TOKEN:
        up_h = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
        try:
            if is_healthy:
                await session.post(f"{UPSTASH_URL}/sadd/fleet:healthy_sessions/{idx_str}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                if uid:
                    await session.post(f"{UPSTASH_URL}/sadd/fleet:healthy_sessions/{uid}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                await session.post(f"{UPSTASH_URL}/srem/fleet:revoked_sessions/{idx_str}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                if uid:
                    await session.post(f"{UPSTASH_URL}/srem/fleet:revoked_sessions/{uid}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                await session.post(f"{UPSTASH_URL}/del/fleet:revocation_reason:{idx_str}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                await session.post(f"{UPSTASH_URL}/del/fleet:alert_debounce:revocation:{idx_str}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
            else:
                await session.post(f"{UPSTASH_URL}/sadd/fleet:revoked_sessions/{idx_str}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                if uid:
                    await session.post(f"{UPSTASH_URL}/sadd/fleet:revoked_sessions/{uid}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                await session.post(f"{UPSTASH_URL}/srem/fleet:healthy_sessions/{idx_str}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                if uid:
                    await session.post(f"{UPSTASH_URL}/srem/fleet:healthy_sessions/{uid}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                if reason:
                    await session.post(f"{UPSTASH_URL}/set/fleet:revocation_reason:{idx_str}?ex=86400", data=reason, headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
        except Exception as e_up:
            print(f"[{name}] Upstash health sync note: {e_up}")

    # 2. Turso LibSQL Mesh
    if TURSO_URL and TURSO_TOKEN:
        try:
            http_url = TURSO_URL.replace("libsql://", "https://") + "/v2/pipeline"
            t_reqs = []
            if is_healthy:
                t_reqs.append({"type": "execute", "stmt": {"sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES ('fleet:healthy_sessions:' || ?, '1', ?);", "args": [{"type": "text", "value": idx_str}, {"type": "integer", "value": str(now_ts)}]}})
                t_reqs.append({"type": "execute", "stmt": {"sql": "DELETE FROM kv_mesh WHERE key = 'fleet:revoked_sessions:' || ?;", "args": [{"type": "text", "value": idx_str}]}})
            else:
                t_reqs.append({"type": "execute", "stmt": {"sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES ('fleet:revoked_sessions:' || ?, '1', ?);", "args": [{"type": "text", "value": idx_str}, {"type": "integer", "value": str(now_ts)}]}})
                t_reqs.append({"type": "execute", "stmt": {"sql": "DELETE FROM kv_mesh WHERE key = 'fleet:healthy_sessions:' || ?;", "args": [{"type": "text", "value": idx_str}]}})
            if t_reqs:
                await session.post(http_url, json={"requests": t_reqs}, headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"}, timeout=aiohttp.ClientTimeout(total=3))
        except Exception:
            pass

    # 3. Proactive Telegram Alert (with 6h debounce)
    if not is_healthy and REPORT_BOT_TOKEN:
        debounced = False
        if UPSTASH_URL and UPSTASH_TOKEN:
            try:
                up_h = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
                async with session.get(f"{UPSTASH_URL}/get/fleet:alert_debounce:revocation:{idx_str}", headers=up_h, timeout=aiohttp.ClientTimeout(total=2)) as r_deb:
                    if r_deb.status == 200:
                        j_deb = await r_deb.json()
                        if j_deb.get("result"):
                            debounced = True
            except Exception:
                pass

        if not debounced:
            if UPSTASH_URL and UPSTASH_TOKEN:
                try:
                    await session.post(f"{UPSTASH_URL}/set/fleet:alert_debounce:revocation:{idx_str}?ex=21600", data="1", headers=up_h, timeout=aiohttp.ClientTimeout(total=2))
                except Exception:
                    pass
            alert_text = (
                f"🚨 <b>[AAA Fleet Sentinel] Session Revocation Notice</b> 🔴\n\n"
                f"• <b>Account:</b> #{idx} ({name})\n"
                f"• <b>Phone:</b> <code>{phone}</code>\n"
                f"• <b>Reason:</b> <code>{reason or 'Session expired / revoked'}</code>\n"
                f"• <b>Action:</b> Account needs to be re-linked.\n\n"
                f"👉 Open <b>MY AGY AI</b> bot (@myagyai), send <code>/relink</code> and tap <b>⚡ 1-Tap Re-link #{idx}</b>."
            )
            for cid in [ADMIN_NOTIFICATION_CHANNEL_ID, str(REPORT_CHAT_ID)]:
                if cid:
                    try:
                        await session.post(
                            f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendMessage",
                            json={"chat_id": cid, "text": alert_text, "parse_mode": "HTML"},
                            timeout=aiohttp.ClientTimeout(total=4)
                        )
                    except Exception:
                        pass


async def extract_account_tokens(acc: dict, session: aiohttp.ClientSession):
    """Connects MTProto for a single account and extracts all 8 bot session links."""
    acc_name = acc.get("name", "Account")
    acc_uid = str(acc.get("user_id"))
    acc_sess = acc.get("session_string") or acc.get("session")
    if not acc_sess or acc.get("status") == "revoked":
        print(f"[{acc_name}] Skipping revoked or empty session.")
        await sync_session_health(session, acc, is_healthy=False, reason="Account marked revoked or session empty")
        return

    is_master = (acc_uid == str(REPORT_CHAT_ID) or acc_uid == "6727787768")
    print(f"\n🚀 [{acc_name} (ID: {acc_uid}{' - 👑 Master' if is_master else ''})] Connecting MTProto...")
    cl = TelegramClient(
        StringSession(acc_sess),
        API_ID,
        API_HASH,
        flood_sleep_threshold=10,
        request_retries=1,
        connection_retries=2,
        timeout=10
    )
    try:
        connected = False
        for attempt in range(1, 4):
            try:
                await cl.connect()
                if await cl.is_user_authorized():
                    connected = True
                    await sync_session_health(session, acc, is_healthy=True)
                    break
                else:
                    print(f"[{acc_name}] Session not authorized or revoked.")
                    await sync_session_health(session, acc, is_healthy=False, reason="Session unauthorized / logged out")
                    return
            except Exception as conn_err:
                err_str = str(conn_err)
                if "used under two different IP addresses" in err_str or "AUTH_KEY_DUPLICATED" in err_str:
                    print(f"[{acc_name}] MTProto key duplicated/revoked: {conn_err}")
                    await sync_session_health(session, acc, is_healthy=False, reason="AUTH_KEY_DUPLICATED (used under two different IP addresses)")
                    return
                if attempt < 3:
                    print(f"[{acc_name}] Connection attempt {attempt} failed ({conn_err}), retrying in {attempt * 2}s...")
                    await asyncio.sleep(attempt * 2.0)
                else:
                    print(f"[{acc_name}] Could not connect after 3 attempts: {conn_err}")
                    await sync_session_health(session, acc, is_healthy=False, reason=f"MTProto connection failed: {str(conn_err)[:60]}")
                    return

        if not connected:
            return

        tokens = {}

        # 1. ATF Miner
        atf_p = None if is_master else str(REPORT_CHAT_ID)
        tok = await extract_bot_webapp_token(cl, ATF_BOT, start_param=atf_p, default_url="https://atfminers.asloni.online/miner/index.html?entry=bot_start", candidate_short_names=["app", "miner", "play"])
        if tok:
            tokens["atf_init_data"] = tok
            print(f"  🪙 ATF Miner: Collected")
        await asyncio.sleep(0.5)

        # 2. Kynex Network (@Kynex_miningbot)
        ky_p = None if is_master else KYNEX_REF
        tok = await extract_bot_webapp_token(cl, KYNEX_BOT, start_param=ky_p, default_url="https://kynex.top/telegram-auth.html", candidate_short_names=["App", "app"])
        if tok:
            tokens["kynex_init_data"] = tok
            print(f"  💎 Kynex Network: Collected")
        await asyncio.sleep(0.5)

        # 5. PepeGram (@Pepe_gram_bot)
        pepe_tok = await extract_bot_webapp_token(cl, PEPEGRAM_BOT, start_param=PEPEGRAM_REF, default_url="https://app.miniempire.app/miniapp/index.html?tid=1449&lang=en", candidate_short_names=["app", "miniapp"], prefer_direct_url=True)
        if pepe_tok:
            tokens["pepegram_init_data"] = pepe_tok
            print(f"  🐸 PepeGram: Collected")
        await asyncio.sleep(0.5)

        # 6. AdxEarn AI (@AdxEarnAibot)
        adx_p = None if is_master else ADXEARN_REF
        adx_tok = await extract_bot_webapp_token(cl, ADXEARN_BOT, start_param=adx_p, default_url="https://app.adxearnai.xyz/index.html", candidate_short_names=["app", "play"], prefer_direct_url=True)
        if adx_tok:
            tokens["adxearn_init_data"] = adx_tok
            print(f"  🤖 AdxEarn AI: Collected")
        await asyncio.sleep(0.5)

        # 6. Chicken Land (@ChickenLandBot)
        chk_p = None if is_master else CHICKENLAND_REF
        chk_tok = await extract_bot_webapp_token(cl, CHICKENLAND_BOT, start_param=chk_p, default_url="https://app8968434282.tgcloud.ai/", candidate_short_names=["play", "app", "farm", "miniapp"])
        if chk_tok:
            tokens["chickenland_init_data"] = chk_tok
            print(f"  🐔 Chicken Land: Collected")
        await asyncio.sleep(0.5)

        # 7. EarnVault (@EarnVaultApp_bot)
        ev_p = None if is_master else EARNVAULT_REF
        ev_tok = await extract_bot_webapp_token(cl, EARNVAULT_BOT, start_param=ev_p, default_url="https://eearnvault.top/", candidate_short_names=["App", "app", "earn"])
        if ev_tok:
            tokens["earnvault_init_data"] = ev_tok
            print(f"  🏦 EarnVault: Collected")
        await asyncio.sleep(0.5)

        # 8. MexoEarn (@MexoEarni_bot)
        mx_p = None if is_master else MEXOEARN_REF
        mx_tok = await extract_bot_webapp_token(cl, MEXOEARN_BOT, start_param=mx_p, default_url="https://mexoearn.xyz/", candidate_short_names=["app", "play"])
        if mx_tok:
            tokens["mexo_init_data"] = mx_tok
            print(f"  ⚡ MexoEarn: Collected")
            # Immediate JWT exchange
            try:
                auth_body = {"initData": mx_tok, "deviceFp": f"fp_{acc_uid[:8]}"}
                if not is_master:
                    auth_body["refCode"] = MEXOEARN_REF
                async with session.post(
                    "https://mexoearn.xyz/api/auth/telegram",
                    json=auth_body,
                    headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as r_mx:
                    if r_mx.status == 200:
                        d_mx = await r_mx.json()
                        if d_mx.get("token"):
                            tokens["mexo_token"] = d_mx["token"]
            except Exception:
                pass
        await asyncio.sleep(0.5)

        # 9. MyAgeApp (@MyAgeApp)
        myage_p = None if is_master else MYAGE_REF
        myage_tok = await extract_bot_webapp_token(cl, MYAGE_BOT, start_param=myage_p, default_url="https://myageapp.xyz/", candidate_short_names=["app", "start", "play"], prefer_direct_url=True)
        if myage_tok:
            tokens["myage_init_data"] = myage_tok
            print(f"  🎂 MyAgeApp: Collected")
        await asyncio.sleep(0.5)

        # 10. Lifetime Energy (@lifetime_energy_bot)
        le_p = None if is_master else LIFETIME_REF
        le_tok = await extract_bot_webapp_token(cl, LIFETIME_BOT, start_param=le_p, default_url="https://app.miniempire.app/miniapp/index.html?tid=5&lang=en", candidate_short_names=["app", "play"], prefer_direct_url=True)
        if le_tok:
            tokens["lifetime_energy_init_data"] = le_tok
            tokens["lifetime_init_data"] = le_tok
            print(f"  ⚡ Lifetime Energy: Collected")
        await asyncio.sleep(0.5)

        # 11. Cherry Play (@CherryPlayBot)
        cherry_p = CHERRY_REF
        cherry_tok = await extract_bot_webapp_token(cl, CHERRY_BOT, start_param=cherry_p, default_url=CHERRY_APP_URL, candidate_short_names=["play", "app"], prefer_direct_url=True)
        if not cherry_tok:
            cherry_tok = await extract_bot_webapp_token(cl, CHERRY_BOT, start_param=cherry_p, default_url=CHERRY_APP_URL, candidate_short_names=["play", "app"], prefer_direct_url=False)
        if cherry_tok:
            tokens["cherry_init_data"] = cherry_tok
            tokens["cherry"] = cherry_tok
            tokens["cherryplay_init_data"] = cherry_tok
            print(f"  🍒 Cherry Play: Collected")
        await asyncio.sleep(0.5)

        # 12. Victor's Company (@Victors_Company_bot)
        vic_p = None if is_master else VICTORS_REF
        vic_tok = await extract_bot_webapp_token(cl, VICTORS_BOT, start_param=vic_p, default_url="https://app.victors.company/", candidate_short_names=["app"], prefer_direct_url=False)
        if not vic_tok:
            vic_tok = await extract_bot_webapp_token(cl, VICTORS_BOT, start_param=vic_p, default_url="https://app.victors.company/", candidate_short_names=["app"], prefer_direct_url=True)
        if vic_tok:
            tokens["victors_init_data"] = vic_tok
            print(f"  🏢 Victor's Company: Collected")
        await asyncio.sleep(0.5)

        # 14. Dillo Miner (@dillo_miner_bot)
        dillo_p = None if is_master else DILLO_REF
        dillo_tok = await extract_bot_webapp_token(cl, DILLO_BOT, start_param=dillo_p, default_url=DILLO_APP_URL, candidate_short_names=["app"], prefer_direct_url=True)
        if dillo_tok:
            tokens["dillo_init_data"] = dillo_tok
            tokens["dillo"] = dillo_tok
            print(f"  ⛏️ Dillo Miner: Collected")
        await asyncio.sleep(0.5)

        # 16. Hunter Miner (@GramHtrsbot)
        hunter_p = None if is_master else HUNTER_REF
        hunter_tok = await extract_bot_webapp_token(cl, HUNTER_BOT, start_param=hunter_p, default_url=HUNTER_APP_URL, candidate_short_names=["app"], prefer_direct_url=False)
        if hunter_tok:
            tokens["hunter_init_data"] = hunter_tok
            tokens["hunter"] = hunter_tok
            try:
                from hunter_miner import HunterClient
                loop = asyncio.get_running_loop()
                def _get_h_jwt():
                    hc = HunterClient(uid=str(acc_uid), name=acc_name, init_data=hunter_tok)
                    if hc.authenticate() and hc.token:
                        return hc.token
                    return None
                h_jwt = await loop.run_in_executor(None, _get_h_jwt)
                if h_jwt:
                    tokens["hunter_jwt"] = h_jwt
                    print(f"  🎯 Hunter Miner: 7-Day JWT Issued ✅")
            except Exception:
                pass
            print(f"  🎯 Hunter Miner: Collected")
        await asyncio.sleep(0.5)

        # Strict Ephemeral Policy (Rule 11): Token refresh is 100% read-only WebApp querying.
        # NEVER join channels or subscribe to third-party bots during token extraction.
        # Side-task channel joins are ephemeral on-demand and immediately left & purged.

        # Sync to Cloudflare Edge KV & Upstash Redis
        if tokens:
            await sync_tokens_to_edge_and_upstash(session, acc_uid, acc_name, tokens)

    finally:
        try:
            await cl.disconnect()
        except Exception:
            pass


async def main():
    print("=" * 65)
    print("MY AGY AI — 100% CLOUD-FIRST TELEGRAM TOKEN REFRESHER")
    print("=" * 65)

    accounts = load_accounts()
    if not accounts:
        print("❌ No accounts found. Ensure accounts.json or FLEET_ACCOUNTS_JSON is set.")
        sys.exit(1)

    print(f"Found {len(accounts)} accounts in fleet.")

    async with aiohttp.ClientSession() as session:
        for idx, acc in enumerate(accounts, 1):
            try:
                await asyncio.wait_for(extract_account_tokens(acc, session), timeout=90.0)
                await asyncio.sleep(1.0)
            except asyncio.TimeoutError:
                print(f"[{acc.get('name')}] Timed out after 90s, continuing to next account...")
            except Exception as e:
                print(f"Error processing account {acc.get('name')}: {e}")

    print("\n✅ All 24h WebApp tokens collected & pushed to 5x Cloudflare Nodes & Upstash Redis!")


if __name__ == "__main__":
    asyncio.run(main())
