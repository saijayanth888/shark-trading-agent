# Shark Trading Agent

An autonomous, regime-adaptive AI trading system that manages a US stock portfolio 24/7 using Claude AI, Alpaca Markets, and Perplexity. Runs entirely on cloud routines — seven scheduled phases handle research, execution, risk management, exit management, and strategy validation without human intervention.

Every trade decision, research note, performance review, and backtest result is committed to Git as immutable memory. The system self-learns from past trades, adapts position sizing to market conditions, and continuously validates its own strategy through automated backtesting.

**Status:** Paper Trading | **Strategy:** Regime-Adaptive Momentum Swing Trading | **Mode:** Shark Signals

---

## Key Features

- **7 Automated Cloud Routines** — fully unattended daily trading cycle + weekly backtest
- **Market Regime Detection** — classifies market as BULL/BEAR × QUIET/VOLATILE using SPY
- **Relative Strength Ranking** — Mansfield RS vs SPY filters out underperformers
- **ATR-Based Position Sizing** — risk-normalized sizing with fractional Kelly overlay
- **Multi-Reason Exit Manager** — hard stops, trailing stops, partial profits, time decay, volatility expansion, thesis breaks, regime shifts
- **Macro Calendar Blocking** — auto-blocks trades on FOMC, CPI, NFP, Quad Witching days
- **AI Trade Review** — Claude grades every closed trade A–F, extracts lessons, feeds back into future decisions
- **Context Management** — phase-specific compressed briefings prevent context bloat in cloud routines
- **Historical Backtesting** — validates strategy parameters against 12 months of real market data
- **Adaptive Learning Loop** — lessons from past trades are injected into analyst prompts
- **Drawdown-Aware Scaling** — position sizes automatically reduce as portfolio draws down from peak
- **Circuit Breaker** — halts all trading at -15% from peak equity

---

## Cloud Routine Schedule

| Time (ET) | Phase | Routine | Action |
|---|---|---|---|
| 6:00 AM | `pre-market` | Mon–Fri | Scan watchlist via Perplexity, detect regime, check macro calendar, rank by RS + sentiment, write top candidates to handoff |
| 9:45 AM | `pre-execute` | Mon–Fri | Validate candidates against first 30 min of live trading data, confirm volume + price action |
| 10:00 AM | `market-open` | Mon–Fri | Execute trades: combined analyst (bull+bear+decision), regime gates, RS filter, ATR sizing, guardrails, bracket orders |
| 1:00 PM | `midday` | Mon–Fri | Manage exits only: run exit manager, check hard stops (-7%), tighten trails, detect vol expansion, thesis-break exits, AI trade review |
| 4:15 PM | `daily-summary` | Mon–Fri | EOD P&L, peak equity update, circuit breaker check, email digest, Git commit |
| 5:00 PM | `weekly-review` | Friday | Grade week vs S&P, compute alpha, win rate, profit factor, plan strategy adjustments |
| 6:00 PM | `backtest` | Friday | Run historical backtest against last 12 months, generate BACKTEST-REPORT.md with metrics + parameter recommendations |

Each routine: `clone repo → git pull → generate context briefing → execute phase → git commit + push`

---

## System Architecture

```
shark-trading-agent/
│
├── shark/                         ── CORE PYTHON ENGINE ──────────────────────
│   ├── run.py                     — Phase runner: env → logging → context briefing → execute → error handling
│   │
│   ├── data/                      ── DATA LAYER ──────────────────────────────
│   │   ├── alpaca_data.py         — Alpaca REST client (account, positions, bars, watchlist snapshots)
│   │   ├── perplexity.py          — Perplexity Sonar-Pro market intelligence + sentiment
│   │   ├── technical.py           — 14 indicators: RSI, SMA-20/50, EMA-9, ATR-14, MACD, Bollinger, ADX, VWAP, momentum score
│   │   ├── market_regime.py       — SPY-based regime detection: BULL_QUIET / BULL_VOLATILE / BEAR_QUIET / BEAR_VOLATILE
│   │   ├── relative_strength.py   — Mansfield RS vs SPY (10d/20d/50d weighted composite, acceleration detection)
│   │   └── macro_calendar.py      — Static FOMC/CPI/NFP/Quad Witching calendar 2025–2026 with impact levels
│   │
│   ├── agents/                    ── AI AGENT LAYER ──────────────────────────
│   │   ├── combined_analyst.py    — Single Claude call: bull thesis + bear thesis + decision with full context
│   │   ├── analyst_bull.py        — Bullish thesis generator (catalyst, target, entry zone, confidence)
│   │   ├── analyst_bear.py        — Counter-thesis generator (risks, invalidation signals, downside)
│   │   ├── risk_manager.py        — Pure Python hard-rule pre-filter (8 checks, no AI involved)
│   │   ├── decision_arbiter.py    — Final decision: BUY / NO_TRADE / WAIT (short-circuits if risk fails)
│   │   └── trade_reviewer.py      — Post-trade AI review: grades A–F, pattern classification, lesson extraction
│   │
│   ├── execution/                 ── EXECUTION ENGINE ────────────────────────
│   │   ├── orders.py              — Place, cancel, close Alpaca orders (market + limit + bracket)
│   │   ├── stops.py               — Three-tier trailing stop manager (10% → 7% → 5%)
│   │   ├── guardrails.py          — Object-oriented pre-trade checks: positions, cash, sector, circuit breaker
│   │   ├── position_sizer.py      — ATR-based sizing + fractional Kelly + regime adjustment + drawdown scaling
│   │   └── exit_manager.py        — Multi-reason exit logic: hard stop, trail, partials, time decay, vol expansion, thesis break
│   │
│   ├── backtest/                  ── BACKTESTING ENGINE ──────────────────────
│   │   ├── engine.py              — Bar-by-bar simulation loop with full portfolio tracking
│   │   ├── strategy.py            — All trading rules encoded as deterministic testable logic
│   │   ├── data_loader.py         — Historical bar fetcher with in-memory caching via Alpaca
│   │   ├── metrics.py             — Sharpe, Sortino, drawdown, win rate, profit factor, CAGR, monthly returns
│   │   └── report.py              — Generates BACKTEST-REPORT.md with tables, regime analysis, recommendations
│   │
│   ├── context/                   ── CONTEXT MANAGEMENT ──────────────────────
│   │   └── context_manager.py     — Phase-specific briefing generator: extracts, compresses, trims to ~4000 tokens
│   │
│   ├── signals/                   ── SIGNAL DISTRIBUTION ─────────────────────
│   │   ├── generator.py           — Packages BUY decisions as distributable signals with UUID tracking
│   │   ├── distributor.py         — Email delivery: daily digest + weekly performance report
│   │   └── templates.py           — HTML email templates for signal alerts
│   │
│   ├── memory/                    ── MEMORY MANAGEMENT ───────────────────────
│   │   ├── state.py               — Read/write PROJECT-CONTEXT.md, peak equity, circuit breaker, git commit/push
│   │   ├── journal.py             — Append-only markdown logging (trades, research, summaries)
│   │   └── handoff.py             — DAILY-HANDOFF.md inter-phase state passing (pre-market → midday chain)
│   │
│   └── phases/                    ── PHASE RUNNERS ───────────────────────────
│       ├── pre_market.py          — Watchlist scan, regime detection, macro check, RS ranking, candidate shortlist
│       ├── pre_execute.py         — Live data validation of pre-market candidates
│       ├── market_open.py         — Trade execution with full gating pipeline
│       ├── midday.py              — Position management, exit checks, trade review
│       ├── daily_summary.py       — EOD snapshot, P&L, circuit breaker, email digest
│       ├── weekly_review.py       — Performance grading, alpha computation, strategy notes
│       └── backtest.py            — Weekly historical backtest execution + report generation
│
├── routines/                      ── CLOUD ROUTINE PROMPTS ───────────────────
│   ├── pre-market.md              — 6:00 AM ET, Mon–Fri
│   ├── pre-execute.md             — 9:45 AM ET, Mon–Fri
│   ├── market-open.md             — 10:00 AM ET, Mon–Fri
│   ├── midday.md                  — 1:00 PM ET, Mon–Fri
│   ├── daily-summary.md           — 4:15 PM ET, Mon–Fri
│   ├── weekly-review.md           — 5:00 PM ET, Friday
│   └── README.md                  — Cloud routine setup guide
│
├── memory/                        ── GIT-BACKED STATE ────────────────────────
│   ├── PROJECT-CONTEXT.md         — Mode, peak equity, circuit breaker, weekly trade count
│   ├── TRADING-STRATEGY.md        — Full strategy: entry criteria, exits, regime rules, macro blocks, RS filter
│   ├── TRADE-LOG.md               — Open positions + closed trade history
│   ├── RESEARCH-LOG.md            — Daily pre-market research outputs
│   ├── DAILY-HANDOFF.md           — Inter-phase state (pre-market → market-open → midday chain)
│   ├── LESSONS-LEARNED.md         — AI-extracted lessons from closed trades (top 20, auto-archived)
│   ├── WEEKLY-REVIEW.md           — Weekly grades, alpha vs SPY, strategy notes
│   ├── BACKTEST-REPORT.md         — Auto-generated weekly backtest results + recommendations
│   └── CONTEXT-BRIEFING.md        — Ephemeral phase-specific briefing (regenerated each phase, never committed)
│
├── .claude/commands/              ── LOCAL SLASH COMMANDS ─────────────────────
│   ├── portfolio.md               — /portfolio — live account + positions snapshot
│   ├── trade.md                   — /trade — manually trigger a trade decision
│   ├── research.md                — /research — ad-hoc Perplexity scan
│   ├── pre-market.md              — /pre-market — manual phase trigger
│   ├── market-open.md             — /market-open
│   ├── midday.md                  — /midday
│   ├── daily-summary.md           — /daily-summary
│   └── weekly-review.md           — /weekly-review
│
├── scripts/                       ── SHELL WRAPPERS ──────────────────────────
│   ├── alpaca.sh                  — 12-subcommand Alpaca API wrapper
│   ├── perplexity.sh              — Sonar-Pro search wrapper
│   ├── notify.sh                  — Gmail SMTP email with fallback
│   └── health-check.sh            — System health verification
│
├── api/                           ── REST API ────────────────────────────────
│   └── main.py                    — FastAPI: /portfolio, /signals/latest, /signals/history
│
├── tests/                         ── TEST SUITE ──────────────────────────────
│   ├── test_guardrails.py         — 29 tests: all 6 guardrail checks + run_all()
│   └── test_technical.py          — 16 tests: RSI (Wilder smoothing), SMA, volume ratio, ATR, MACD, ADX
│
├── CLAUDE.md                      — Agent persona, hard rules, context management, phase directives
├── env.template                   — All environment variables with descriptions
├── requirements.txt               — Python dependencies
└── LICENSE                        — Apache 2.0
```

---

## Core Strategy: Regime-Adaptive Momentum Swing Trading

**Hold period:** 2–10 trading days | **Universe:** US stocks only | **Style:** momentum swing with adaptive risk

### Strategy Components

| Component | Module | Description |
|---|---|---|
| **Regime Detection** | `market_regime.py` | Classifies market into 4 states using SPY SMA-20/50 crossover + ATR volatility percentile |
| **Relative Strength** | `relative_strength.py` | Mansfield RS vs SPY: weighted 10d/20d/50d composite — only trade outperformers |
| **Macro Calendar** | `macro_calendar.py` | Blocks trades on FOMC, CPI, NFP days; half-size during event weeks |
| **Technical Analysis** | `technical.py` | 14 indicators: RSI, SMA, EMA, ATR, MACD, Bollinger, ADX, VWAP + composite momentum score |
| **Position Sizing** | `position_sizer.py` | ATR-based risk normalization + fractional Kelly + regime multiplier + drawdown scaling |
| **Exit Management** | `exit_manager.py` | 7 exit reasons: hard stop, trail, partials, time decay, vol expansion, thesis break, regime shift |
| **AI Trade Review** | `trade_reviewer.py` | Claude grades closed trades A–F, classifies patterns, extracts one-line lessons |
| **Combined Analyst** | `combined_analyst.py` | Single Claude call: bull+bear+decision with regime, RS, macro, and lesson context |
| **Backtesting** | `backtest/engine.py` | Bar-by-bar simulation validating all rules against 12 months of historical data |

### Regime Rules

| Regime | New Trades | Size Multiplier | Max Trades/Day | Confidence Threshold |
|---|---|---|---|---|
| **BULL_QUIET** | Yes | 1.0× | 3 | 0.65 |
| **BULL_VOLATILE** | Yes | 0.5× | 2 | 0.75 |
| **BEAR_QUIET** | No | 0.0× | 0 | — |
| **BEAR_VOLATILE** | No | 0.0× | 0 | — |

### Entry Criteria (all 9 must pass)

1. **Regime gate** — BULL_QUIET or BULL_VOLATILE only
2. **Macro calendar clear** — no FOMC/CPI/NFP on trade day or next day
3. **Relative Strength** — RS composite > 0 (outperforming SPY)
4. **Clear catalyst** — earnings beat, product launch, analyst upgrade, sector rotation
5. **Technical momentum** — price > SMA-20, RSI 45–70, volume > 1.2×, MACD positive, momentum ≥ 40
6. **Sector health** — sector ETF trending, max 3 per sector, no 2 consecutive sector failures
7. **Risk/reward** — minimum 2:1 target-to-stop ratio
8. **Sentiment confirmation** — Perplexity sentiment bullish, no headline risks within 48h
9. **No earnings trap** — block entry if earnings within 2 trading days

### Exit Management (Multi-Reason)

| Exit Type | Trigger | Action |
|---|---|---|
| **Hard stop** | -7% from entry | Close 100% immediately |
| **Trailing stop** | Dynamic: 10% → 7% at +15% → 5% at +20% | Close 100% |
| **Partial profit T1** | +5% gain | Sell 25% |
| **Partial profit T2** | +10% gain | Sell 25% |
| **Partial profit T3** | +15% gain | Sell 25% |
| **Time decay** | No +3% move after 5 days | Close 100% |
| **Volatility expansion** | ATR > 2× entry ATR | Close 100% |
| **Thesis break** | Sentiment flips bearish | Close 100% |
| **Regime shift** | Regime moves to BEAR | Close all within 1 session |

### Position Sizing Algorithm

```
1. ATR-based:     shares = risk_dollars / (ATR × stop_multiple)
2. Kelly overlay: kelly_shares = (fractional_kelly × portfolio) / price
3. Max cap:       max_shares = portfolio × 20% / price
4. Take minimum:  raw = min(ATR, Kelly, max_cap)
5. Regime adjust: adjusted = raw × regime_multiplier (0.0–1.0)
6. Drawdown scale: final = adjusted × drawdown_multiplier (0.0–1.0)
7. Confidence:    output = final × confidence_scale (0.8–1.0)
```

**Drawdown scaling curve:**

| Drawdown from Peak | Size Multiplier |
|---|---|
| 0–3% | 1.0× (full size) |
| 3–5% | 0.9× |
| 5–10% | 0.8× → 0.5× (linear) |
| 10–15% | 0.5× → 0.3× |
| >15% | 0.0× (circuit breaker) |

---

## Trade Decision Flow

```
                    ┌─────────────────────────────────┐
                    │      PRE-MARKET (6:00 AM)       │
                    │  Perplexity scan → catalyst rank │
                    │  Regime detection (SPY analysis) │
                    │  Macro calendar check            │
                    │  RS ranking (Mansfield vs SPY)   │
                    └──────────────┬──────────────────┘
                                   ↓
                    ┌─────────────────────────────────┐
                    │     PRE-EXECUTE (9:45 AM)       │
                    │  Live 30-min data validation    │
                    │  Volume + price action confirm  │
                    └──────────────┬──────────────────┘
                                   ↓
                    ┌─────────────────────────────────┐
                    │     MARKET-OPEN (10:00 AM)      │
                    │                                 │
                    │  ┌─ Combined Analyst (Claude) ─┐│
                    │  │  Bull thesis + target       ││
                    │  │  Bear thesis + risks        ││
                    │  │  Decision: BUY/NO/WAIT      ││
                    │  │  Regime + RS + Macro context ││
                    │  │  Injected lessons (top 5)   ││
                    │  └─────────────────────────────┘│
                    │            ↓                     │
                    │  Risk Manager (Python — 8 gates) │
                    │            ↓                     │
                    │  Position Sizer (ATR + Kelly)    │
                    │            ↓                     │
                    │  Place bracket order (Alpaca)    │
                    └──────────────┬──────────────────┘
                                   ↓
                    ┌─────────────────────────────────┐
                    │        MIDDAY (1:00 PM)         │
                    │  Exit manager → 7 exit checks   │
                    │  Stop tightening                │
                    │  AI trade review (closed trades) │
                    │  Lesson extraction → memory     │
                    └──────────────┬──────────────────┘
                                   ↓
                    ┌─────────────────────────────────┐
                    │     DAILY SUMMARY (4:15 PM)     │
                    │  P&L snapshot                   │
                    │  Peak equity update             │
                    │  Circuit breaker check          │
                    │  Email digest                   │
                    └──────────────┬──────────────────┘
                                   ↓ (Friday only)
              ┌────────────────────┴────────────────────┐
              ↓                                         ↓
┌──────────────────────┐              ┌──────────────────────┐
│  WEEKLY REVIEW (5PM) │              │   BACKTEST (6PM)     │
│  Grade vs S&P 500    │              │  12-month simulation │
│  Win rate, alpha     │              │  Regime attribution  │
│  Strategy adjustments│              │  Parameter validation│
│  Plan next week      │              │  BACKTEST-REPORT.md  │
└──────────────────────┘              └──────────────────────┘
```

---

## Backtesting Engine

The backtest phase runs weekly as a cloud routine, validating the current strategy parameters against real historical data from Alpaca.

### What It Tests

- **Regime gating** — Did blocking BEAR trades save money?
- **RS filtering** — Do stocks outperforming SPY produce better returns?
- **ATR stop distance** — Is 2× ATR optimal, or should it be 1.5× or 2.5×?
- **Momentum threshold** — Is 40 the right cutoff?
- **Partial profit tiers** — Does scaling out at +5/+10/+15% beat holding full size?
- **Time decay exit** — Does closing stale positions after 5 days improve returns?
- **Volatility expansion** — Does exiting on ATR expansion prevent drawdowns?

### Output: BACKTEST-REPORT.md

Generated automatically with:

- **Performance summary** — total return, CAGR, ending capital
- **Trade statistics** — win rate, profit factor, expectancy per trade
- **Risk metrics** — Sharpe ratio, Sortino ratio, max drawdown
- **Regime breakdown** — P&L and win rate per regime (BULL_QUIET, BULL_VOLATILE, etc.)
- **Exit reason breakdown** — which exit types saved money vs cost money
- **Monthly returns table** — month-by-month P&L and return %
- **Automated recommendations** — edge detected / no edge / parameter suggestions

### Configurable Parameters (via environment variables)

| Variable | Default | Description |
|---|---|---|
| `BACKTEST_CAPITAL` | 100000 | Starting capital for simulation |
| `BACKTEST_LOOKBACK_DAYS` | 365 | How far back to test |
| `BACKTEST_MOMENTUM_MIN` | 40 | Momentum score entry threshold |
| `BACKTEST_RS_MIN` | 0 | Minimum RS composite for entry |
| `BACKTEST_ATR_STOP_MULT` | 2.0 | ATR multiplier for stop distance |
| `BACKTEST_RISK_PCT` | 1.0 | Risk per trade as % of portfolio |
| `BACKTEST_SYMBOLS` | (full watchlist) | Comma-separated tickers to test |

---

## Context Management System

Cloud routines run in ephemeral containers with limited context windows. The context management system prevents quality degradation by generating **phase-specific, token-efficient briefings**.

### How It Works

```
run.py starts phase
        ↓
context_manager.py reads memory files
        ↓
Extracts ONLY what this phase needs (manifest-driven)
        ↓
Compresses: sections, tails, keys, today-only
        ↓
Trims to ~4000 token budget
        ↓
Writes memory/CONTEXT-BRIEFING.md
        ↓
Phase executes with focused context
```

### Phase Context Manifests

Each phase declares exactly which memory files and sections it needs:

| Phase | Context Loaded |
|---|---|
| `pre-market` | Strategy (watchlist, entry, regime, macro) + full project context + recent trades + lessons |
| `market-open` | Strategy (entry, sizing, exits, regime, RS) + handoff + project context + trades + lessons |
| `midday` | Handoff (market-open section) + project context keys + exit rules + today's trades |
| `daily-summary` | Full project context + full handoff + today's trades + circuit breaker rules |
| `weekly-review` | Full project context + this week's trades + research + previous reviews + lessons |
| `backtest` | Project context keys + strategy (sizing, entry, exits, regime) + previous backtest report |

---

## Hard Rules (Python-Enforced — Never Overridable by LLM)

```python
# These are checked in guardrails.py BEFORE any order is placed
MAX_POSITIONS         = 6      # absolute maximum open positions
MAX_POSITION_PCT      = 20%    # max portfolio % per position
MAX_WEEKLY_TRADES     = 3      # Mon-Fri calendar week
MIN_CASH_BUFFER       = 15%    # always maintain cash floor
HARD_STOP             = -7%    # cut losers, no exceptions
TRAIL_STOP_DEFAULT    = 10%    # initial trailing stop
TRAIL_STOP_TIGHT      = 7%     # tighten at +15% gain
TRAIL_STOP_TIGHTER    = 5%     # tighten at +20% gain
CIRCUIT_BREAKER       = -15%   # halt all trading from peak
SECTOR_BAN_THRESHOLD  = 2      # consecutive failures → exit sector
MIN_CONFIDENCE        = 0.70   # AI confidence threshold for BUY
MIN_RISK_REWARD       = 2.0    # minimum risk/reward ratio
```

---

## Watchlist

| Sector | Tickers | Focus |
|---|---|---|
| **Technology** | NVDA, MSFT, AAPL, GOOGL, META, AMD, AVGO | Primary — momentum leaders |
| **Financials** | JPM, GS, MS | Rate-sensitive |
| **Healthcare** | UNH, LLY, JNJ | Defensive rotation |
| **Energy** | XOM, CVX | Catalyst-driven only |
| **Consumer** | AMZN, TSLA | High-beta momentum |

---

## Adaptive Learning Loop

```
Trade closes → AI Review (Claude grades A–F)
        ↓
Pattern classified (momentum_continuation, failed_breakout, thesis_decay, etc.)
        ↓
One-line lesson extracted → saved to LESSONS-LEARNED.md
        ↓
Top 5 recent lessons injected into combined analyst prompts
        ↓
Strategy evolves based on real trade outcomes
```

This creates a **feedback loop** where the system gets smarter over time. Bad patterns get flagged, winning patterns get reinforced.

---

## Memory Model

All state lives in `memory/` as plain markdown files. Cloud routines run in ephemeral containers — **everything is lost unless committed to Git**.

| File | Purpose | Lifecycle |
|---|---|---|
| `PROJECT-CONTEXT.md` | Mode, peak equity, circuit breaker, weekly counts | Updated every phase |
| `TRADING-STRATEGY.md` | Full strategy rules, watchlist, parameters | Updated during reviews |
| `TRADE-LOG.md` | Open + closed trades | Appended per trade, archived monthly |
| `RESEARCH-LOG.md` | Daily pre-market research | Appended daily, archived weekly |
| `DAILY-HANDOFF.md` | Inter-phase state passing | Reset each morning |
| `LESSONS-LEARNED.md` | AI-extracted trade lessons | Rolling 20 entries, auto-archived |
| `WEEKLY-REVIEW.md` | Weekly grades and strategy notes | Appended weekly |
| `BACKTEST-REPORT.md` | Auto-generated backtest results | Overwritten weekly |
| `CONTEXT-BRIEFING.md` | Phase-specific compressed briefing | Ephemeral, never committed |

Every routine ends with:

```bash
git add memory/ && git commit -m "phase: [PHASE] [DATE] ..." && git push origin main
```

The Git log **is** the audit trail. Every decision, trade, and review is a permanent commit.

---

## Setup

### 1. Clone and Install

```bash
git clone https://github.com/saijayanth888/shark-trading-agent
cd shark-trading-agent
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Environment

```bash
cp env.template .env
# Edit .env with your API keys (local development only)
```

**Required API Keys:**

| Variable | Source | Purpose |
|---|---|---|
| `ALPACA_API_KEY` | [app.alpaca.markets](https://app.alpaca.markets) | Brokerage API |
| `ALPACA_SECRET_KEY` | [app.alpaca.markets](https://app.alpaca.markets) | Brokerage API |
| `ALPACA_BASE_URL` | — | `https://paper-api.alpaca.markets` (paper) or `https://api.alpaca.markets` (live) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | Claude AI (analyst, reviewer) |
| `PERPLEXITY_API_KEY` | [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api) | Market research + sentiment |
| `GMAIL_APP_PASSWORD` | [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) | Email notifications |
| `NOTIFY_EMAIL` | — | Recipient email address |

**Risk Parameters (override defaults):**

| Variable | Default | Description |
|---|---|---|
| `TRADING_MODE` | `paper` | `paper` or `live` |
| `MAX_POSITIONS` | `6` | Maximum open positions |
| `MAX_POSITION_PCT` | `0.20` | Max portfolio % per position |
| `MAX_WEEKLY_TRADES` | `3` | Max new trades per week |
| `MIN_CASH_BUFFER_PCT` | `0.15` | Minimum cash buffer |
| `CIRCUIT_BREAKER_PCT` | `0.15` | Halt threshold from peak |
| `RISK_PER_TRADE_PCT` | `1.0` | Base risk per trade |
| `ATR_STOP_MULTIPLE` | `2.0` | ATR multiplier for stops |
| `KELLY_FRACTION` | `0.25` | Fractional Kelly (¼ Kelly) |

### 3. Smoke Test

```bash
# Test API connections
bash scripts/alpaca.sh account
bash scripts/alpaca.sh positions
bash scripts/perplexity.sh "NVDA catalyst today. One sentence."

# Test Python modules
python -c "from shark.data.technical import compute_indicators; print('OK')"
python -c "from shark.backtest.engine import BacktestEngine; print('OK')"
```

### 4. Run Tests

```bash
pytest tests/ -v
```

### 5. Run a Phase Locally

```bash
# Any phase can be run locally with --dry-run
python shark/run.py pre-market --dry-run
python shark/run.py backtest --dry-run
```

### 6. Run Backtest Manually

```bash
# Full backtest with defaults
python shark/run.py backtest

# Custom parameters (set via env vars)
BACKTEST_CAPITAL=50000 BACKTEST_MOMENTUM_MIN=50 python shark/run.py backtest
```

---

## Cloud Routine Setup (Claude Code)

### Prerequisites

1. Install the [Claude GitHub App](https://github.com/apps/claude) on this repository
2. Enable "Allow unrestricted branch pushes" on each routine
3. Set **all API keys as environment variables** on each routine (NOT in .env)

### Cron Schedule (America/New_York)

```
0  6  * * 1-5   routines/pre-market.md        # 6:00 AM Mon-Fri
45 9  * * 1-5   routines/pre-execute.md       # 9:45 AM Mon-Fri
0  10 * * 1-5   routines/market-open.md       # 10:00 AM Mon-Fri
0  13 * * 1-5   routines/midday.md            # 1:00 PM Mon-Fri
15 16 * * 1-5   routines/daily-summary.md     # 4:15 PM Mon-Fri
0  17 * * 5     routines/weekly-review.md     # 5:00 PM Friday
0  18 * * 5     routines/backtest.md          # 6:00 PM Friday (or add to weekly-review)
```

### Environment Variables per Routine

Every cloud routine needs these set:

```
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ANTHROPIC_API_KEY=...
PERPLEXITY_API_KEY=...
GMAIL_APP_PASSWORD=...
NOTIFY_EMAIL=...
TRADING_MODE=paper
```

**Backtest-specific (optional, add to backtest routine):**

```
BACKTEST_CAPITAL=100000
BACKTEST_LOOKBACK_DAYS=365
BACKTEST_MOMENTUM_MIN=40
BACKTEST_RS_MIN=0
BACKTEST_ATR_STOP_MULT=2.0
BACKTEST_RISK_PCT=1.0
```

---

## Paper Trading Roadmap

| Phase | Duration | Objective |
|---|---|---|
| **Phase 1: Paper + Backtest** | Weeks 1–4 | Run cloud routines in paper mode. Review every Git commit. Analyze BACKTEST-REPORT.md weekly. |
| **Phase 2: Parameter Tuning** | Weeks 5–8 | Adjust parameters based on backtest evidence. Compare paper results vs backtest predictions. |
| **Phase 3: Small Live** | Weeks 9–12 | Switch to live with $5K–$10K deployed. Keep backtesting weekly. |
| **Phase 4: Scale** | Month 4+ | Increase capital gradually as strategy proves consistent edge. |

**To switch from paper to live:**

1. Set `ALPACA_BASE_URL=https://api.alpaca.markets`
2. Set `TRADING_MODE=live`
3. Start with small capital — prove consistency first

---

## Signals Business (Shark Signals)

The agent generates daily pre-market research regardless of whether trades are placed. Package as a paid subscription:

- **Daily email digest** — automated via `scripts/notify.sh`
- **Weekly performance report** — grade, P&L, alpha vs S&P, strategy notes
- **Backtest transparency** — weekly BACKTEST-REPORT.md included
- **Full audit trail** — all trades in public Git log

Target: 100 subscribers × $49–$99/month = $4,900–$9,900/month in parallel with trading returns.

---

## Tech Stack

| Layer | Technology | Purpose |
|---|---|---|
| **AI Brain** | Anthropic Claude API | Trade analysis, thesis generation, trade review, lesson extraction |
| **Market Data** | Alpaca Trade API | Account, positions, historical bars, watchlist snapshots, order execution |
| **Research** | Perplexity Sonar-Pro | Real-time market intelligence, catalyst discovery, sentiment analysis |
| **Execution** | Alpaca Orders API | Market/limit/bracket orders, trailing stops, position management |
| **Notifications** | Gmail SMTP | Daily digest, trade alerts, weekly reports |
| **State** | Git + Markdown | All memory files committed after each phase — Git log = audit trail |
| **Scheduling** | Claude Cloud Routines | 7 cron-scheduled phases in ephemeral containers |
| **Technical Analysis** | Pandas | RSI, SMA, EMA, ATR, MACD, Bollinger, ADX, VWAP — no external TA library |
| **REST API** | FastAPI + Uvicorn | Portfolio endpoint, signal history (stub for future expansion) |
| **Testing** | Pytest | Guardrail checks, technical indicator accuracy, RSI Wilder smoothing |

---

## License

Apache 2.0 — see [LICENSE](LICENSE)

Copyright 2026 Sai Jayanth Reddy Ailoni
