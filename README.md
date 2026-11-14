# Cloud Fleet Solver

Autonomous, headless cloud worker running on GitHub Actions for multi-bot fleet operations and stealth challenge solving.

## Overview
This repository functions as a decoupled headless solver. It executes compute-heavy and browser-based challenges (such as Cloudflare Turnstile CAPTCHA solving with SeleniumBase UC mode and arcade minigame game-engine solvers) in the cloud with zero phone load.

## Architecture
- **Runner**: GitHub Actions (Ubuntu 24.04, Python 3.12, Google Chrome, Xvfb).
- **Decoupled State Relay**:
  - Session tokens and fleet targets are retrieved dynamically from Upstash Serverless Redis via authenticated HTTPS REST (`fleet:tokens:{uid}` / `miniapp_tokens`).
  - Solved authentication tokens and passes (`victors:pass:{uid}`) are written directly back to Upstash Redis with a 24-hour TTL.
  - Zero plaintext credentials, zero account sessions, and zero private keys are stored in this repository.
- **Security**:
  - All runtime endpoints and bearer tokens are delivered securely via GitHub Repository Secrets (`UPSTASH_URL`, `UPSTASH_TOKEN`, `ADMIN_BOT_TOKEN`, `OWNER_ID`).
  - Pull requests from forks are strictly prohibited from accessing repository secrets.

## Workflows
- **Victor's Company Cloud Turnstile Solver & Referral Pipeline**:
  - File: `.github/workflows/victors_sb_solver.yml`
  - Schedule: Every 6 hours (`0 */6 * * *`) + on-demand manual trigger (`workflow_dispatch`).
