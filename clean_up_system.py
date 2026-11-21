#!/usr/bin/env python3
"""
clean_up_system.py — Autonomous Multi-Tier Sanitation & Maintenance Engine
Part of the AAA AUTO Cloud Fleet & Ephemeral Infrastructure.

Enforces:
1. Local Disk & Ephemeral Workspace Hygiene (prunes __pycache__, rotates logs, caps old backups).
2. Phone Process Sanitation (Zero Phone Load - Rule 4: terminates rogue local loops).
3. Turso LibSQL kv_mesh Sanitation (purges defunct bot keys, enforces Strict 12-Bot Integrity).
4. GitHub Actions CI/CD History Pruning (cleans runs older than 5 days).
5. Safe Boundaries (Strictly protects accounts.json, .env, and User Bot data - Rule 1).
"""

import os
import sys
import glob
import time
import json
import shutil
import logging
import argparse
import subprocess
import urllib.request
import urllib.error

# -----------------------------------------------------------------------------
# Logging Configuration
# -----------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [CLEANUP] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("cleanup_system")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment credentials safely
ENV_FILE = os.path.join(BASE_DIR, ".env")
env_config = {}
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env_config[k.strip()] = v.strip().strip('"').strip("'")

# Turso LibSQL Primary Authoritative Mesh
TURSO_URL = (
    env_config.get("TURSO_DB_URL")
    or env_config.get("TURSO_URL")
    or os.environ.get("TURSO_DB_URL")
    or os.environ.get("TURSO_URL")
    or "https://my-agy-fleet-db-aaa2743.aws-ap-south-1.turso.io"
)
TURSO_TOKEN = (
    env_config.get("TURSO_DB_TOKEN")
    or env_config.get("TURSO_TOKEN")
    or os.environ.get("TURSO_DB_TOKEN")
    or os.environ.get("TURSO_TOKEN")
    or ""
)
def _resolve_gh_pat() -> str:
    cred_path = os.path.expanduser("~/.git-credentials")
    if os.path.exists(cred_path):
        try:
            with open(cred_path, "r", encoding="utf-8") as f:
                for line in f:
                    if "@github.com" in line:
                        auth_part = line.strip().split("@github.com")[0].replace("https://", "")
                        return auth_part.split(":")[1] if ":" in auth_part else auth_part
        except Exception:
            pass
    return (
        env_config.get("GITHUB_USERBOT_PAT")
        or env_config.get("GITHUB_FINE_GRAINED_PAT")
        or os.environ.get("GITHUB_USERBOT_PAT")
        or os.environ.get("GITHUB_FINE_GRAINED_PAT")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or ""
    )

GH_PAT = _resolve_gh_pat()
GH_REPOS = ["AAA0002/ATL", "AAA0002/cloud-fleet-solver", "AAA0002/cloud-fleet-ops", "AAA0002/my-agy-fleet-standby"]

# -----------------------------------------------------------------------------
# 1. Local Workspace & Disk Sanitation
# -----------------------------------------------------------------------------
def clean_local_disk(dry_run: bool = False) -> dict:
    """Removes caches, truncates oversized logs, and prunes stale backup archives."""
    logger.info("🧹 [Tier 1] Initiating Local Workspace & Disk Sanitation...")
    stats = {
        "pycache_removed": 0,
        "backup_zips_pruned": 0,
        "logs_rotated": 0,
        "bytes_freed": 0
    }

    # 1.1 Remove __pycache__ and bytecode (pruning large non-project directories)
    SKIP_DIRS = {".git", ".gemini", "node_modules", ".local", ".npm", ".tgcloud"}
    for root, dirs, files in os.walk(BASE_DIR):
        # Prune large directories from search tree in-place
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for d in list(dirs):
            if d in ("__pycache__", ".pytest_cache", ".coverage_cache"):
                dp = os.path.join(root, d)
                try:
                    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(dp) for f in fs)
                    if not dry_run:
                        shutil.rmtree(dp, ignore_errors=True)
                    stats["pycache_removed"] += 1
                    stats["bytes_freed"] += size
                except Exception as e:
                    logger.debug(f"Could not remove {dp}: {e}")

    # 1.2 Retain only the 2 most recent backup zips
    backup_zips = sorted(
        glob.glob(os.path.join(BASE_DIR, "backups", "my_agy_ai_backup_*.zip")) +
        glob.glob(os.path.join(BASE_DIR, "my_agy_ai_backup_*.zip")),
        key=os.path.getmtime,
        reverse=True
    )
    if len(backup_zips) > 2:
        for old_zip in backup_zips[2:]:
            try:
                size = os.path.getsize(old_zip)
                if not dry_run:
                    os.remove(old_zip)
                stats["backup_zips_pruned"] += 1
                stats["bytes_freed"] += size
                logger.info(f"  🗑️ Pruned stale local backup archive: {os.path.basename(old_zip)} ({size / 1024 / 1024:.2f} MB)")
            except Exception as e:
                logger.warning(f"Error removing {old_zip}: {e}")

    # 1.3 Rotate / truncate oversized logs (> 2 MB)
    for log_path in glob.glob(os.path.join(BASE_DIR, "*.log")) + glob.glob(os.path.join(BASE_DIR, "**/*.log")):
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > 2 * 1024 * 1024:
                size = os.path.getsize(log_path)
                if not dry_run:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as lf:
                        lines = lf.readlines()
                    tail_lines = lines[-1500:] if len(lines) > 1500 else lines
                    with open(log_path, "w", encoding="utf-8") as lf:
                        lf.writelines(tail_lines)
                    new_size = os.path.getsize(log_path)
                    stats["bytes_freed"] += max(0, size - new_size)
                else:
                    stats["bytes_freed"] += max(0, size - 200 * 1024)
                stats["logs_rotated"] += 1
                logger.info(f"  📜 Rotated oversized log: {os.path.basename(log_path)}")
        except Exception as e:
            logger.debug(f"Log rotation note for {log_path}: {e}")

    # 1.4 Clean temporary browser and scrap files in /tmp
    for tmp_pattern in ("/tmp/*chrome*", "/tmp/*xvfb*", "/tmp/*selenium*", "/tmp/*tmp*"):
        for tmp_file in glob.glob(tmp_pattern):
            try:
                if os.path.isfile(tmp_file) or os.path.islink(tmp_file):
                    size = os.path.getsize(tmp_file)
                    if not dry_run:
                        os.remove(tmp_file)
                    stats["bytes_freed"] += size
                elif os.path.isdir(tmp_file):
                    if not dry_run:
                        shutil.rmtree(tmp_file, ignore_errors=True)
            except Exception:
                pass

    logger.info(f"✅ Local Disk Clean: Freed ~{stats['bytes_freed'] / 1024 / 1024:.2f} MB ({stats['pycache_removed']} caches, {stats['backup_zips_pruned']} zips, {stats['logs_rotated']} logs rotated)")
    return stats


# -----------------------------------------------------------------------------
# 2. Local Process Sanitation (Strict Phone Guardrail - Rule 4)
# -----------------------------------------------------------------------------
def sanitize_local_processes(dry_run: bool = False) -> int:
    """Detects and terminates any unauthorized Python worker/scraper loops running locally on phone."""
    logger.info("🛡️ [Tier 2] Verifying Zero Phone Load Directive (Samsung Galaxy A30)...")
    killed = 0
    my_pid = os.getpid()

    # Disallowed scripts that must NEVER run locally on the phone (Rule 4)
    ROUGE_SCRIPTS = [
        "render_app_latest.py",
        "render_standby_app.py",
        "mrg_turnstile_solver.py",
        "victors_sb_solver.py",
        "userbot_refresher.py",
        "batch_atf_miner.py",
        "cloud_wallet_sweeper.py",
        "bot_supervisor.sh"
    ]

    try:
        ps_out = subprocess.check_output(["ps", "-ef"], text=True)
        for line in ps_out.splitlines():
            if "python" in line or "bash" in line:
                for script in ROUGE_SCRIPTS:
                    if script in line and str(my_pid) not in line:
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            pid = int(parts[1])
                            logger.warning(f"⚠️ Detected unauthorized background worker on device: PID {pid} ({script})")
                            if not dry_run:
                                try:
                                    os.kill(pid, 9)
                                    killed += 1
                                    logger.info(f"  🛑 Terminated rogue PID {pid} to protect mobile resources.")
                                except Exception as ke:
                                    logger.error(f"  Could not terminate PID {pid}: {ke}")
    except Exception as e:
        logger.debug(f"Process check note: {e}")

    if killed == 0:
        logger.info("✅ Process Sanitation: 0 rogue background bots detected on phone (100% cloud compliant).")
    return killed


# -----------------------------------------------------------------------------
# 3. Turso LibSQL Edge Mesh Sanitation (Strict 12-Bot Integrity)
# -----------------------------------------------------------------------------
def sanitize_turso_libsql(dry_run: bool = False) -> int:
    """Purges defunct bot keys from Turso LibSQL kv_mesh (Strict 12-Bot Integrity)."""
    logger.info("🛡️ [Tier 3] Auditing Turso LibSQL kv_mesh for defunct bot keys...")
    if not (TURSO_URL and TURSO_TOKEN):
        logger.warning("Turso credentials not configured. Skipping Turso cleanup.")
        return 0

    defunct_bots = [
        "mrg", "vyro", "diggo", "cherry", "cherryplay", "azm", "azmir",
        "tavora", "tvr", "jvault", "jvt", "lum", "lumeon", "cryptofarm", "crypto_farm",
        "grouche", "grouchemine", "leafearn", "leaf", "pumpkin", "stones", "ailab",
        "ultrawallet", "finvora", "ainovum", "tensor", "tontrader", "tac", "usdtquad",
        "apex", "apx", "art", "trxpower", "turbogram", "mininggram", "grc", "paidz",
        "astra", "bitcoincloud", "bitcoin_cloud", "ominix", "grammaster", "gram_master"
    ]
    endpoint = TURSO_URL.rstrip("/").replace("libsql://", "https://") + "/v2/pipeline"
    chunk_size = 8
    total_purged = 0

    for i in range(0, len(defunct_bots), chunk_size):
        chunk = defunct_bots[i:i+chunk_size]
        where_clauses = []
        for b in chunk:
            where_clauses.append(f"key LIKE 'fleet:last_farm:{b}:%'")
            where_clauses.append(f"key LIKE 'fleet:farm_error:{b}:%'")
            where_clauses.append(f"key LIKE 'fleet:{b}:%'")
            where_clauses.append(f"key LIKE 'fleet:tokens:%:{b}%'")
        sql = f"DELETE FROM kv_mesh WHERE {' OR '.join(where_clauses)};"
        if not dry_run:
            try:
                body = json.dumps({"requests": [{"type": "execute", "stmt": {"sql": sql}}, {"type": "close"}]}).encode()
                req = urllib.request.Request(endpoint, data=body, headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=12) as resp:
                    data = json.loads(resp.read().decode())
                    if data.get("results"):
                        affected = data["results"][0].get("response", {}).get("result", {}).get("affected_row_count", 0)
                        total_purged += affected
                        if affected > 0:
                            logger.info(f"  🧹 Turso: Deleted {affected} dead keys for {chunk}")
            except Exception as e:
                logger.debug(f"Turso cleanup note for {chunk}: {e}")

    logger.info(f"✅ Turso LibSQL Hygiene Complete: {total_purged} defunct bot keys purged.")
    return total_purged


def sanitize_upstash_redis(dry_run: bool = False) -> dict:
    """Backward-compatibility stub: Upstash Redis is permanently decommissioned in favor of Turso LibSQL kv_mesh."""
    purged = sanitize_turso_libsql(dry_run=dry_run)
    return {"purged": purged, "lists_trimmed": 0, "stale_passes_purged": 0}


# -----------------------------------------------------------------------------
# 4. GitHub Actions CI/CD History Pruning
# -----------------------------------------------------------------------------
def clean_github_actions_history(max_age_days: int = 7, dry_run: bool = False) -> int:
    """Deletes old completed/cancelled/failed workflow runs to keep CI history clean."""
    logger.info(f"🐙 [Tier 4] Pruning GitHub Actions workflow run history (runs older than {max_age_days} days)...")
    if not GH_PAT:
        logger.warning("GitHub PAT not found. Skipping CI history pruning.")
        return 0

    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "AAA-Fleet-Cleanup"
    }
    deleted_runs = 0

    for repo in GH_REPOS:
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/actions/runs?per_page=50&status=completed",
                headers=headers
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                runs = data.get("workflow_runs", [])

            cutoff_sec = time.time() - (max_age_days * 86400)
            for r in runs:
                created_at_str = r.get("created_at")
                run_id = r.get("id")
                conclusion = r.get("conclusion")
                if created_at_str and run_id:
                    # ISO parsing (e.g. 2026-10-01T12:00:00Z)
                    try:
                        import datetime
                        dt = datetime.datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        run_epoch = dt.timestamp()
                        if run_epoch < cutoff_sec or conclusion in ("failure", "cancelled", "timed_out", "startup_failure"):
                            if not dry_run:
                                del_req = urllib.request.Request(
                                    f"https://api.github.com/repos/{repo}/actions/runs/{run_id}",
                                    headers=headers,
                                    method="DELETE"
                                )
                                with urllib.request.urlopen(del_req, timeout=8) as del_resp:
                                    if del_resp.status in (204, 200):
                                        deleted_runs += 1
                                        logger.info(f"  🗑️ [{repo}] Deleted CI run ID {run_id} ({conclusion}) ({r.get('name')})")
                            else:
                                deleted_runs += 1
                    except Exception as de:
                        logger.debug(f"Error evaluating run {run_id}: {de}")

        except Exception as ge:
            logger.debug(f"GitHub Actions run pruning note for {repo}: {ge}")

    logger.info(f"✅ GitHub Actions Cleanup: {deleted_runs} old workflow runs purged.")
    return deleted_runs


# -----------------------------------------------------------------------------
# 5. Master Orchestrator
# -----------------------------------------------------------------------------
def run_all_sanitation(dry_run: bool = False):
    """Executes all sanitation tiers."""
    start_time = time.time()
    print("=" * 65)
    print("🧹 AAA AUTO — AUTONOMOUS SANITATION & MAINTENANCE ENGINE")
    print("=" * 65)
    if dry_run:
        logger.info("ℹ️ Running in DRY-RUN mode. No files or keys will be modified.")

    disk_stats = clean_local_disk(dry_run=dry_run)
    procs_killed = sanitize_local_processes(dry_run=dry_run)
    turso_purged = sanitize_turso_libsql(dry_run=dry_run)
    runs_deleted = clean_github_actions_history(max_age_days=7, dry_run=dry_run)

    duration = time.time() - start_time
    print("=" * 65)
    print(f"✨ CLEANUP SUMMARY (Completed in {duration:.2f}s)")
    print(f"  • Disk Space Freed:     {disk_stats['bytes_freed'] / 1024 / 1024:.2f} MB")
    print(f"  • Caches Removed:       {disk_stats['pycache_removed']} directories")
    print(f"  • Stale Backups Pruned: {disk_stats['backup_zips_pruned']} archives")
    print(f"  • Oversized Logs:       {disk_stats['logs_rotated']} rotated")
    print(f"  • Local Rogue Procs:    {procs_killed} terminated (0 phone load)")
    print(f"  • Turso SSOT Purged:    {turso_purged} defunct keys deleted")
    print(f"  • Stale CI/CD Runs:     {runs_deleted} runs purged")
    print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Autonomous Sanitation & Maintenance Engine for AAA AUTO")
    parser.add_argument("--dry-run", action="store_true", help="Preview cleanup actions without modifying files or keys")
    parser.add_argument("--disk-only", action="store_true", help="Run only local disk & cache cleanup")
    parser.add_argument("--cloud-only", action="store_true", help="Run only Turso SSOT & GitHub Actions cleanup")
    parser.add_argument("--proc-only", action="store_true", help="Run only process sanitation check")
    args = parser.parse_args()

    if args.disk_only:
        clean_local_disk(dry_run=args.dry_run)
    elif args.cloud_only:
        sanitize_turso_libsql(dry_run=args.dry_run)
        clean_github_actions_history(dry_run=args.dry_run)
    elif args.proc_only:
        sanitize_local_processes(dry_run=args.dry_run)
    else:
        run_all_sanitation(dry_run=args.dry_run)
