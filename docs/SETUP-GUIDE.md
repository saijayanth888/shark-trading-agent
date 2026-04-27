# Shark Trading Agent — Setup Guide

Complete guide to get Shark running as cloud routines on Claude Code.

---

## Prerequisites

1. **GitHub repo** — This repo must be pushed to GitHub
2. **Claude GitHub App** — Install on this repo at [github.com/apps/claude](https://github.com/apps/claude)
3. **Alpaca account** — Paper or live at [alpaca.markets](https://alpaca.markets)
4. **Perplexity API key** — For market research at [perplexity.ai](https://perplexity.ai)
5. **Anthropic API key** — For Claude brain at [console.anthropic.com](https://console.anthropic.com)
6. **Gmail App Password** — For email alerts at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)

---

## Step 1: Environment Variables

Every routine needs these environment variables set **directly on the routine config** (not in a `.env` file — cloud doesn't read those).

### Required Variables

| Variable | Example Value | Description |
|----------|--------------|-------------|
| `ALPACA_API_KEY` | `PK...` | Alpaca public API key |
| `ALPACA_SECRET_KEY` | `abc123...` | Alpaca secret key |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` | Paper trading URL |
| `PERPLEXITY_API_KEY` | `pplx-...` | Perplexity API key |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Anthropic API key |
| `GMAIL_APP_PASSWORD` | `abcd efgh ijkl mnop` | Gmail app-specific password |
| `NOTIFY_EMAIL` | `you@gmail.com` | Where alerts get sent |
| `NOTIFY_FROM_EMAIL` | `you@gmail.com` | Sending Gmail address |
| `TRADING_MODE` | `paper` | `paper` or `live` |

### Optional Variables (have sensible defaults)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Claude model to use |
| `RISK_PER_TRADE_PCT` | `1.0` | Risk per trade as % of portfolio |
| `ATR_STOP_MULTIPLE` | `2.0` | Stop distance in ATR units |
| `MAX_POSITION_PCT` | `20.0` | Max single position size % |
| `MAX_POSITIONS` | `6` | Max concurrent positions |
| `MAX_WEEKLY_TRADES` | `3` | Max trades per week |
| `MIN_CASH_BUFFER_PCT` | `0.15` | Min cash to keep in account |
| `KELLY_FRACTION` | `0.25` | Fractional Kelly sizing |
| `HARD_STOP_PCT` | `-0.07` | Hard stop loss (-7%) |
| `TIME_DECAY_DAYS` | `5` | Days before time decay exit |
| `CIRCUIT_BREAKER_PCT` | `0.15` | Portfolio loss to halt trading |

### Backtest-Only Variables (set only on the backtest routine)

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKTEST_CAPITAL` | `100000` | Starting capital |
| `BACKTEST_LOOKBACK_DAYS` | `365` | Historical window |
| `BACKTEST_MOMENTUM_MIN` | `40` | Min momentum score |
| `BACKTEST_RS_MIN` | `1.0` | Min relative strength |
| `BACKTEST_ATR_STOP_MULT` | `2.0` | ATR stop multiple |
| `BACKTEST_RISK_PCT` | `1.0` | Risk per trade % |
| `BACKTEST_SYMBOLS` | *(strategy watchlist)* | Comma-separated tickers |

---

## Step 2: Create Routines

Go to **Claude Code Cloud → Routines** and create 7 routines. For each one:

1. **Name** — Use the routine name from the table below
2. **Schedule** — Set the cron expression (timezone: `America/New_York`)
3. **Prompt** — Copy-paste the full content from the matching file in `routines/`
4. **Environment variables** — Add ALL required variables listed above
5. **Settings** — Enable **"Allow unrestricted branch pushes"**

### Routine Schedule

| # | Routine Name | Cron Expression | Time (ET) | Days | File to Copy |
|---|-------------|-----------------|-----------|------|-------------|
| 1 | Pre-Market Research | `0 6 * * 1-5` | 6:00 AM | Mon–Fri | `routines/pre-market.md` |
| 2 | Pre-Execute Validation | `45 9 * * 1-5` | 9:45 AM | Mon–Fri | `routines/pre-execute.md` |
| 3 | Market Open | `0 10 * * 1-5` | 10:00 AM | Mon–Fri | `routines/market-open.md` |
| 4 | Midday Scan | `0 13 * * 1-5` | 1:00 PM | Mon–Fri | `routines/midday.md` |
| 5 | Daily Summary | `15 16 * * 1-5` | 4:15 PM | Mon–Fri | `routines/daily-summary.md` |
| 6 | Weekly Review | `0 17 * * 5` | 5:00 PM | Friday only | `routines/weekly-review.md` |
| 7 | Weekly Backtest | `0 18 * * 5` | 6:00 PM | Friday only | `routines/backtest.md` |

---

## Step 3: Verify Setup

After creating all routines, verify:

- [ ] All 7 routines are created and scheduled
- [ ] Every routine has ALL required env vars set
- [ ] "Allow unrestricted branch pushes" is ON for each routine
- [ ] Claude GitHub App is installed on this repo
- [ ] Run one routine manually to test (try `pre-market` first — it's the safest)

---

## How It Works

```
┌─────────────┐    ┌──────────────┐    ┌────────────────┐
│  Cloud Cron  │───>│  pip install │───>│  shark/run.py  │
│  (schedule)  │    │  packages    │    │  <phase>       │
└─────────────┘    └──────────────┘    └────────────────┘
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                   ┌──────────┐      ┌──────────────┐    ┌──────────┐
                   │ git pull │      │ Phase Logic   │    │ git push │
                   │ latest   │      │ + API calls   │    │ results  │
                   └──────────┘      └──────────────┘    └──────────┘
                                          │
                                          ▼
                                   ┌──────────────┐
                                   │ Email alert  │
                                   │ (on error)   │
                                   └──────────────┘
```

Each routine runs this sequence:
1. **pip install** — Installs Python dependencies from `requirements.txt`
2. **run.py** — Executes the phase (pre-market, market-open, etc.)
3. **Phase logic** — Pulls data, runs analysis, executes trades, etc.
4. **Git push** — Commits results back to the repo
5. **Email** — Sends alerts on errors or daily summaries

---

## Troubleshooting

### "No module named pandas" / "No module named numpy"
The pip install step failed silently. The routines now include `--break-system-packages` to fix this on Python 3.12+ cloud sandboxes. Make sure you're using the latest routine prompts from `routines/`.

### Routine exits but no trades happen
Check `memory/market-open-analysis.json` — if market regime is BEAR or no candidates pass filters, this is expected behavior (capital preservation).

### Email alerts not sending
Verify `GMAIL_APP_PASSWORD` is a 16-character app password (not your regular password). Check that `NOTIFY_FROM_EMAIL` matches the Gmail account that generated the app password.

### Git push fails
Ensure "Allow unrestricted branch pushes" is enabled on the routine. The Claude GitHub App must have write access to this repo.
