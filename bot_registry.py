"""
bot_registry.py
=============================================================================
SINGLE SOURCE OF TRUTH (SSOT) PLUG-AND-FARM BOT REGISTRY FOR MY AGY AI FLEET
=============================================================================
Defines all active legitimate fleet bots, their referral codes, WebApp URLs,
mandatory sponsor channels, and API farming parameters.

Adding ANY new bot to the fleet in the future requires ONLY adding one entry here!
The cloud token refresher, referral binder, and 24/7 cloud farming engine
automatically pick up new bots from this registry with ZERO per-account manual work.
"""

from typing import Dict, List, Any

ACTIVE_BOTS: Dict[str, Dict[str, Any]] = {
    "atf": {
        "id": "atf",
        "name": "ATF Miner",
        "bot_username": "ATF_AIRDROP_bot",
        "bot_aliases": ["atfminers_bot", "atfminerbot", 7058204689],
        "master_ref_code": "6727787768",
        "app_url": "https://atfminers.asloni.online/miner/index.html?entry=bot_start",
        "token_key": "atf_init_data",
        "short_names": ["app", "miner", "play"],
        "api_base": "https://atfminers.asloni.online/api",
        "sponsor_channels": ["AI_TRADING_FOREX"],
        "requires_turnstile": False,
        "is_active": True,
    },
    "kynex": {
        "id": "kynex",
        "name": "Kynex Network",
        "bot_username": "Kynex_miningbot",
        "bot_aliases": ["kynexnetworkbot"],
        "master_ref_code": "6727787768",
        "app_url": "https://kynex.top/telegram-auth.html",
        "token_key": "kynex_init_data",
        "short_names": ["App", "app"],
        "api_base": "https://us-central1-keynex-e9511.cloudfunctions.net",
        "sponsor_channels": ["kynex_mining", "EarnVaulte", "solanamemes001", "Web3Primeteam"],
        "requires_turnstile": False,
        "is_active": True,
    },
    "pepegram": {
        "id": "pepegram",
        "name": "PepeGram",
        "bot_username": "Pepe_gram_bot",
        "master_ref_code": "1449",
        "app_url": "https://app.miniempire.app/miniapp/",
        "token_key": "pepegram_init_data",
        "short_names": ["app", "miniapp"],
        "api_base": "https://app.miniempire.app/miniapp",
        "sponsor_channels": ["pepe_grammmm", "BTCMAKERR", "PaperHouseNFT"],
        "requires_turnstile": False,
        "is_active": True,
    },
    "adxearn": {
        "id": "adxearn",
        "name": "AdxEarn AI",
        "bot_username": "AdxEarnAibot",
        "master_ref_code": "6727787768",
        "app_url": "https://app.adxearnai.xyz/",
        "token_key": "adxearn_init_data",
        "short_names": ["app", "play"],
        "api_base": "https://api.adxearnai.xyz/api",
        "sponsor_channels": ["AdxearnAi"],
        "requires_turnstile": False,
        "is_active": True,
    },
    "chickenland": {
        "id": "chickenland",
        "name": "Chicken Land",
        "bot_username": "ChickenLandBot",
        "master_ref_code": "12668",
        "app_url": "https://app8968434282.tgcloud.ai/",
        "token_key": "chickenland_init_data",
        "short_names": ["play", "app", "farm"],
        "api_base": "https://app8968434282.tgcloud.ai/api",
        "sponsor_channels": ["LandChickenNews", "ChickenLandPay", "ChickenLandCodes"],
        "requires_turnstile": False,
        "is_active": True,
    },
    "earnvault": {
        "id": "earnvault",
        "name": "EarnVault 🏦",
        "bot_username": "EarnVaultApp_bot",
        "bot_aliases": ["earnvault_bot"],
        "master_ref_code": "6727787768",
        "app_url": "https://eearnvault.top/",
        "token_key": "earnvault_init_data",
        "short_names": ["App", "app", "earn"],
        "api_base": "https://us-central1-earnvaulte.cloudfunctions.net",
        "sponsor_channels": ["EarnVaulte"],
        "requires_turnstile": False,
        "is_active": True,
    },
    "mexoearn": {
        "id": "mexoearn",
        "name": "MexoEarn ⚡",
        "bot_username": "MexoEarni_bot",
        "bot_aliases": ["mexoearnbot"],
        "master_ref_code": "6727787768",
        "app_url": "https://mexoearn.xyz/",
        "token_key": "mexo_init_data",
        "short_names": ["app", "play"],
        "api_base": "https://mexoearn.xyz/api",
        "sponsor_channels": [],
        "requires_turnstile": False,
        "is_active": True,
    },
    "myage": {
        "id": "myage",
        "name": "MyAgeApp 🎂",
        "bot_username": "MyAgeApp",
        "bot_aliases": ["myageapp", "myage_bot", 9269075166],
        "master_ref_code": "r6727787768",
        "app_url": "https://myageapp.xyz/",
        "token_key": "myage_init_data",
        "short_names": ["app", "start", "play"],
        "api_base": "https://myageapp.xyz/api",
        "sponsor_channels": [],
        "requires_turnstile": False,
        "is_active": True,
    },
    "lifetime": {
        "id": "lifetime",
        "name": "Lifetime Energy ⚡",
        "bot_username": "lifetime_energy_bot",
        "bot_aliases": ["lifetime_energy", 8960846166],
        "master_ref_code": "6727787768",
        "app_url": "https://app.miniempire.app/miniapp/index.html?tid=5&lang=en",
        "token_key": "lifetime_energy_init_data",
        "short_names": ["app", "play"],
        "api_base": "https://app.miniempire.app/miniapp",
        "sponsor_channels": [],
        "requires_turnstile": False,
        "is_active": True,
    },
    "cherry": {
        "id": "cherry",
        "name": "Cherry Play",
        "bot_username": "CherryPlayBot",
        "bot_aliases": ["cherryplaybot", "cherryearn"],
        "master_ref_code": "r9bsfi3a8c",
        "app_url": "https://cherryearn.site",
        "token_key": "cherry_init_data",
        "short_names": ["play", "app"],
        "api_base": "https://api.cherryearn.site/api",
        "sponsor_channels": [],
        "requires_turnstile": False,
        "is_active": True,
    },
    "victors": {
        "id": "victors",
        "name": "Victor's Company",
        "bot_username": "Victors_Company_bot",
        "bot_aliases": ["victors_company_bot"],
        "master_ref_code": "ref_9DB8C58014",
        "app_url": "https://app.victors.company/",
        "token_key": "victors_init_data",
        "short_names": ["app"],
        "api_base": "https://server.victors.company/api",
        "sponsor_channels": [],
        "requires_turnstile": True,
        "is_active": True,
    },
    "azm": {
        "id": "azm",
        "name": "AZM Miner",
        "bot_username": "AZMIR_Miner_BOT",
        "bot_aliases": ["azmir_miner_bot"],
        "master_ref_code": "6727787768",
        "app_url": "https://provide-presentation-subsidiaries-heel.trycloudflare.com/",
        "token_key": "azm_init_data",
        "short_names": ["app"],
        "api_base": "https://provide-presentation-subsidiaries-heel.trycloudflare.com/api",
        "sponsor_channels": [],
        "requires_turnstile": False,
        "is_active": True,
    },
    "dillo": {
        "id": "dillo",
        "name": "Dillo Miner",
        "bot_username": "dillo_miner_bot",
        "bot_aliases": ["dillominer_bot", 9058560283],
        "master_ref_code": "6727787768",
        "app_url": "https://dillo-miner-bot.ih0st.app/#/",
        "token_key": "dillo_init_data",
        "short_names": ["app"],
        "api_base": "https://dillo-miner-bot.ih0st.app/api/app",
        "sponsor_channels": ["DilloTONLive"],
        "requires_turnstile": False,
        "is_active": True,
    },
    "hunter": {
        "id": "hunter",
        "name": "Hunter Miner 🎯",
        "bot_username": "GramHtrsbot",
        "bot_aliases": ["gramhtrsbot", 8867174524],
        "master_ref_code": "6727787768",
        "app_url": "https://huntrs.up.railway.app/",
        "token_key": "hunter_init_data",
        "short_names": ["app", "miner"],
        "api_base": "https://huntrs.up.railway.app/api",
        "sponsor_channels": [],
        "requires_turnstile": False,
        "is_active": True,
    }
}

def get_active_bots() -> Dict[str, Dict[str, Any]]:
    """Returns all active bots in the registry."""
    return {k: v for k, v in ACTIVE_BOTS.items() if v.get("is_active", True)}

def get_all_sponsor_channels() -> List[str]:
    """Returns a deduplicated list of all mandatory sponsor channels across all active bots."""
    channels = set()
    for b in ACTIVE_BOTS.values():
        if b.get("is_active", True):
            channels.update(b.get("sponsor_channels", []))
    return sorted(list(channels))

def get_token_keys() -> List[str]:
    """Returns the list of token keys (e.g. ['atf_init_data', ...]) for active bots."""
    return [b["token_key"] for b in ACTIVE_BOTS.values() if b.get("is_active", True)]
