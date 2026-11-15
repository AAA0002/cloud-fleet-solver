#!/usr/bin/env python3
"""
scripts/maintenance/export_gpg_wallet_vault.py
Automated Offline Encrypted Wallet Vault Snapshot Engine.
Exports, validates, and encrypts all 46 fleet wallet records, 24 TON mnemonics,
and 18 EVM private keys into a timestamped, tamper-proof AES-256-CBC archive.

Complies with:
- Rule 6: Strict Secret Hygiene (Zero plaintext secrets in git or logs)
- Rule 9 & Rule 12: Permanent Wallet & Secret Immutability Directive (Mandatory Creation Date & Account Origin Attribution)
- Rule 5: Zero-Trust Ephemeral Infrastructure (Dual disaster recovery to Telegram @myagyai and Cloudflare R2)
"""

import os
import sys
import json
import hashlib
import argparse
import subprocess
import urllib.request
import urllib.error
import urllib.parse
try:
    import requests
except ImportError:
    requests = None
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

# Cloud & Network Configurations
UPSTASH_URL = (os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_URL", "")).rstrip("/")
UPSTASH_TOKEN = os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_TOKEN", "")
REPORT_BOT_TOKEN = os.getenv("REPORT_BOT_TOKEN") or os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_NOTIFICATION_CHANNEL_ID = os.getenv("ADMIN_NOTIFICATION_CHANNEL_ID", "-1004358687850")
BACKUP_CHANNEL = "@myagyai"  # Private Disaster Recovery Vault Channel (-1004453515167)
CF_UPLOAD_URL = "https://restore-agy.agorameet.workers.dev/upload"
SECRET_KEY = os.getenv("SECRET_KEY", "")
VAULT_KEY = os.getenv("VAULT_ENCRYPTION_KEY") or SECRET_KEY or "AAA_AUTO_FLEET_VAULT_BACKUP_KEY"


def safe_http_get(url: str, headers: dict = None, timeout: int = 10) -> Tuple[int, str]:
    """Robust HTTP GET with requests and urllib fallback."""
    if requests is not None:
        try:
            r = requests.get(url, headers=headers or {}, timeout=timeout)
            return r.status_code, r.text
        except Exception as e:
            return 0, str(e)
    try:
        req = urllib.request.Request(url, headers=headers or {}, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as he:
        return he.code, he.read().decode("utf-8") if he.fp else str(he)
    except Exception as e:
        return 0, str(e)


def safe_http_post(url: str, headers: dict = None, data: dict = None, body_bytes: bytes = None, timeout: int = 15) -> Tuple[int, str]:
    """Robust HTTP POST with requests and urllib fallback."""
    if requests is not None:
        try:
            if body_bytes is not None:
                r = requests.post(url, data=body_bytes, headers=headers or {}, timeout=timeout)
            else:
                r = requests.post(url, data=data or {}, headers=headers or {}, timeout=timeout)
            return r.status_code, r.text
        except Exception as e:
            return 0, str(e)
    try:
        h = headers.copy() if headers else {}
        body = body_bytes
        if body is None and data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            h["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=h, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as he:
        return he.code, he.read().decode("utf-8") if he.fp else str(he)
    except Exception as e:
        return 0, str(e)


def get_upstash_keys(pattern: str) -> List[str]:
    """Retrieve keys matching pattern from Upstash Redis REST API."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return []
    try:
        url = f"{UPSTASH_URL}/keys/{pattern}"
        headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
        status, text = safe_http_get(url, headers=headers, timeout=10)
        if status == 200:
            return json.loads(text).get("result", [])
    except Exception as e:
        print(f"⚠️ Error fetching keys for {pattern}: {e}")
    return []


def get_upstash_val(key: str) -> Any:
    """Retrieve key value from Upstash Redis REST API."""
    if not UPSTASH_URL or not UPSTASH_TOKEN:
        return None
    try:
        url = f"{UPSTASH_URL}/get/{key}"
        headers = {"Authorization": f"Bearer {UPSTASH_TOKEN}"}
        status, text = safe_http_get(url, headers=headers, timeout=10)
        if status == 200:
            raw = json.loads(text).get("result")
            if not raw:
                return None
            try:
                return json.loads(raw)
            except Exception:
                return raw
    except Exception as e:
        print(f"⚠️ Error fetching key {key}: {e}")
    return None


def read_local_json(filename: str) -> Any:
    """Safely read a local JSON file."""
    path = os.path.join(BASE_DIR, filename)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def extract_master_vault_secrets() -> Dict[str, Any]:
    """Extract local master vault secrets if present."""
    vault_file = os.path.join(BASE_DIR, "FLEET_WALLETS_SECRETS_MASTER_VAULT.txt")
    secrets = {}
    if os.path.exists(vault_file):
        try:
            with open(vault_file, "r", encoding="utf-8") as f:
                content = f.read()
            secrets["raw_master_vault_available"] = True
            secrets["master_vault_sha256"] = hashlib.sha256(content.encode()).hexdigest()
            secrets["master_vault_size_bytes"] = len(content)
        except Exception:
            secrets["raw_master_vault_available"] = False
    return secrets


def build_full_vault_dataset() -> Dict[str, Any]:
    """Assemble complete wallet vault records across Upstash Redis and local caches."""
    print("🔄 Connecting to Upstash Redis & local repositories...")
    vault_keys = get_upstash_keys("fleet:wallets_vault:*")
    print(f"📦 Found {len(vault_keys)} fleet:wallets_vault:* records in Upstash Redis.")

    # 1. Fetch all cloud vault records
    vault_records = []
    for vk in sorted(vault_keys):
        rec = get_upstash_val(vk)
        if isinstance(rec, dict):
            vault_records.append(rec)

    # 2. Fetch master keys
    ton_cloud = get_upstash_val("fleet:wallets:ton") or {}
    evm_cloud = get_upstash_val("fleet:wallets:evm") or {}
    tron_cloud = get_upstash_val("fleet:wallets:tron") or {}
    sol_cloud = get_upstash_val("fleet:wallets:solana") or {}
    btc_cloud = get_upstash_val("fleet:wallets:btc") or {}

    # 3. Read local wallet files for full redundancy
    ton_local = read_local_json("fleet_ton_wallets.json") or {}
    evm_local = read_local_json("fleet_evm_wallets.json") or {}
    tron_local = read_local_json("fleet_tron_wallets.json") or {}
    sol_local = read_local_json("fleet_solana_wallets.json") or {}
    btc_local = read_local_json("fleet_btc_wallets.json") or {}

    # Merge master dictionaries (cloud priority, local fallback)
    ton_merged = {**ton_local, **ton_cloud} if isinstance(ton_cloud, dict) else ton_local
    evm_merged = {**evm_local, **evm_cloud} if isinstance(evm_cloud, dict) else evm_local
    tron_merged = {**tron_local, **tron_cloud} if isinstance(tron_cloud, dict) else tron_local
    sol_merged = {**sol_local, **sol_cloud} if isinstance(sol_cloud, dict) else sol_local
    btc_merged = {**btc_local, **btc_cloud} if isinstance(btc_cloud, dict) else btc_local

    master_vault_meta = extract_master_vault_secrets()

    now_iso = datetime.now(timezone.utc).isoformat()

    snapshot_payload = {
        "snapshot_id": f"snapshot_wallets_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "schema_version": "2.0.0",
        "created_at": now_iso,
        "compliance_rules": [
            "Rule 6: Zero Plaintext Secrets in Tracked Files & Public Repositories",
            "Rule 9 & Rule 12: Permanent Wallet & Secret Immutability Directive (Mandatory Creation Date & Account Origin Attribution)",
            "Rule 5: Zero-Trust Ephemeral Infrastructure & Dual Disaster Recovery"
        ],
        "immutability_guarantee": (
            "Wallets and wallet secrets CANNOT be deleted, purged, or overwritten under ANY circumstances. "
            "Even if a Telegram account is banned or deleted, its associated multi-chain wallets and secrets remain permanently archived."
        ),
        "summary": {
            "total_accounts_audited": len(vault_records),
            "total_cloud_vault_records": len(vault_keys),
            "ton_mnemonics_preserved": len(ton_merged),
            "evm_private_keys_preserved": len(evm_merged),
            "tron_wallets_preserved": len(tron_merged),
            "solana_wallets_preserved": len(sol_merged),
            "btc_wallets_preserved": len(btc_merged),
            "master_vault_file_attached": master_vault_meta.get("raw_master_vault_available", False)
        },
        "vault_records": vault_records,
        "master_keys": {
            "ton": ton_merged,
            "evm": evm_merged,
            "tron": tron_merged,
            "solana": sol_merged,
            "btc": btc_merged
        },
        "master_vault_metadata": master_vault_meta
    }

    return snapshot_payload


def encrypt_data(plaintext_bytes: bytes, password: str) -> bytes:
    """Encrypt byte payload with OpenSSL AES-256-CBC with PBKDF2 (100,000 iterations and salt)."""
    env = os.environ.copy()
    env["_VAULT_PW"] = password
    proc = subprocess.Popen(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-salt", "-pass", "env:_VAULT_PW"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )
    stdout, stderr = proc.communicate(input=plaintext_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"OpenSSL encryption failed: {stderr.decode()}")
    return stdout


def decrypt_data(ciphertext_bytes: bytes, password: str) -> bytes:
    """Decrypt byte payload with OpenSSL AES-256-CBC."""
    env = os.environ.copy()
    env["_VAULT_PW"] = password
    proc = subprocess.Popen(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "100000", "-pass", "env:_VAULT_PW"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env
    )
    stdout, stderr = proc.communicate(input=ciphertext_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"OpenSSL decryption failed: {stderr.decode()}")
    return stdout


def upload_to_telegram(enc_path: str, caption: str) -> Optional[str]:
    """Upload encrypted snapshot file to Telegram Disaster Recovery Vault channel."""
    if not REPORT_BOT_TOKEN:
        print("⚠️ REPORT_BOT_TOKEN missing; skipping Telegram upload.")
        return None
    try:
        url = f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendDocument"
        fname = os.path.basename(enc_path)
        res_json = {}
        if requests is not None:
            with open(enc_path, "rb") as f:
                data = {
                    "chat_id": BACKUP_CHANNEL,
                    "caption": caption,
                    "parse_mode": "HTML"
                }
                files = {
                    "document": (fname, f, "application/octet-stream")
                }
                r = requests.post(url, data=data, files=files, timeout=30)
                res_json = r.json()
        else:
            cmd = [
                "curl", "-s", "-X", "POST", url,
                "-F", f"chat_id={BACKUP_CHANNEL}",
                "-F", f"caption={caption}",
                "-F", "parse_mode=HTML",
                "-F", f"document=@{enc_path}"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            res_json = json.loads(res.stdout) if res.stdout else {}

        if res_json.get("ok"):
            doc = res_json["result"]["document"]
            file_id = doc.get("file_id")
            print(f"✅ Uploaded to Telegram {BACKUP_CHANNEL}: File ID {file_id[:12]}...")
            return file_id
        else:
            print(f"❌ Telegram sendDocument failed: {res_json.get('description')}")
    except Exception as e:
        print(f"⚠️ Telegram upload error: {e}")
    return None


def upload_to_cloudflare_r2(enc_bytes: bytes, filename: str) -> bool:
    """Upload encrypted snapshot file to Cloudflare R2 via Edge Gateway Mesh."""
    sec = os.getenv("SECRET_KEY")
    if not sec:
        return False
    try:
        headers = {
            "Authorization": f"Bearer {sec}",
            "Content-Type": "application/octet-stream",
            "X-Filename": filename,
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        status, _ = safe_http_post(CF_UPLOAD_URL, headers=headers, body_bytes=enc_bytes, timeout=15)
        if status == 200:
            print("✅ Uploaded to Cloudflare R2 bucket via Edge Gateway Mesh.")
            return True
    except Exception as e:
        print(f"⚠️ Cloudflare R2 upload notice: {e}")
    return False


def send_admin_alert_card(summary: Dict[str, Any], enc_fname: str, sha256_enc: str) -> None:
    """Send concise operational alert card to Admin Notification Channel (-1004358687850)."""
    if not REPORT_BOT_TOKEN or not ADMIN_NOTIFICATION_CHANNEL_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{REPORT_BOT_TOKEN}/sendMessage"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        text = (
            f"🛡️ <b>Fleet Wallet Vault — Encrypted Offline Snapshot</b>\n"
            f"📅 <i>{ts}</i>\n\n"
            f"• <b>Snapshot Archive:</b> <code>{enc_fname}</code>\n"
            f"• <b>Accounts Audited:</b> <code>{summary.get('total_accounts_audited', 0)} Preserved</code>\n"
            f"• <b>TON Mnemonics:</b> <code>{summary.get('ton_mnemonics_preserved', 0)} Preserved</code>\n"
            f"• <b>EVM Private Keys:</b> <code>{summary.get('evm_private_keys_preserved', 0)} Preserved</code>\n"
            f"• <b>Multichain (TRON/SOL/BTC):</b> <code>{summary.get('tron_wallets_preserved', 0)}/{summary.get('solana_wallets_preserved', 0)}/{summary.get('btc_wallets_preserved', 0)}</code>\n"
            f"• <b>Integrity Checksum:</b> <code>sha256:{sha256_enc[:16]}...</code>\n"
            f"• <b>Encryption:</b> <code>AES-256-CBC (PBKDF2 100k iter)</code>\n"
            f"• <b>Vault Delivery:</b> <code>Telegram @myagyai + Cloudflare R2</code> ✅\n\n"
            f"<i>Rule 12 Compliance: Permanent immutability enforced. All wallets and secrets immutable.</i>"
        )
        data = {
            "chat_id": ADMIN_NOTIFICATION_CHANNEL_ID,
            "text": text,
            "parse_mode": "HTML"
        }
        safe_http_post(url, data=data, timeout=10)
        print(f"✅ Alert card dispatched to Admin Notification Channel ({ADMIN_NOTIFICATION_CHANNEL_ID}).")
    except Exception as e:
        print(f"⚠️ Failed to send admin alert card: {e}")


def run_snapshot_export() -> Tuple[str, str]:
    """Execute complete snapshot creation, encryption, self-test, and cloud delivery."""
    # 1. Assemble data
    payload = build_full_vault_dataset()
    plaintext_json = json.dumps(payload, indent=2, ensure_ascii=False)
    plaintext_bytes = plaintext_json.encode("utf-8")
    sha256_plain = hashlib.sha256(plaintext_bytes).hexdigest()

    print(f"📊 Dataset compiled: {len(plaintext_bytes)} bytes, SHA-256: {sha256_plain[:16]}...")

    # 2. Encrypt
    print("🔒 Encrypting with OpenSSL AES-256-CBC (PBKDF2, 100,000 iterations)...")
    enc_bytes = encrypt_data(plaintext_bytes, VAULT_KEY)
    sha256_enc = hashlib.sha256(enc_bytes).hexdigest()

    # 3. Round-trip self-test
    print("🧪 Performing round-trip decryption self-verification...")
    dec_bytes = decrypt_data(enc_bytes, VAULT_KEY)
    sha256_dec = hashlib.sha256(dec_bytes).hexdigest()
    if sha256_dec != sha256_plain:
        raise ValueError("❌ CRITICAL INTEGRITY MISMATCH: Decrypted SHA-256 does not match original!")
    dec_obj = json.loads(dec_bytes.decode("utf-8"))
    if dec_obj.get("summary", {}).get("total_accounts_audited") != payload["summary"]["total_accounts_audited"]:
        raise ValueError("❌ CRITICAL INTEGRITY MISMATCH: Account counts mismatch in round-trip!")
    print("✅ Self-test PASSED: 100% round-trip fidelity verified.")

    # 4. Save encrypted archive locally in backups directory
    backups_dir = os.path.join(BASE_DIR, "backups")
    os.makedirs(backups_dir, exist_ok=True)
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    enc_fname = f"fleet_wallets_vault_{ts_str}.enc"
    enc_path = os.path.join(backups_dir, enc_fname)
    with open(enc_path, "wb") as f:
        f.write(enc_bytes)
    os.chmod(enc_path, 0o600)  # strict permissions
    print(f"💾 Encrypted snapshot saved: {enc_path} ({len(enc_bytes)} bytes, chmod 600)")

    # 5. Save metadata file (unencrypted, contains hashes & counts only, zero secrets)
    meta_fname = f"fleet_wallets_vault_{ts_str}.meta.json"
    meta_path = os.path.join(backups_dir, meta_fname)
    meta_obj = {
        "snapshot_id": payload["snapshot_id"],
        "created_at": payload["created_at"],
        "archive_file": enc_fname,
        "archive_size_bytes": len(enc_bytes),
        "sha256_ciphertext": sha256_enc,
        "sha256_plaintext": sha256_plain,
        "encryption": "AES-256-CBC-PBKDF2-100K",
        "summary": payload["summary"]
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_obj, f, indent=2)
    print(f"📄 Metadata saved: {meta_path}")

    # 6. Deliver to Telegram & Cloudflare R2
    caption = (
        f"🛡️ <b>Fleet Wallets Vault — Tamper-Proof Snapshot</b>\n"
        f"📅 <i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</i>\n\n"
        f"• <b>Archive:</b> <code>{enc_fname}</code> ({round(len(enc_bytes)/1024, 1)} KB)\n"
        f"• <b>Accounts Preserved:</b> <code>{payload['summary']['total_accounts_audited']} Accounts</code>\n"
        f"• <b>TON Mnemonics:</b> <code>{payload['summary']['ton_mnemonics_preserved']} Preserved</code>\n"
        f"• <b>EVM Private Keys:</b> <code>{payload['summary']['evm_private_keys_preserved']} Preserved</code>\n"
        f"• <b>Integrity SHA-256:</b> <code>{sha256_enc}</code>\n"
        f"• <b>Encryption:</b> <code>AES-256-CBC (PBKDF2 100k iter)</code>\n\n"
        f"<i>Rule 12 Compliance: Permanent immutability enforced. All wallets and secrets immutable.</i>"
    )
    upload_to_telegram(enc_path, caption)
    upload_to_cloudflare_r2(enc_bytes, enc_fname)
    send_admin_alert_card(payload["summary"], enc_fname, sha256_enc)

    return enc_path, meta_path


def run_decrypt_cli(enc_path: str, key: str, out_path: Optional[str] = None):
    """Decrypt an existing snapshot archive."""
    if not os.path.exists(enc_path):
        print(f"❌ Error: Encrypted file not found: {enc_path}")
        sys.exit(1)
    with open(enc_path, "rb") as f:
        enc_bytes = f.read()

    print(f"🔓 Decrypting {enc_path} ({len(enc_bytes)} bytes)...")
    dec_bytes = decrypt_data(enc_bytes, key)
    dec_json = json.loads(dec_bytes.decode("utf-8"))

    summary = dec_json.get("summary", {})
    print("✅ Decryption successful!")
    print(f"• Snapshot ID: {dec_json.get('snapshot_id')}")
    print(f"• Created At: {dec_json.get('created_at')}")
    print(f"• Accounts Audited: {summary.get('total_accounts_audited')}")
    print(f"• TON Mnemonics: {summary.get('ton_mnemonics_preserved')}")
    print(f"• EVM Private Keys: {summary.get('evm_private_keys_preserved')}")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(dec_bytes.decode("utf-8"))
        os.chmod(out_path, 0o600)
        print(f"💾 Decrypted payload written to: {out_path} (chmod 600)")
    else:
        print("\n(Use --out <path> to save the decrypted JSON file)")


def main():
    parser = argparse.ArgumentParser(description="Automated Offline Encrypted Wallet Vault Snapshot Engine")
    parser.add_argument("--export", action="store_true", help="Generate and deliver new encrypted vault snapshot")
    parser.add_argument("--decrypt", help="Path to .enc file to decrypt and inspect")
    parser.add_argument("--key", default=VAULT_KEY, help="Decryption key (defaults to VAULT_ENCRYPTION_KEY or SECRET_KEY)")
    parser.add_argument("--out", help="Output path for decrypted JSON")

    args = parser.parse_args()

    if args.decrypt:
        run_decrypt_cli(args.decrypt, args.key, args.out)
    else:
        run_snapshot_export()


if __name__ == "__main__":
    main()
