# Shark Trading Agent

An autonomous AI trading agent that manages a US stock portfolio 24/7 using Claude, Alpaca Markets, and Perplexity. Built on the Claude Code cloud routines architecture — every trade decision, research note, and performance review is committed to Git as immutable memory.

**Status:** Paper trading | **Mode:** Shark Signals (trading + paid signals subscription)

---

## What It Does

Shark runs five scheduled routines every market day, fully unattended:

| Time (ET) | Routine | Action |
|---|---|---|
| 6:00 AM | Pre-market research | Scans watchlist via Perplexity, scores catalysts, writes top candidates to Git |
| 9:45 AM | Pre-execute validation | Re-validates top candidates against first 15 min of trading before committing |
| 10:00 AM | Market open execution | Multi-agent debate (bull → bear → risk → arbiter), places trades + trailing stops |
| 1:00 PM | Midday scan | Cuts hard losers at -7%, tightens stops on winners, thesis-break exits |
| 4:15 PM | Daily summary | EOD portfolio snapshot, circuit breaker check, email digest, Git commit |
| 5:00 PM (Fri) | Weekly review | Grades performance vs S&P, notes strategy mutations, updates memory |

---

## Architecture

```
shark-trading-agent/
├── shark/
│   ├── data/
│   │   ├── alpaca_data.py     — Alpaca REST client (account, positions, bars, quotes)
│   │   ├── perplexity.py      — Perplexity Sonar Pro market intelligence
│   │   └── technical.py       — Pandas indicators (RSI/Wilder, SMA20/50, volume ratio)
│   ├── agents/
│   │   ├── analyst_bull.py    — Bullish thesis generator (Claude claude-sonnet-4-6, cached system prompt)
│   │   ├── analyst_bear.py    — Counter-thesis generator (identifies risks and invalidation signals)
│   │   ├── risk_manager.py    — Python hard-rule pre-filter (pure if/else, no AI)
│   │   └── decision_arbiter.py — Final decision agent; short-circuits to NO_TRADE if risk fails
│   ├── execution/
│   │   ├── orders.py          — Place, cancel, and close Alpaca orders
│   │   ├── stops.py           — Three-tier trailing stop manager (10% → 7% → 5%)
│   │   └── guardrails.py      — Object-oriented hard stops; enforced before every order
│   ├── signals/
│   │   ├── generator.py       — Packages BUY decisions as distributable signals with UUID
│   │   └── distributor.py     — SendGrid HTML email: daily digest + weekly performance report
│   └── memory/
│       ├── journal.py         — Append-only markdown logging (trades, research, summaries)
│       └── state.py           — Reads/writes PROJECT-CONTEXT.md; git commit helper
├── routines/                  — Cloud routine prompts (cron-scheduled, run in ephemeral containers)
│   ├── pre-market.md
│   ├── pre-execute.md
│   ├── market-open.md
│   ├── midday.md
│   ├── daily-summary.md
│   └── weekly-review.md
├── .claude/commands/          — Local slash commands for manual inspection and ad-hoc runs
│   ├── portfolio.md           — /portfolio — live account + positions snapshot
│   ├── trade.md               — /trade — manually trigger a trade decision
│   ├── research.md            — /research — ad-hoc Perplexity scan
│   ├── pre-market.md          — /pre-market
│   ├── market-open.md         — /market-open
│   ├── midday.md              — /midday
│   ├── daily-summary.md       — /daily-summary
│   └── weekly-review.md       — /weekly-review
├── memory/                    — Git-backed state (committed after every routine run)
│   ├── TRADING-STRATEGY.md    — Watchlist, position sizing rules, sector failure tracking
│   ├── TRADE-LOG.md           — All trades: open positions + closed trade history
│   ├── RESEARCH-LOG.md        — Daily pre-market research outputs
│   ├── WEEKLY-REVIEW.md       — Weekly grades and strategy notes
│   └── PROJECT-CONTEXT.md     — Mode, circuit breaker status, API config status
├── scripts/
│   ├── alpaca.sh              — 12-subcommand Alpaca API wrapper (account, positions, orders, etc.)
│   ├── perplexity.sh          — Sonar Pro search wrapper
│   └── notify.sh              — SendGrid email with local fallback
├── api/
│   └── main.py                — FastAPI stub: /portfolio, /signals/latest, /signals/history
├── tests/
│   ├── test_guardrails.py     — 29 tests covering all 6 guardrail checks + run_all()
│   └── test_technical.py      — 16 tests for RSI (Wilder smoothing), SMA, volume ratio
├── CLAUDE.md                  — Agent persona, hard rules, memory read order, API wrappers
└── env.template               — All required environment variables with descriptions
```

---

## Hard Rules (enforced in Python — never overridable by the LLM)

- NO options. NO crypto. US stocks only.
- Max **6 open positions** at any time
- Max **20% of equity** per position
- Max **3 new trades per week**
- Always maintain **15% cash buffer** minimum
- **10% trailing stop** on every position (real GTC order)
- Cut losers at **-7%** — no exceptions, no averaging down
- Tighten trail to **7%** at +15% gain, **5%** at +20% gain
- **Circuit breaker:** halt all new trades if portfolio drops 15% from peak
- **Sector ban:** exit sector after 2 consecutive failed trades; no new sector trades for 2 weeks

---

## Trade Decision Flow

```
Pre-market research (Perplexity)
        ↓
Catalyst scoring → top 3 candidates
        ↓
Pre-execute validation (9:45 AM — first 15 min confirmed)
        ↓
Bull Analyst (Claude) → thesis, target, entry zone, confidence
        ↓
Bear Analyst (Claude) → counter-thesis, risks, stop level
        ↓
Risk Manager (Python) → 6 hard checks, adjusted size
        ↓ (blocks here if any check fails — no API call to arbiter)
Decision Arbiter (Claude) → BUY / NO_TRADE / WAIT
        ↓ (only on BUY, confidence >= 0.70)
Place order + trailing stop (Alpaca)
        ↓
Log to Git memory → notify via email
```

---

## Memory Model

All state lives in `memory/` as plain markdown files. Cloud routines run in ephemeral containers — **everything is lost unless committed to Git**. Every routine ends with:

```bash
git add memory/ && git commit -m "eod: [DATE] ..." && git push origin main
```

This means the Git log *is* the audit trail. Every research run, trade, stop tightening, and weekly review is a permanent commit.

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/saijayanth888/shark-trading-agent
cd shark-trading-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp env.template .env
# Edit .env with your API keys
```

Required keys:
| Variable | Source |
|---|---|
| `ALPACA_API_KEY` + `ALPACA_SECRET_KEY` | [app.alpaca.markets](https://app.alpaca.markets) |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` (paper) or `https://api.alpaca.markets` (live) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `PERPLEXITY_API_KEY` | [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api) |
| `SENDGRID_API_KEY` + `NOTIFY_EMAIL` | [sendgrid.com](https://sendgrid.com) |

### 3. Smoke test

```bash
bash scripts/alpaca.sh account      # should return account JSON
bash scripts/alpaca.sh positions    # should return []
bash scripts/perplexity.sh "NVDA catalyst today. One sentence."
```

### 4. Run tests

```bash
pytest tests/ -v   # 45 tests — all should pass
```

### 5. Configure cloud routines (Claude Code Cloud)

See `routines/README.md` for the full setup guide. Summary:

- Install the [Claude GitHub App](https://github.com/apps/claude) on this repo
- Create 6 routines with the schedule below
- Set all API keys as environment variables on each routine (NOT in .env)
- Enable "Allow unrestricted branch pushes" on each routine

**Cron schedule (America/New_York):**
```
0  6  * * 1-5   routines/pre-market.md
45 9  * * 1-5   routines/pre-execute.md
0  10 * * 1-5   routines/market-open.md
0  13 * * 1-5   routines/midday.md
15 16 * * 1-5   routines/daily-summary.md
0  17 * * 5     routines/weekly-review.md
```

---

## Paper Trading First

Run on Alpaca paper trading for **minimum 4 weeks** before switching to live. Monitor every Git commit the routines make. When ready to go live:

1. Change `ALPACA_BASE_URL` to `https://api.alpaca.markets`
2. Update `TRADING_MODE=live` in environment
3. Start with $5k deployed regardless of account size — prove the strategy first

---

## Signals Business (Shark Signals)

The agent generates daily pre-market research regardless of whether trades are placed. That research output can be packaged as a paid subscription:

- **Daily email digest** — sent automatically via `scripts/notify.sh`
- **Weekly performance report** — grade, P&L, alpha vs S&P, strategy notes
- **Transparency** — all trades are in the public Git log

Target: 100 subscribers × $49–$99/month = $4,900–$9,900/month in parallel with trading income.

---

## License

Apache 2.0 — see [LICENSE](LICENSE)

Copyright 2026 Sai Jayanth Reddy Ailoni
