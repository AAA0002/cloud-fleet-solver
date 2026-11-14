#!/usr/bin/env python3
"""
scripts/maintenance/scan_secrets.py
Automated Zero-Secret Scanner for AAA AUTO Fleet.
Scans tracked files, staged changes, or working directory for accidental secret leakage:
Telegram Bot Tokens, EVM/TON private keys, mnemonics, GitHub PATs, Cloud credentials.

Exits with:
  0 - Clean (No secrets detected)
  1 - Secret detected (CI / Pre-commit failure)
"""

import os
import sys
import re
import argparse
import subprocess
from typing import List, Tuple, Dict, Any

# High-precision detection rules
RULES = [
    {
        "id": "telegram-bot-token",
        "name": "Telegram Bot Token",
        "pattern": re.compile(r'(?<![A-Za-z0-9_])([0-9]{9,10}:[A-Za-z0-9_-]{35})(?![A-Za-z0-9_])'),
        "severity": "CRITICAL"
    },
    {
        "id": "private-key-block",
        "name": "Private Key Block",
        "pattern": re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----'),
        "severity": "CRITICAL"
    },
    {
        "id": "assigned-evm-private-key",
        "name": "Hardcoded EVM/Hex Private Key",
        "pattern": re.compile(r'(?i)(?:private_?key|priv_?key|secret_?key|signing_?key)\s*[:=]\s*[\'"](?:0x)?([0-9a-fA-F]{64})[\'"]'),
        "severity": "CRITICAL"
    },
    {
        "id": "github-pat",
        "name": "GitHub Personal Access Token",
        "pattern": re.compile(r'\b(?:ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9_]{82}|gho_[a-zA-Z0-9]{36}|ghu_[a-zA-Z0-9]{36})\b'),
        "severity": "CRITICAL"
    },
    {
        "id": "aws-access-key",
        "name": "AWS Access Key ID",
        "pattern": re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
        "severity": "HIGH"
    },
    {
        "id": "google-api-key",
        "name": "Google Cloud / AI API Key",
        "pattern": re.compile(r'\bAIza[0-9A-Za-z\\-_]{35}\b'),
        "severity": "HIGH"
    },
    {
        "id": "upstash-redis-token",
        "name": "Hardcoded Upstash Redis Token",
        "pattern": re.compile(r'(?i)(?:UPSTASH_REDIS_REST_TOKEN|UPSTASH_TOKEN)\s*[:=]\s*[\'"]([a-zA-Z0-9_-]{25,})[\'"]'),
        "severity": "CRITICAL"
    },
    {
        "id": "pinata-jwt-token",
        "name": "Hardcoded Pinata/Cloud JWT",
        "pattern": re.compile(r'(?i)(?:PINATA_JWT|BEARER_TOKEN)\s*[:=]\s*[\'"](eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,})[\'"]'),
        "severity": "HIGH"
    }
]

# Known documentation examples, dummy tokens, and allowlisted strings
ALLOWLIST_PATTERNS = [
    re.compile(r'YOUR_.*'),
    re.compile(r'<.*?>'),
    re.compile(r'\.\.\.'),
    re.compile(r'dummy[_-]token', re.IGNORECASE),
    re.compile(r'ghp_example', re.IGNORECASE),
    re.compile(r'0000000000:AA[A-Za-z0-9_-]{33}'),
    re.compile(r'1234567890:AA[A-Za-z0-9_-]{33}'),
    re.compile(r'816\.\.\.:AAE\.\.\.'),
    re.compile(r'ghp_\.\.\.'),
    re.compile(r'example[a-zA-Z0-9_]*', re.IGNORECASE),
    re.compile(r'AIzaSyDummy[a-zA-Z0-9_-]{24}'),
    # EarnVault Public MiniApp Firebase Web API Key (Public Client Identifier for eearnvault.top)
    re.compile(r'AIzaSyCP9yYj_KSQxv0lW0EoqR39_QTO2SGJGEg')
]

# File paths to completely exclude from scanning
IGNORED_PATHS = {
    ".env",
    ".git",
    "backups",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    ".cloudflare_credentials",
    "FLEET_WALLETS_SECRETS_MASTER_VAULT.txt",
    "USER_BOT_FLEET_SECRETS_VAULT.json",
    "ARCHIVED_ACCOUNT_10_OLD_WALLETS.json"
}

IGNORED_EXTENSIONS = {
    ".session",
    ".session-journal",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".enc",
    ".tar",
    ".gz"
}


def is_allowlisted(match_str: str) -> bool:
    """Check if the matched secret is a recognized example or placeholder."""
    for pat in ALLOWLIST_PATTERNS:
        if pat.search(match_str):
            return True
    return False


def get_tracked_files(repo_dir: str) -> List[str]:
    """Get list of files tracked by git."""
    try:
        res = subprocess.check_output(["git", "ls-files"], cwd=repo_dir, stderr=subprocess.DEVNULL)
        files = res.decode("utf-8").splitlines()
        return [f for f in files if f.strip()]
    except Exception:
        # Fallback to walk if not a git repository
        all_files = []
        for root, dirs, fnames in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_PATHS and not d.startswith(".")]
            for fn in fnames:
                all_files.append(os.path.relpath(os.path.join(root, fn), repo_dir))
        return all_files


def get_staged_files(repo_dir: str) -> List[str]:
    """Get list of git staged files."""
    try:
        res = subprocess.check_output(["git", "diff", "--name-only", "--cached"], cwd=repo_dir, stderr=subprocess.DEVNULL)
        files = res.decode("utf-8").splitlines()
        return [f for f in files if f.strip()]
    except Exception:
        return []


def mask_secret(secret_str: str) -> str:
    """Safely mask a secret for CLI reporting."""
    if len(secret_str) <= 8:
        return "****"
    return secret_str[:4] + "****" + secret_str[-4:]


def scan_file(file_path: str, repo_dir: str) -> List[Dict[str, Any]]:
    """Scan an individual file against all security rules."""
    abs_path = os.path.join(repo_dir, file_path)
    if not os.path.exists(abs_path) or os.path.isdir(abs_path):
        return []

    # Check extension
    _, ext = os.path.splitext(file_path)
    if ext.lower() in IGNORED_EXTENSIONS:
        return []

    # Check path ignores
    for part in file_path.split(os.sep):
        if part in IGNORED_PATHS:
            return []

    findings = []
    try:
        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
            for line_no, line in enumerate(f, 1):
                # Quick length check
                if len(line) > 5000:
                    continue  # skip minified bundles
                for rule in RULES:
                    matches = rule["pattern"].findall(line)
                    for match in matches:
                        match_text = match if isinstance(match, str) else match[0]
                        if not is_allowlisted(match_text):
                            findings.append({
                                "file": file_path,
                                "line_no": line_no,
                                "rule_id": rule["id"],
                                "rule_name": rule["name"],
                                "severity": rule["severity"],
                                "masked": mask_secret(match_text),
                                "line_preview": line.strip()[:80]
                            })
    except Exception as e:
        pass

    return findings


def main():
    parser = argparse.ArgumentParser(description="Zero-Secret Scanner for AAA AUTO Fleet")
    parser.add_argument("--repo-dir", default=".", help="Base repository directory")
    parser.add_argument("--staged", action="store_true", help="Scan only git staged files")
    parser.add_argument("--all", action="store_true", help="Scan all workspace files (except ignored)")
    parser.add_argument("--file", help="Scan a single specific file")

    args = parser.parse_args()
    repo_dir = os.path.abspath(args.repo_dir)

    print("🛡️  AAA AUTO Fleet — Automated Secret Scanner")
    print(f"📁 Target Directory: {repo_dir}")

    if args.file:
        files_to_scan = [args.file]
    elif args.staged:
        files_to_scan = get_staged_files(repo_dir)
        print(f"🔍 Mode: Staged Changes Only ({len(files_to_scan)} files)")
    elif args.all:
        files_to_scan = []
        for root, dirs, fnames in os.walk(repo_dir):
            dirs[:] = [d for d in dirs if d not in IGNORED_PATHS and not d.startswith(".")]
            for fn in fnames:
                rel = os.path.relpath(os.path.join(root, fn), repo_dir)
                files_to_scan.append(rel)
        print(f"🔍 Mode: All Workspace Files ({len(files_to_scan)} files)")
    else:
        files_to_scan = get_tracked_files(repo_dir)
        print(f"🔍 Mode: Git Tracked Files ({len(files_to_scan)} files)")

    all_findings = []
    scanned_count = 0

    for rel_path in files_to_scan:
        findings = scan_file(rel_path, repo_dir)
        if findings:
            all_findings.extend(findings)
        scanned_count += 1

    print(f"✅ Scanned {scanned_count} files against {len(RULES)} security rules.\n")

    if not all_findings:
        print("🎉 SUCCESS: 0 secrets detected. Codebase meets Strict Secret Hygiene (Rule 6).")
        sys.exit(0)
    else:
        print(f"🚨 CRITICAL ALERT: {len(all_findings)} secret leakage finding(s) detected!\n")
        for f in all_findings:
            print(f"  ❌ [{f['severity']}] {f['rule_name']} in {f['file']}:{f['line_no']}")
            print(f"     Found: {f['masked']}")
            print(f"     Preview: {f['line_preview']}\n")
        print("Action Required: Remove secrets before committing or pushing!")
        sys.exit(1)


if __name__ == "__main__":
    main()
