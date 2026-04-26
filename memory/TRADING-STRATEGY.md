# Trading Strategy

## Mission
Beat the S&P 500 with disciplined swing trading. US stocks only.
Current mode: PAPER TRADING (minimum 4 weeks before going live)

## Core Strategy: Momentum Swing Trading
- Hold period: 2–10 trading days
- Entry: stocks with confirmed catalyst + technical momentum alignment
- Exit: trailing stop (10%) or thesis broken

## Position Sizing Rules
- Max 20% of portfolio per position
- Max 6 open positions simultaneously
- Maintain minimum 15% cash buffer at all times
- Max 3 new trades per week (Mon–Fri calendar week)

## Entry Criteria (all must pass)
1. Clear catalyst: earnings beat, product launch, analyst upgrade, sector rotation catalyst
2. Technical momentum: price above SMA20, RSI 45–70 (not overbought), volume > 1.2x average
3. Sector health: sector ETF trending, not two consecutive recent failures in this sector
4. Risk/reward: minimum 2:1 target-to-stop ratio
5. Confirmation: Perplexity sentiment bullish, no major headline risks within 48h

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

## Trailing Stop Tiers
| Position P&L | Trail % |
|---|---|
| < +15% | 10% |
| +15% to +19% | 7% |
| >= +20% | 5% |

Rule: never tighten within 3% of current price. Never move stop down.

## Circuit Breaker
- Trigger: portfolio drops 15% from rolling peak equity
- Effect: halt ALL new trades until manually reviewed and reset
- Reset: owner reviews, updates PROJECT-CONTEXT.md to INACTIVE, adjusts strategy

## Strategy Review Schedule
- Weekly: grade performance, note what worked/failed
- Monthly: consider watchlist rotation, sector weight adjustments
- After 3 consecutive losing weeks: mandatory strategy review before next trade

## Last Updated
2026-04-25 — Initial strategy. Paper trading mode. Starting capital TBD at account funding.
