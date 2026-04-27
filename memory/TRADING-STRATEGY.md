# Trading Strategy

## Mission
Beat the S&P 500 with disciplined swing trading. US stocks only.
Current mode: PAPER TRADING (minimum 4 weeks before going live)

## Core Strategy: Regime-Adaptive Momentum Swing Trading
- Hold period: 2–10 trading days
- Entry: stocks with confirmed catalyst + technical momentum + relative strength vs SPY
- Exit: multi-reason (trailing stop, partial profits, time decay, thesis break, volatility expansion)
- Regime-aware: aggressiveness scales with market conditions (BULL/BEAR × LOW/HIGH VOL)

## Position Sizing Rules
- ATR-based sizing: risk 1–2% of portfolio per trade, stop = 2× ATR from entry
- Fractional Kelly criterion overlay for optimal position sizing
- Max 20% of portfolio per position (hard cap)
- Max 6 open positions simultaneously
- Maintain minimum 15% cash buffer at all times
- Max 3 new trades per week (Mon–Fri calendar week)
- Regime multiplier: BULL_QUIET=1.0, BULL_VOLATILE=0.5, BEAR_QUIET=0.0, BEAR_VOLATILE=0.0
- Drawdown scaling: reduce size when equity drops from peak

## Entry Criteria (all must pass)
1. **Market regime**: BULL_QUIET or BULL_VOLATILE only (no new longs in BEAR)
2. **Macro calendar clear**: no FOMC/CPI/NFP on trade day or next day
3. **Relative Strength**: stock outperforming SPY (RS composite > 1.0)
4. **Clear catalyst**: earnings beat, product launch, analyst upgrade, sector rotation
5. **Technical momentum**: price above SMA20, RSI 45–70, volume > 1.2x, MACD positive, momentum score ≥ 40
6. **Sector health**: sector ETF trending, max 3 positions per sector, no 2 consecutive sector failures
7. **Risk/reward**: minimum 2:1 target-to-stop ratio
8. **Confirmation**: Perplexity sentiment bullish, no major headline risks within 48h
9. **No earnings**: block entry if earnings within 2 trading days

## Watchlist — Core Tickers
### Technology (primary focus)
- NVDA, MSFT, AAPL, GOOGL, META, AMD, AVGO

### Financials
- JPM, GS, MS

### Healthcare (defensive rotation)
- UNH, LLY, JNJ

### Energy (catalyst-driven only)
- XOM, CVX

### Consumer Discretionary
- AMZN, TSLA

## Sector Failure Tracking
- Track consecutive failed trades per sector
- After 2 consecutive losses in same sector: rotate out, no new trades in that sector for 2 weeks

## Exit Management (Multi-Reason)

### Trailing Stop Tiers
| Position P&L | Trail % |
|---|---|
| < +15% | 10% |
| +15% to +19% | 7% |
| >= +20% | 5% |

Rule: never tighten within 3% of current price. Never move stop down.

### Partial Profit-Taking
| Gain Level | Action |
|---|---|
| +5% | Sell 25% (T1) |
| +10% | Sell 25% (T2) |
| +15% | Sell 25% (T3) |
| Runner | Let remaining 25% ride with tight trail |

### Additional Exit Triggers
- **Hard stop**: -7% intraday → close entire position
- **Time decay**: no +3% move after 5 trading days → reduce/close
- **Thesis break**: Perplexity sentiment flips bearish + invalidation signal → close
- **Volatility expansion**: ATR expands >2x from entry → tighten or close
- **Regime shift**: regime moves to BEAR → close all within 1 session

## Market Regime Detection
- Uses SPY daily bars: SMA-50 trend + ATR volatility percentile
- **BULL_QUIET**: full aggression, 3 trades/day, 1.0x sizing
- **BULL_VOLATILE**: cautious, 2 trades/day, 0.5x sizing, wider stops
- **BEAR_QUIET**: no new longs, manage exits only
- **BEAR_VOLATILE**: no new longs, tighten all stops, prepare for opportunities

## Macro Calendar Blocks
- **FOMC**: no trades day-of or day-before. Half-size during FOMC week.
- **CPI/NFP**: no trades day-of. Half-size day-before.
- **Quad Witching**: elevated caution (half-size)
- Calendar covers 2025–2026, updated quarterly.

## Relative Strength Filter
- Mansfield RS vs SPY over 10d, 20d, 50d (weighted composite)
- Only trade stocks outperforming SPY (RS composite > 1.0)
- Accelerating RS = extra conviction bonus
- Decelerating RS = reduced sizing

## Circuit Breaker
- Trigger: portfolio drops 15% from rolling peak equity
- Effect: halt ALL new trades until manually reviewed and reset
- Reset: owner reviews, updates PROJECT-CONTEXT.md to INACTIVE, adjusts strategy

## Adaptive Learning
- Every closed trade reviewed by AI (graded A–F)
- Pattern classified (momentum_continuation, failed_breakout, thesis_decay, etc.)
- One-line actionable lesson extracted and stored in LESSONS-LEARNED.md
- Top 5 recent lessons injected into analyst prompts
- Pattern win rates tracked to adapt strategy over time

## Strategy Review Schedule
- Weekly: grade performance, note what worked/failed
- Monthly: consider watchlist rotation, sector weight adjustments
- After 3 consecutive losing weeks: mandatory strategy review before next trade

## Last Updated
2025-07-07 — Advanced strategy upgrade: regime detection, RS filtering, ATR sizing, macro blocks, exit management, adaptive learning.
