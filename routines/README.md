# Shark Agent — Cloud Routines

Nine scheduled routines run on various cadences. Configure in Claude Code Cloud → Routines.

| Routine | File | Cron (America/New_York) | Time ET |
|---------|------|--------------------------|---------|
| Pre-market research | pre-market.md | `0 6 * * 1-5` | 6:00 AM Mon-Fri |
| Pre-execute validation | pre-execute.md | `45 9 * * 1-5` | 9:45 AM Mon-Fri |
| Market-open execution | market-open.md | `0 10 * * 1-5` | 10:00 AM Mon-Fri |
| Midday scan | midday.md | `0 13 * * 1-5` | 1:00 PM Mon-Fri |
| Daily summary | daily-summary.md | `15 16 * * 1-5` | 4:15 PM Mon-Fri |
| **KB daily update** | **kb-update.md** | **`30 17 * * 1-5`** | **5:30 PM Mon-Fri** |
| Weekly review | weekly-review.md | `0 17 * * 5` | 5:00 PM Fri |
| Weekly backtest | backtest.md | `0 18 * * 5` | 6:00 PM Fri |
| **KB weekly refresh** | **kb-refresh.md** | **`0 8 * * 0`** | **8:00 AM Sun** |

### KB (Knowledge Base) Routines

The KB is a self-contained historical intelligence store in `kb/` that lets all
trading routines fast-load cached data instead of hitting APIs every time.

- **kb-refresh** (Sunday 8 AM) — heavy weekly rebuild: pulls 2 years of daily bars
  for the entire S&P 500 + sector ETFs, recomputes statistical patterns
  (calendar effects, sector rotation, regime outcomes, anti-patterns).
  Total runtime: ~10-15 min. **Bootstrap once with `python scripts/seed_kb.py --commit`.**

- **kb-update** (Mon-Fri 5:30 PM) — light daily increment: appends today's bar to
  each ticker file. ~1-2 min runtime. Patterns are NOT recomputed daily
  (that runs only on Sundays for stability).

Both routines auto-commit + push the `kb/` folder to `main` so all subsequent
trading routines see the latest data.

## Critical Setup
1. Install the Claude GitHub App on this repo
2. Enable "Allow unrestricted branch pushes" on EVERY routine
3. Each routine prompt uses `git push origin HEAD:main` (not `git push origin main`)
4. Set ALL env vars on each routine — do NOT use a .env file in cloud

## Required Env Vars (set on each routine)

### Trading APIs (required)
- `ALPACA_API_KEY` — Alpaca public key
- `ALPACA_SECRET_KEY` — Alpaca secret key
- `ALPACA_BASE_URL` — `https://paper-api.alpaca.markets` (paper) or `https://api.alpaca.markets` (live)
- `PERPLEXITY_API_KEY` — Perplexity API key

> **Note: `ANTHROPIC_API_KEY` is NOT needed for cloud routines.** Claude IS the brain — the routine prompt itself runs on Claude infrastructure. The Python phases use rule-based analysis when no API key is set, which is the cloud default. Only set `ANTHROPIC_API_KEY` for local dev when running `_run_full` mode (which calls Anthropic directly for combined_analyst / decision_arbiter / trade_reviewer).

### Email Notifications (required)
- `GMAIL_APP_PASSWORD` — Gmail app-specific password (NOT your regular Gmail password)
- `NOTIFY_EMAIL` — destination address (e.g. sharkwaveai@gmail.com)
- `NOTIFY_FROM_EMAIL` — sending address (must match the Gmail account for GMAIL_APP_PASSWORD)

### Trading Mode (required)
- `TRADING_MODE` — `paper` or `live`

### AI Model (optional, has defaults)
- `CLAUDE_MODEL` — Claude model ID (default: `claude-sonnet-4-6`)

### Position Sizing (optional, has defaults)
- `RISK_PER_TRADE_PCT` — base risk per trade as % of portfolio (default: `1.0`)
- `ATR_STOP_MULTIPLE` — stop distance in ATR units (default: `2.0`)
- `MAX_POSITION_PCT` — hard cap on single position size (default: `20.0`)
- `KELLY_FRACTION` — fractional Kelly sizing fraction (default: `0.25`)

### Exit Management (optional, has defaults)
- `HARD_STOP_PCT` — hard stop loss threshold (default: `-0.07` = -7%)
- `TIME_DECAY_DAYS` — days held before time decay triggers (default: `5`)
- `TIME_DECAY_MIN_MOVE_PCT` — minimum move % to avoid time decay exit (default: `2.0`)
- `VOL_EXPANSION_THRESHOLD` — ATR expansion ratio to trigger vol exit (default: `2.0`)

### Backtest Parameters (optional, has defaults — set only on backtest routine)
- `BACKTEST_CAPITAL` — starting capital for simulation (default: `100000`)
- `BACKTEST_LOOKBACK_DAYS` — historical window in days (default: `365`)
- `BACKTEST_MOMENTUM_MIN` — minimum momentum score for entry (default: `40`)
- `BACKTEST_RS_MIN` — minimum RS composite for entry (default: `1.0`)
- `BACKTEST_ATR_STOP_MULT` — ATR stop multiple in simulation (default: `2.0`)
- `BACKTEST_RISK_PCT` — risk per trade % in simulation (default: `1.0`)
- `BACKTEST_SYMBOLS` — comma-separated tickers to test (default: strategy watchlist)

## How Routines Work
Each routine prompt runs a single command:
```bash
cd /repo && python shark/run.py <phase>
```
The Python engine handles: git pull → context briefing → phase logic → email → git commit + push.
On non-zero exit, the routine sends an error alert via `scripts/notify.sh` and stops.
