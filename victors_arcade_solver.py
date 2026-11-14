#!/usr/bin/env python3
"""
victors_arcade_solver.py
Autonomous Solver for Victor's Company (@Victors_Company_bot) Deep Mine Arcade Minigame (/api/arcade/mine/*).

Mathematical & Game-Engine Strategy:
- Tool: Shovel (Cost: 25 VIC, Energy: 25, Hearts: 3, Power: 1).
- Capital Guard: Requires min_balance (default 50.0 VIC) to prevent coin depletion on volatile runs.
- Safe Rows: Rows 0 and 1 have Ue.safeRows = 2 (strictly ZERO hazards, 0% bust chance).
- Smart Layer 1 Probing: When hearts == 3 and energy >= 4, probes accessible Layer 1 (Row 2) soft dirt tiles.
- Zero Bust Guarantee: Immediately cashes out (/api/arcade/mine/end) if hearts <= 2 OR bag >= 25.0 VIC (profit secured).
- Energy Refund: Unused energy is refunded at 60% upon safe cashout, protecting unspent capital.
"""

import os
import sys
import json
import time
import asyncio
import aiohttp
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("victors_arcade")

VICTORS_API = "https://server.victors.company/api"
VICTORS_ORIGIN = "https://app.victors.company"
MASTER_UID = "6727787768"
ENABLE_NEW_PAID_EXPEDITIONS = False  # Strict Profit Guard: Disabled paid expeditions (25 VIC Shovel) to preserve 100% capital
MIN_BALANCE_TO_PLAY = 100.0  # Only enter new expeditions if explicitly enabled and balance >= 100 VIC


async def solve_deep_mine_expeditions(
    session: aiohttp.ClientSession,
    headers: dict,
    account_name: str,
    max_runs: int = 10,
    min_balance: float = MIN_BALANCE_TO_PLAY
) -> dict:
    """
    Executes safe, EV-optimized Deep Mine expeditions for an authenticated account.
    Returns summary of games played and balance changes.
    """
    stats = {"runs_played": 0, "net_vic": 0.0, "total_paid": 0.0, "final_balance": None}

    # 0. STRICT MASTER ACCOUNT GUARD: Never play minigames or spend coins on Master Account
    if str(MASTER_UID) in str(account_name) or "Arif Hossain" in str(account_name):
        logger.info(f"[{account_name}] 👑 Master Account detected: Skipping Deep Mine minigame. Preserving 100% capital for treasury.")
        return stats

    # 1. Fetch current user state & balance
    try:
        async with session.get(f"{VICTORS_API}/me", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status != 200:
                logger.warning(f"[{account_name}] /me returned HTTP {resp.status}")
                return stats
            me_data = await resp.json()
            st = me_data.get("state") or {}
            user = st.get("user") or {}
            balance = float(user.get("inAppBalance", 0.0) or user.get("balance", 0.0) or 0.0)
            stats["initial_balance"] = balance
            stats["final_balance"] = balance
    except Exception as e:
        logger.error(f"[{account_name}] Failed to fetch /me: {e}")
        return stats

    for run_idx in range(max_runs):
        # 2. Check arcade status
        try:
            async with session.get(f"{VICTORS_API}/arcade", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    break
                arc_data = await resp.json()
        except Exception as e:
            logger.error(f"[{account_name}] Failed to check arcade: {e}")
            break

        active_run = arc_data.get("mine")
        games = {g.get("id"): g for g in arc_data.get("games", []) if isinstance(g, dict)}
        mine_game = games.get("mine")
        plays_left = mine_game.get("playsLeft", 0) if mine_game else 0

        # Check eligibility
        if not active_run:
            if not ENABLE_NEW_PAID_EXPEDITIONS:
                logger.info(f"[{account_name}] 🛡️ Profit Guard: New paid expeditions disabled (25 VIC Shovel is negative EV). Preserving 100% balance.")
                break
            if plays_left <= 0:
                logger.info(f"[{account_name}] 🎮 Deep Mine: No plays left today ({plays_left}/10).")
                break
            if balance < min_balance:
                logger.info(f"[{account_name}] 🎮 Deep Mine: Balance ({balance:.2f} VIC) < safe threshold ({min_balance} VIC). Preserving capital.")
                break

            # Start new expedition with shovel
            try:
                start_payload = {"tool": "shovel", "items": {}}
                async with session.post(f"{VICTORS_API}/arcade/mine/start", json=start_payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    start_data = await resp.json()
                    if resp.status != 200 or not start_data.get("success"):
                        err_msg = start_data.get("error", f"HTTP {resp.status}")
                        logger.warning(f"[{account_name}] 🎮 Deep Mine start failed: {err_msg}")
                        break
                    active_run = start_data.get("run")
                    balance = max(0.0, balance - 25.0)
                    logger.info(f"[{account_name}] 🎮 Expedition #{run_idx + 1} started! Shovel equipped (-25 VIC).")
            except Exception as e:
                logger.error(f"[{account_name}] Error starting expedition: {e}")
                break

        if not active_run or active_run.get("status") != "active":
            logger.warning(f"[{account_name}] No active expedition to play.")
            break

        # 3. Autonomous Digging Loop with Anti-Bust & Profit Lock
        run_state = active_run
        grid_w = 9
        dig_count = 0

        while run_state.get("status") == "active":
            open_str = run_state.get("open", "0" * 360)
            rock_str = run_state.get("rock", "0" * 360)
            hint_str = run_state.get("hint", "0" * 360)
            current_energy = run_state.get("energy", 0)
            current_hearts = run_state.get("hearts", 3)
            current_bag = run_state.get("bag", 0.0)

            # PROFIT LOCK: If bag already broke even or gained profit (>= 25 VIC), cash out immediately!
            if current_bag >= 25.0:
                logger.info(f"[{account_name}] 🎯 Profit target reached! Bag: {current_bag:.1f} VIC >= 25 VIC. Locking in profit.")
                break

            # SAFETY LOCK: If hearts dropped below 3, cash out immediately before any bust risk!
            if current_hearts <= 2:
                logger.info(f"[{account_name}] 🛡️ Heart safety trigger: Hearts = {current_hearts}/3. Cashing out safely to guarantee zero bust.")
                break

            # Find reachable tiles in safe rows (y=0, y=1) and careful row 2
            candidates = []
            max_depth = 2 if current_hearts == 3 else 1  # Only probe row 2 if full hearts

            for y in range(max_depth + 1):
                for x in range(grid_w):
                    idx = y * grid_w + x
                    if idx >= len(open_str) or open_str[idx] == "1":
                        continue  # Already opened

                    # Check reachability:
                    # Row 0 is directly reachable from top.
                    # Row 1+ is reachable if tile directly above (x, y-1) is already opened ("1").
                    is_reachable = (y == 0) or (open_str[(y - 1) * grid_w + x] == "1")
                    if not is_reachable:
                        continue

                    # Check energy cost: soft dirt costs 1, rock costs 2
                    is_rock = (rock_str[idx] == "1") if idx < len(rock_str) else False
                    cost = 2 if is_rock else 1
                    if cost > current_energy:
                        continue

                    # Sparkle hint
                    hint_char = hint_str[idx] if idx < len(hint_str) else "0"
                    hint_val = int(hint_char) if hint_char.isdigit() else 0
                    has_sparkle = bool(hint_val & 1)

                    # Dynamic score priority:
                    # Safe row sparkle soft dirt: 120
                    # Safe row plain soft dirt: 80
                    # Safe row sparkle rock: 60
                    # Layer 1 (row 2) sparkle soft dirt: 50 (high EV opportunity)
                    # Safe row plain rock: 30
                    # Layer 1 plain soft dirt: 20
                    # Never dig rocks in row 2
                    if y < 2:
                        priority = (120 if has_sparkle else 80) - (50 if is_rock else 0)
                    else:
                        if is_rock:
                            continue  # Skip boulders in hazard layers
                        priority = 50 if has_sparkle else 20

                    candidates.append({
                        "x": x,
                        "y": y,
                        "cost": cost,
                        "priority": priority,
                        "sparkle": has_sparkle,
                        "rock": is_rock
                    })

            if not candidates:
                logger.info(f"[{account_name}] 🎮 No further viable tiles within energy/safety bounds. Cashing out bag.")
                break

            # Pick highest priority candidate
            candidates.sort(key=lambda c: c["priority"], reverse=True)
            chosen = candidates[0]

            # Dig tile
            try:
                await asyncio.sleep(0.15)
                dig_payload = {"x": chosen["x"], "y": chosen["y"]}
                async with session.post(f"{VICTORS_API}/arcade/mine/dig", json=dig_payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    dig_res = await resp.json()
                    if resp.status != 200 or not dig_res.get("success"):
                        logger.warning(f"[{account_name}] Dig failed at ({chosen['x']}, {chosen['y']}): {dig_res.get('error')}")
                        break
                    dig_count += 1
                    run_state = dig_res.get("run") or run_state
                    cell = dig_res.get("cell", {})
                    found = cell.get("found")
                    if found and isinstance(found, dict) and found.get("coins", 0) > 0:
                        logger.info(f"[{account_name}] 💰 Found loot at ({chosen['x']},{chosen['y']})! +{found['coins']} VIC. Bag now: {run_state.get('bag')} VIC")

                    if cell.get("hazard"):
                        logger.warning(f"[{account_name}] ⚠️ Hit hazard at ({chosen['x']},{chosen['y']})! Hearts remaining: {run_state.get('hearts')}")

                    # If server concluded the game automatically (e.g. energy depleted or cap reached)
                    if dig_res.get("result"):
                        res_obj = dig_res.get("result", {})
                        paid = res_obj.get("paid", run_state.get("bag", 0.0))
                        refund = res_obj.get("refund", 0.0)
                        net = res_obj.get("net", paid - 25.0)
                        st = dig_res.get("state") or {}
                        user = st.get("user") or {}
                        balance = float(user.get("inAppBalance", balance + paid) or user.get("balance", balance + paid) or 0.0)
                        stats["runs_played"] += 1
                        stats["total_paid"] += paid
                        stats["net_vic"] += net
                        stats["final_balance"] = balance
                        logger.info(f"[{account_name}] 🏆 Expedition #{run_idx + 1} auto-concluded: {dig_count} digs | Bag: {run_state.get('bag', 0):.1f} | Refund: {refund:.1f} | Paid: {paid:.1f} VIC (Net: {net:+.1f} VIC) | New Balance: {balance:.2f} VIC")
                        run_state["status"] = "ended"
                        break
            except Exception as e:
                logger.error(f"[{account_name}] Dig network error: {e}")
                break

        # 4. Cash Out safely with /api/arcade/mine/end if run is still active
        if run_state.get("status") == "active":
            try:
                async with session.post(f"{VICTORS_API}/arcade/mine/end", json={}, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    end_res = await resp.json()
                    if resp.status == 200 and end_res.get("success"):
                        res_obj = end_res.get("result", {})
                        paid = res_obj.get("paid", run_state.get("bag", 0.0))
                        refund = res_obj.get("refund", 0.0)
                        net = res_obj.get("net", paid - 25.0)
                        st = end_res.get("state") or {}
                        user = st.get("user") or {}
                        balance = float(user.get("inAppBalance", balance + paid) or user.get("balance", balance + paid) or 0.0)
                        stats["runs_played"] += 1
                        stats["total_paid"] += paid
                        stats["net_vic"] += net
                        stats["final_balance"] = balance
                        logger.info(f"[{account_name}] 🏆 Expedition #{run_idx + 1} cashed out: {dig_count} digs | Bag: {run_state.get('bag', 0):.1f} | Refund: {refund:.1f} | Paid: {paid:.1f} VIC (Net: {net:+.1f} VIC) | New Balance: {balance:.2f} VIC")
                    else:
                        logger.warning(f"[{account_name}] Cash out response: {end_res}")
            except Exception as e:
                logger.error(f"[{account_name}] Error cashing out: {e}")

        # Short pause between expeditions
        await asyncio.sleep(0.5)

    return stats
