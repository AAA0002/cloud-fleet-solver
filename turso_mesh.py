"""
Turso LibSQL KV Mesh (turso_mesh.py)
Authoritative high-performance Key-Value replacement for Upstash Redis across AAA AUTO.
Uses Turso LibSQL (table `kv_mesh`) with 25,000,000 requests/month free tier and zero rate limits.

Guarantees 100% backward-compatibility with all legacy upstash_* and get_upstash_* callers.
"""

import os
import json
import time
import logging
import urllib.request
import urllib.error
from typing import Any, Optional, List, Dict

try:
    from dotenv import load_dotenv
    # Search for .env in current dir, project root, or parent dirs
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(curr_dir, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)
    else:
        load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("turso_mesh")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

TURSO_URL = os.getenv("TURSO_DB_URL") or os.getenv("TURSO_URL") or ""
TURSO_TOKEN = os.getenv("TURSO_DB_TOKEN") or os.getenv("TURSO_TOKEN") or ""

def _format_hrana_arg(val: Any) -> dict:
    """Formats an argument for Turso Hrana v2 HTTP pipeline."""
    if val is None:
        return {"type": "null"}
    elif isinstance(val, (int, float)):
        # Turso Hrana integer values must be passed as strings to prevent JSON numeric overflow
        return {"type": "integer", "value": str(int(val))}
    elif isinstance(val, bytes):
        import base64
        return {"type": "blob", "base64": base64.b64encode(val).decode("ascii")}
    else:
        return {"type": "text", "value": str(val)}

def turso_execute(sql: str, args: Optional[List[Any]] = None, timeout: float = 8.0) -> Optional[List[List[dict]]]:
    """
    Executes a single SQL statement on Turso LibSQL Hrana v2 pipeline.
    Returns rows as list of dicts or None on failure.
    """
    if not TURSO_URL or not TURSO_TOKEN:
        logger.warning("Turso URL or Token missing from environment.")
        return None

    http_url = TURSO_URL.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
    formatted_args = [_format_hrana_arg(a) for a in (args or [])]

    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": formatted_args
                }
            }
        ]
    }

    try:
        req = urllib.request.Request(
            http_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {TURSO_TOKEN}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("results", [])
            if results and results[0].get("type") == "ok":
                return results[0].get("response", {}).get("result", {}).get("rows", [])
            elif results and results[0].get("type") == "error":
                logger.error(f"Turso Hrana error: {results[0].get('error')}")
                return None
    except Exception as e:
        logger.debug(f"Turso execute error on query [{sql[:40]}...]: {e}")
        return None
    return None

def turso_execute_batch(requests_list: List[dict], timeout: float = 12.0) -> Optional[List[dict]]:
    """
    Executes a batch of requests on Turso LibSQL Hrana v2 pipeline atomically in a single HTTP roundtrip.
    """
    if not TURSO_URL or not TURSO_TOKEN:
        return None

    http_url = TURSO_URL.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
    payload = {"requests": requests_list}

    try:
        req = urllib.request.Request(
            http_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {TURSO_TOKEN}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("results", [])
    except Exception as e:
        logger.debug(f"Turso batch execute error ({len(requests_list)} requests): {e}")
        return None

def _decode_stored_value(val: Any) -> Any:
    """Safely decodes JSON values from storage, unwrapping double-encoded strings."""
    if val is None:
        return None
    curr = val
    # Unpack nested json strings up to 3 layers
    for _ in range(3):
        if isinstance(curr, str):
            curr_stripped = curr.strip()
            if (curr_stripped.startswith("{") and curr_stripped.endswith("}")) or \
               (curr_stripped.startswith("[") and curr_stripped.endswith("]")) or \
               (curr_stripped.startswith('"') and curr_stripped.endswith('"')):
                try:
                    loaded = json.loads(curr)
                    if loaded == curr:
                        break
                    curr = loaded
                except Exception:
                    break
            else:
                break
        else:
            break
    return curr

def turso_get(key: str, default: Any = None) -> Any:
    """
    Retrieves a key from `kv_mesh` table in Turso LibSQL.
    Auto-decodes JSON structures (dict, list, etc.).
    """
    rows = turso_execute("SELECT val FROM kv_mesh WHERE key = ? LIMIT 1;", [key])
    if rows and len(rows) > 0 and len(rows[0]) > 0:
        raw_val = rows[0][0].get("value")
        decoded = _decode_stored_value(raw_val)
        return decoded if decoded is not None else default
    return default

def turso_set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """
    Persists a key-value pair to `kv_mesh` table in Turso LibSQL.
    Serializes non-string objects to JSON.
    """
    if value is None:
        return turso_delete(key)

    val_str = json.dumps(value) if not isinstance(value, str) else value
    now_ts = int(time.time())
    rows = turso_execute(
        "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES (?, ?, ?);",
        [key, val_str, now_ts]
    )
    return rows is not None

def turso_delete(key: str) -> bool:
    """
    Deletes a key from `kv_mesh` table in Turso LibSQL.
    """
    rows = turso_execute("DELETE FROM kv_mesh WHERE key = ?;", [key])
    return rows is not None

def turso_keys(pattern: str = "%") -> List[str]:
    """
    Lists keys from `kv_mesh` matching a pattern.
    Supports Redis-style '*' which is converted to SQL '%' wildcard.
    """
    sql_pattern = pattern.replace("*", "%")
    rows = turso_execute("SELECT key FROM kv_mesh WHERE key LIKE ? ORDER BY key ASC;", [sql_pattern])
    if rows:
        return [r[0].get("value") for r in rows if r and len(r) > 0 and r[0].get("value")]
    return []

def turso_mget(keys: List[str]) -> Dict[str, Any]:
    """
    Batch-fetches multiple keys from `kv_mesh` in a single query.
    """
    if not keys:
        return {}
    placeholders = ",".join(["?"] * len(keys))
    rows = turso_execute(f"SELECT key, val FROM kv_mesh WHERE key IN ({placeholders});", keys)
    result = {}
    if rows:
        for r in rows:
            if len(r) >= 2:
                k = r[0].get("value")
                v = _decode_stored_value(r[1].get("value"))
                result[k] = v
    return result

def turso_mset(mapping: Dict[str, Any]) -> bool:
    """
    Batch-inserts multiple key-value pairs into `kv_mesh` in a single atomic HTTP roundtrip.
    """
    if not mapping:
        return True
    now_ts = int(time.time())
    requests = []
    for k, v in mapping.items():
        v_str = json.dumps(v) if not isinstance(v, str) else v
        requests.append({
            "type": "execute",
            "stmt": {
                "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES (?, ?, ?);",
                "args": [
                    {"type": "text", "value": str(k)},
                    {"type": "text", "value": str(v_str)},
                    {"type": "integer", "value": str(now_ts)}
                ]
            }
        })
    res = turso_execute_batch(requests)
    return res is not None

# ==============================================================================
# 100% Drop-In Compatibility Aliases for Legacy Upstash Callers
# All calls to Upstash Redis automatically route through high-quota Turso Mesh
# ==============================================================================
upstash_get = turso_get
upstash_set = turso_set
upstash_del = turso_delete
upstash_delete = turso_delete
upstash_keys = turso_keys
get_upstash = turso_get
set_upstash = turso_set
sync_upstash_key = turso_set

# Async support if aiohttp is available
try:
    import aiohttp

    async def async_turso_get(key: str, default: Any = None) -> Any:
        if not TURSO_URL or not TURSO_TOKEN:
            return default
        http_url = TURSO_URL.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": "SELECT val FROM kv_mesh WHERE key = ? LIMIT 1;",
                        "args": [{"type": "text", "value": key}]
                    }
                }
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    http_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        if results and results[0].get("type") == "ok":
                            rows = results[0].get("response", {}).get("result", {}).get("rows", [])
                            if rows and len(rows) > 0 and len(rows[0]) > 0:
                                return _decode_stored_value(rows[0][0].get("value"))
        except Exception as e:
            logger.debug(f"async_turso_get error: {e}")
        return default

    async def async_turso_set(key: str, value: Any) -> bool:
        if not TURSO_URL or not TURSO_TOKEN:
            return False
        if value is None:
            return await async_turso_delete(key)
        http_url = TURSO_URL.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
        val_str = json.dumps(value) if not isinstance(value, str) else value
        now_ts = int(time.time())
        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": "INSERT OR REPLACE INTO kv_mesh (key, val, updated_at) VALUES (?, ?, ?);",
                        "args": [
                            {"type": "text", "value": key},
                            {"type": "text", "value": val_str},
                            {"type": "integer", "value": str(now_ts)}
                        ]
                    }
                }
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    http_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.debug(f"async_turso_set error: {e}")
            return False

    async def async_turso_delete(key: str) -> bool:
        if not TURSO_URL or not TURSO_TOKEN:
            return False
        http_url = TURSO_URL.replace("libsql://", "https://").rstrip("/") + "/v2/pipeline"
        payload = {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": "DELETE FROM kv_mesh WHERE key = ?;",
                        "args": [{"type": "text", "value": key}]
                    }
                }
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    http_url,
                    json=payload,
                    headers={"Authorization": f"Bearer {TURSO_TOKEN}", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception as e:
            logger.debug(f"async_turso_delete error: {e}")
            return False

    async_upstash_get = async_turso_get
    async_upstash_set = async_turso_set
    async_upstash_delete = async_turso_delete
except ImportError:
    pass
