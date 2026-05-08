# Backtest Report

Generated: 2026-05-08 21:11

- **Capital**: $100,000
- **Symbols tested**: 22
- **Simulation days**: 310

## Performance Summary

| Metric | Value |
|---|---|
| Starting Capital | $100,000.00 |
| Ending Capital | $104,787.10 |
| Total Return | +4.79% |
| Total P&L | $+15,344.20 |
| CAGR | 3.87% |

## Trade Statistics

| Metric | Value |
|---|---|
| Total Trades | 136 |
| Winners | 70 |
| Losers | 63 |
| Win Rate | 51.5% |
| Profit Factor | 1.43 |
| Avg Winner | $+725.43 (+5.95%) |
| Avg Loser | $562.47 (3.45%) |
| Win/Loss Ratio | 1.29 |
| Expectancy | $+112.83/trade |

## Risk Metrics

| Metric | Value |
|---|---|
| Max Drawdown | 17.80% |
| Sharpe Ratio | -0.01 |
| Sortino Ratio | -0.01 |
| Max Consecutive Wins | 6 |
| Max Consecutive Losses | 7 |
| Avg Hold (all) | 8.9 days |
| Avg Hold (winners) | 13.7 days |
| Avg Hold (losers) | 4.1 days |

## Regime Breakdown

| Regime | Trades | Total P&L | Win Rate |
|---|---|---|---|
| BULL_QUIET | 126 | $+10,483.97 | 49.2% |
| BULL_VOLATILE | 10 | $+4,860.23 | 80.0% |

## Strategy Breakdown (setup_tag)

| Setup | Trades | Total P&L | Win Rate | Avg P&L |
|---|---|---|---|---|
| momentum | 109 | $+16,575.29 | 55.0% | $+152.07 |
| pead | 27 | $-1,231.09 | 37.0% | $-45.60 |

## Exit Reason Breakdown

| Exit Reason | Count | Total P&L | Avg P&L |
|---|---|---|---|
| stop | 24 | $+14,479.08 | $+603.29 |
| partial_complete | 1 | $+4,751.25 | $+4,751.25 |
| regime_shift | 23 | $+2,177.69 | $+94.68 |
| time_decay | 80 | $+955.60 | $+11.94 |
| target | 3 | $+0.00 | $+0.00 |
| hard_stop | 5 | $-7,019.42 | $-1,403.88 |

## Monthly Returns

| Month | Return | P&L | Ending Equity |
|---|---|---|---|
| 2024-09 | +0.13% | $+130.08 | $100,130.08 |
| 2024-10 | -2.33% | $-2,283.52 | $95,921.11 |
| 2024-11 | +0.68% | $+651.03 | $96,806.21 |
| 2024-12 | +1.07% | $+1,028.14 | $97,415.54 |
| 2025-01 | -7.20% | $-7,016.98 | $90,398.56 |
| 2025-02 | -5.84% | $-5,274.16 | $85,087.18 |
| 2025-03 | +0.00% | $+0.00 | $85,087.18 |
| 2025-04 | +0.00% | $+0.00 | $85,087.18 |
| 2025-05 | +4.86% | $+4,134.21 | $89,221.39 |
| 2025-06 | +5.13% | $+4,573.76 | $93,799.84 |
| 2025-07 | +5.19% | $+4,869.99 | $98,670.74 |
| 2025-08 | +7.62% | $+7,450.08 | $105,180.40 |
| 2025-09 | +10.46% | $+10,778.32 | $113,794.19 |
| 2025-10 | +0.84% | $+953.79 | $114,594.84 |
| 2025-11 | -4.61% | $-5,304.28 | $109,655.79 |
| 2025-12 | -4.27% | $-4,677.04 | $104,787.10 |

- **Positive months**: 9/16
- **Avg monthly return**: +0.73%
- **Best month**: +10.46%
- **Worst month**: -7.20%

## Notable Trades

- **Best**: AVGO on 2024-12-09 — $+4,751.25 (+25.64%)
- **Worst**: AVGO on 2025-01-17 — $-2,012.02 (-14.78%)

## Parameters Used

- **Momentum min**: 40.0
- **RS min**: 1.0
- **ATR stop multiplier**: 2.0x
- **Risk per trade**: 1.0%

## Recommendations

**VERDICT: MARGINAL EDGE** — positive but needs parameter tuning.

- Max drawdown exceeds 15% — reduce risk_pct or tighten stops
- Sharpe < 0.5 — returns are not well-compensated for risk taken

