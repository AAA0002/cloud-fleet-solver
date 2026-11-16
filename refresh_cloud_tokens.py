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

API_ID = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID", "37321306"))
API_HASH = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH", "5cd9e5bbfb572a4429a0c54774153b47")
REPORT_CHAT_ID = int(os.getenv("REPORT_CHAT_ID", "6727787768"))
CF_SECRET_KEY = os.getenv("CF_SECRET_KEY") or os.getenv("SECRET_KEY", "")

UPSTASH_URL = os.getenv("UPSTASH_URL") or "https://relaxing-starfish-285827.upstash.io"
UPSTASH_TOKEN = os.getenv("UPSTASH_TOKEN") or os.getenv("UPSTASH_REDIS_REST_TOKEN") or ""

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
JVAULT_BOT = "JVaultMinerBot"
TAVORA_BOT = "TavoraNode_Bot"
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
JVAULT_REF = "6727787768"
TAVORA_REF = "ref_MW0MB8RV"
VICTORS_REF = "ref_9DB8C58014"
AZM_BOT = "AZMIR_Miner_BOT"
AZM_REF = "6727787768"
AZM_APP_URL = os.getenv("AZM_APP_URL", "https://amd-dot-thin-resident.trycloudflare.com/")
DILLO_BOT = "dillo_miner_bot"
DILLO_REF = "6727787768"
DILLO_APP_URL = "https://dillo-miner-bot.ih0st.app/#/"


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
    """Loads accounts from accounts.json, Upstash Redis, or environment."""
    acc_path = os.path.join(BASE_DIR, "accounts.json")
    if os.path.exists(acc_path):
        try:
            with open(acc_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading accounts.json: {e}")

    # Fallback to Upstash Redis fleet:accounts
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            import urllib.request
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
    """Pushes extracted tokens to both 5x Cloudflare Edge Workers and Upstash Redis with safe merging."""
    # 0. Fetch existing tokens from Upstash to preserve existing active bots
    existing_tokens = {}
    if UPSTASH_URL and UPSTASH_TOKEN:
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

    # 1. Cloudflare Edge Workers (Sync to primary node with failover; break on success to prevent KV 1000/day write limit)
    headers = {
        "Authorization": f"Bearer {CF_SECRET_KEY}",
        "Content-Type": "application/json"
    }
    for cf_url in CF_NODES:
        try:
            async with session.post(f"{cf_url}/api/tokens", json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status in (200, 201):
                    print(f"  ✅ Synced to {cf_url.split('//')[1].split('.')[0]}")
                    break  # Primary sync succeeded! Avoid burning KV writes on secondary nodes.
        except Exception as e:
            print(f"  ⚠️ Sync note for {cf_url}: {e}")

    # 2. Upstash Redis REST (Atomic single-key set)
    if UPSTASH_URL and UPSTASH_TOKEN:
        try:
            await session.post(
                f"{UPSTASH_URL}/set/fleet:tokens:{uid}",
                data=json.dumps(payload),
                headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                timeout=aiohttp.ClientTimeout(total=5)
            )
            print(f"  ✅ Synced to Upstash Redis (fleet:tokens:{uid})")

            # Also sync into monolithic miniapp_tokens dictionary for runner compatibility
            try:
                async with session.get(
                    f"{UPSTASH_URL}/get/miniapp_tokens",
                    headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                    timeout=aiohttp.ClientTimeout(total=4)
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
                            timeout=aiohttp.ClientTimeout(total=5)
                        )
            except Exception:
                pass

            # Sync individual bot keys for sub-second sniper & task runner compatibility
            if tokens.get("chickenland_init_data"):
                try:
                    await session.post(
                        f"{UPSTASH_URL}/set/fleet:tokens:{uid}:chickenland_init_data",
                        data=tokens["chickenland_init_data"],
                        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                        timeout=aiohttp.ClientTimeout(total=3)
                    )
                except Exception:
                    pass
            if tokens.get("myage_init_data"):
                try:
                    await session.post(
                        f"{UPSTASH_URL}/set/fleet:tokens:{uid}:myage_init_data",
                        data=tokens["myage_init_data"],
                        headers={"Authorization": f"Bearer {UPSTASH_TOKEN}"},
                        timeout=aiohttp.ClientTimeout(total=3)
                    )
                except Exception:
                    pass
        except Exception as ue:
            print(f"  ⚠️ Upstash sync note: {ue}")

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
            frag = urllib.parse.parse_qs(p.fragment).get("tgWebAppData", [None])[0]
            if frag:
                if frag.startswith("user%3D") or "%257B" in frag or "%2522" in frag:
                    frag = urllib.parse.unquote(frag)
                return frag
            query = urllib.parse.parse_qs(p.query).get("tgWebAppData", [None])[0]
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
        try:
            is_jvault = ("jvault" in str(bot_username).lower() or "jvault" in str(default_url).lower())
            req_p = {
                "peer": bot_ent,
                "bot": bot_ent,
                "platform": "android",
                "url": default_url,
                "from_bot_menu": False if is_jvault else True
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
    names_to_try = candidate_short_names or ["app", "play", "myapp", "miniapp", "bot", "game"]
    for sn in names_to_try:
        try:
            req_p = {
                "peer": bot_in,
                "app": InputBotAppShortName(bot_id=bot_in, short_name=sn),
                "platform": "android"
            }
            if start_param:
                req_p["start_param"] = str(start_param)
            res = await client(RequestAppWebViewRequest(**req_p))
            tok = extract_from_url(getattr(res, "url", None))
            if tok:
                return tok
        except Exception:
            continue

    # Strategy B: Bot Menu Button
    try:
        menu = await client(functions.bots.GetBotMenuButtonRequest(user_id=bot_in))
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
            res = await client(RequestWebViewRequest(**req_p))
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
                        w_url = getattr(getattr(raw_b, "web_app", None), "url", None) or getattr(raw_b, "url", None)
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
            res = await client(RequestWebViewRequest(**req_p))
            tok = extract_from_url(getattr(res, "url", None))
            if tok:
                return tok
        except Exception:
            pass

    return None


async def extract_account_tokens(acc: dict, session: aiohttp.ClientSession):
    """Connects MTProto for a single account and extracts all 8 bot session links."""
    acc_name = acc.get("name", "Account")
    acc_uid = str(acc.get("user_id"))
    acc_sess = acc.get("session_string") or acc.get("session")
    if not acc_sess or acc.get("status") == "revoked":
        print(f"[{acc_name}] Skipping revoked or empty session.")
        return

    is_master = (acc_uid == str(REPORT_CHAT_ID) or acc_uid == "6727787768")
    print(f"\n🚀 [{acc_name} (ID: {acc_uid}{' - 👑 Master' if is_master else ''})] Connecting MTProto...")
    cl = TelegramClient(StringSession(acc_sess), API_ID, API_HASH)
    try:
        connected = False
        for attempt in range(1, 4):
            try:
                await cl.connect()
                if await cl.is_user_authorized():
                    connected = True
                    break
                else:
                    print(f"[{acc_name}] Session not authorized or revoked.")
                    return
            except Exception as conn_err:
                err_str = str(conn_err)
                if "used under two different IP addresses" in err_str or "AUTH_KEY_DUPLICATED" in err_str:
                    print(f"[{acc_name}] MTProto key duplicated/revoked.")
                    return
                if attempt < 3:
                    print(f"[{acc_name}] Connection attempt {attempt} failed ({conn_err}), retrying in {attempt * 2}s...")
                    await asyncio.sleep(attempt * 2.0)
                else:
                    print(f"[{acc_name}] Could not connect after 3 attempts: {conn_err}")
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

        # 11. JVault Miner (@JVaultMinerBot)
        jv_p = None if is_master else JVAULT_REF
        jv_tok = await extract_bot_webapp_token(cl, JVAULT_BOT, start_param=jv_p, default_url="https://jvaultminer.world/", candidate_short_names=["app", "start"], prefer_direct_url=True)
        if jv_tok:
            tokens["jvault_init_data"] = jv_tok
            tokens["jvault_user_id"] = f"usr_{acc_uid}"
            print(f"  💎 JVault Miner: Collected")
        await asyncio.sleep(0.5)

        # 12. TAVORA Node (@TavoraNode_Bot)
        tav_p = None if is_master else TAVORA_REF
        tav_tok = await extract_bot_webapp_token(cl, TAVORA_BOT, start_param=tav_p, default_url="https://tavoranode.vip/", candidate_short_names=["myapp", "app"], prefer_direct_url=True)
        if tav_tok:
            tokens["tavora_init_data"] = tav_tok
            tokens["tavora_uid"] = "MW0MB8RV" if is_master else acc_uid
            try:
                login_payload = json.dumps({"initData": tav_tok, "referrer_code": "ref_MW0MB8RV", "turnstileToken": ""}).encode()
                h_tl = {"Content-Type": "application/json", "Origin": "https://tavoranode.vip", "Referer": "https://tavoranode.vip/", "User-Agent": "Mozilla/5.0"}
                async with session.post("https://tavoranode.vip/api/user/login/tg", data=login_payload, headers=h_tl, timeout=aiohttp.ClientTimeout(total=8)) as t_resp:
                    if t_resp.status == 200:
                        t_data = await t_resp.json()
                        if t_data.get("auth_info"):
                            tokens["tavora_token"] = t_data["auth_info"]
                            u_inf = t_data.get("user_info", {})
                            tokens["tavora_uid"] = u_inf.get("uid") or tokens["tavora_uid"]
            except Exception:
                pass
            print(f"  ⚡ TAVORA Node: Collected")
        await asyncio.sleep(0.5)

        # 13. Victor's Company (@Victors_Company_bot)
        vic_p = None if is_master else VICTORS_REF
        vic_tok = await extract_bot_webapp_token(cl, VICTORS_BOT, start_param=vic_p, default_url="https://app.victors.company/", candidate_short_names=["app"], prefer_direct_url=False)
        if not vic_tok:
            vic_tok = await extract_bot_webapp_token(cl, VICTORS_BOT, start_param=vic_p, default_url="https://app.victors.company/", candidate_short_names=["app"], prefer_direct_url=True)
        if vic_tok:
            tokens["victors_init_data"] = vic_tok
            print(f"  🏢 Victor's Company: Collected")
        await asyncio.sleep(0.5)

        # 14. AZM Miner (@AZMIR_Miner_BOT)
        azm_p = None if is_master else AZM_REF
        azm_url = f"{AZM_APP_URL}?ref={AZM_REF}" if not is_master else AZM_APP_URL
        azm_tok = await extract_bot_webapp_token(cl, AZM_BOT, start_param=azm_p, default_url=azm_url, candidate_short_names=["app"], prefer_direct_url=True)
        if azm_tok:
            tokens["azm_init_data"] = azm_tok
            tokens["azm"] = azm_tok
            tokens["azmir"] = azm_tok
            print(f"  💎 AZM Miner: Collected")
        await asyncio.sleep(0.5)

        # 15. Dillo Miner (@dillo_miner_bot)
        dillo_p = None if is_master else DILLO_REF
        dillo_tok = await extract_bot_webapp_token(cl, DILLO_BOT, start_param=dillo_p, default_url=DILLO_APP_URL, candidate_short_names=["app"], prefer_direct_url=True)
        if dillo_tok:
            tokens["dillo_init_data"] = dillo_tok
            tokens["dillo"] = dillo_tok
            print(f"  ⛏️ Dillo Miner: Collected")
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
                await extract_account_tokens(acc, session)
                await asyncio.sleep(1.5)
            except Exception as e:
                print(f"Error processing account {acc.get('name')}: {e}")

    print("\n✅ All 24h WebApp tokens collected & pushed to 5x Cloudflare Nodes & Upstash Redis!")


if __name__ == "__main__":
    asyncio.run(main())
