# Shark Agent — Cloud Routines

Five scheduled routines fit the cloud subscription limit. Configure in Claude Code Cloud → Routines.

| # | Routine | File | Cron (America/New_York) | Time ET | Phases Included |
|---|---------|------|--------------------------|---------|-----------------|
| 1 | Pre-market research | pre-market.md | `0 6 * * 1-5` | 6:00 AM Mon-Fri | pre-market |
| 2 | Trading (validate + execute) | trading.md | `45 9 * * 1-5` | 9:45 AM Mon-Fri | pre-execute → market-open |
| 3 | Midday scan | midday.md | `0 13 * * 1-5` | 1:00 PM Mon-Fri | midday |
| 4 | End-of-day | eod.md | `15 16 * * 1-5` | 4:15 PM Mon-Fri | daily-summary → kb-update |
| 5 | Weekly (review + backtest + KB) | weekly.md | `0 17 * * 5` | 5:00 PM Fri | weekly-review → backtest → kb-refresh |

### Consolidation Notes

Previously 9 routines; consolidated to 5 by merging sequential phases:

- **trading.md** = pre-execute (validate candidates) → market-open (3-step: prepare → analyze → execute)
- **eod.md** = daily-summary (EOD snapshot + email + outcome resolution) → kb-update (append today's bars)
- **weekly.md** = weekly-review → backtest → kb-refresh (full pattern recompute, moved from Sunday to Friday evening since EOD routine already appended Friday's bars)

### KB (Knowledge Base) Routines

The KB is a self-contained historical intelligence store in `kb/` that lets all
trading routines fast-load cached data and apply rule-based historical edge
scoring without external dependencies.

- **kb-refresh** (Friday 5 PM, inside weekly routine) — incremental weekly
  rebuild: full pull only for new/stale tickers, delta pulls for fresh ones.
  Bars are fetched with `Adjustment.ALL` (split + dividend adjusted — required
  for correct sector and regime math). Auto-detects legacy unadjusted KBs and
  forces a one-time full refresh to upgrade.
  Recomputes all statistical patterns:
    - `calendar_effects.json` (day-of-week, FOMC drift)
    - `sector_rotation.json`  (6m sector momentum, top_3 / bottom_3)
    - `regime_outcomes.json`  (per-ticker stats by SPY regime)
    - `ticker_base_rates.json` (per-ticker setup win rates from kb/trades/)
    - `anti_patterns.json`     (ticker+setup combos that historically fail)
  Also prunes stale PEAD setup files (>90 days, no recorded outcomes).
  Steady-state runtime: ~3-5 min.

- **kb-update** (Mon-Fri 4:15 PM, inside eod routine) — light daily increment:
  appends today's bar to each ticker file. ~1-2 min runtime. Patterns are NOT
  recomputed daily (that runs only on Fridays for stability).

### Strategy Overlays (read by pre-market scoring)

- **Sector Rotation** (Asness 1997, Faber 2007): tickers in a top-3 6m-momentum
  sector get +3 score bonus; bottom-3 sectors get -5 penalty. Reads
  `kb/patterns/sector_rotation.json`.
- **PEAD** (Bernard-Thomas 1989): detects earnings-like gaps (>4% with >2x
  volume) from price data and applies a time-decaying score bonus across the
  60-day post-earnings drift window. Active setups persisted to
  `kb/earnings/{symbol}_{event_date}.json`. Outcomes are recorded back to that
  file when the trade closes.
- **Anti-patterns**: hard-rejects tickers matching documented losing patterns.
- **Base rate boost/penalty**: ±4 score adjustment when historical win rate in
  the current regime is consistently above 65% or below 30%.

Strategy attribution is preserved end-to-end: each open trade is tagged with
`setup_tag` in `memory/open-trades.json`; closed trades carry the tag to
`kb/trades/`; the weekly backtest report breaks performance down by tag.

Both KB routines auto-commit + push the `kb/` folder to `main` so all subsequent
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

Cloud sandboxes block SMTP (port 587), so emails use the **Gmail REST API** over HTTPS (port 443).
Run `python scripts/gmail_oauth_setup.py` once locally to get the OAuth tokens, then set:

- `GMAIL_OAUTH_CLIENT_ID` — Google Cloud OAuth2 client ID
- `GMAIL_OAUTH_CLIENT_SECRET` — Google Cloud OAuth2 client secret
- `GMAIL_OAUTH_REFRESH_TOKEN` — long-lived refresh token (does not expire unless revoked)
- `NOTIFY_EMAIL` — destination address (e.g. sharkwaveai@gmail.com)
- `NOTIFY_FROM_EMAIL` — sending Gmail address (must match the account that authorized OAuth)

> **Fallback (local dev only):** `GMAIL_APP_PASSWORD` works via SMTP when port 587 is open.
> If all transports fail, alerts are written to `memory/SIGNAL-LOG.md` as a last resort.

### Trading Mode (required)
- `TRADING_MODE` — `paper` or `live`

### Paper-Mode Overrides (optional, has defaults — only apply when `TRADING_MODE=paper`)

In paper mode, the agent allows limited trading in BEAR regimes and bypasses macro blocks
so the full pipeline can be tested. Set `PAPER_BEAR_OVERRIDE=false` to disable.

- `PAPER_BEAR_OVERRIDE` — allow trades in BEAR regimes (default: `true`)
- `PAPER_MACRO_BYPASS` — bypass CRITICAL/HIGH macro blocks (default: `true`)
- `PAPER_BEAR_MAX_TRADES` — max new trades per day in BEAR override (default: `1`)
- `PAPER_BEAR_SIZE_MULT` — position size multiplier, 0.5 = half size (default: `0.5`)
- `PAPER_BEAR_CONFIDENCE` — min confidence threshold (default: `0.85`)
- `PAPER_BEAR_MIN_SCORE` — pre-market min score in BEAR regimes (default: `3`)

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
