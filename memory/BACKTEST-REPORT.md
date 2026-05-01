# Backtest Report

Generated: 2026-05-01 21:14

- **Capital**: $100,000
- **Symbols tested**: 22
- **Simulation days**: 310

## Performance Summary

| Metric | Value |
|---|---|
| Starting Capital | $100,000.00 |
| Ending Capital | $104,690.70 |
| Total Return | +4.69% |
| Total P&L | $+15,702.66 |
| CAGR | 3.80% |

## Trade Statistics

| Metric | Value |
|---|---|
| Total Trades | 133 |
| Winners | 69 |
| Losers | 64 |
| Win Rate | 51.9% |
| Profit Factor | 1.45 |
| Avg Winner | $+730.65 (+6.01%) |
| Avg Loser | $542.38 (3.36%) |
| Win/Loss Ratio | 1.35 |
| Expectancy | $+118.07/trade |

## Risk Metrics

| Metric | Value |
|---|---|
| Max Drawdown | 17.66% |
| Sharpe Ratio | -0.01 |
| Sortino Ratio | -0.01 |
| Max Consecutive Wins | 6 |
| Max Consecutive Losses | 7 |
| Avg Hold (all) | 9.1 days |
| Avg Hold (winners) | 13.8 days |
| Avg Hold (losers) | 4.1 days |

## Regime Breakdown

| Regime | Trades | Total P&L | Win Rate |
|---|---|---|---|
| BULL_QUIET | 123 | $+10,843.00 | 49.6% |
| BULL_VOLATILE | 10 | $+4,859.66 | 80.0% |

## Strategy Breakdown (setup_tag)

| Setup | Trades | Total P&L | Win Rate | Avg P&L |
|---|---|---|---|---|
| momentum | 106 | $+16,793.87 | 55.7% | $+158.43 |
| pead | 27 | $-1,091.21 | 37.0% | $-40.42 |

## Exit Reason Breakdown

| Exit Reason | Count | Total P&L | Avg P&L |
|---|---|---|---|
| stop | 24 | $+14,393.52 | $+599.73 |
| partial_complete | 1 | $+4,706.00 | $+4,706.00 |
| target | 5 | $+1,761.03 | $+352.21 |
| regime_shift | 18 | $+1,073.25 | $+59.63 |
| time_decay | 80 | $+692.20 | $+8.65 |
| hard_stop | 5 | $-6,923.34 | $-1,384.67 |

## Monthly Returns

| Month | Return | P&L | Ending Equity |
|---|---|---|---|
| 2024-09 | -1.03% | $-1,028.90 | $98,971.10 |
| 2024-10 | -2.45% | $-2,385.20 | $95,127.69 |
| 2024-11 | +0.74% | $+708.77 | $96,067.51 |
| 2024-12 | +1.05% | $+1,001.09 | $96,656.88 |
| 2025-01 | -7.11% | $-6,873.78 | $89,783.10 |
| 2025-02 | -5.76% | $-5,171.62 | $84,575.50 |
| 2025-03 | +0.00% | $+0.00 | $84,575.50 |
| 2025-04 | +0.00% | $+0.00 | $84,575.50 |
| 2025-05 | +4.89% | $+4,133.07 | $88,708.57 |
| 2025-06 | +5.11% | $+4,530.77 | $93,244.03 |
| 2025-07 | +5.20% | $+4,850.48 | $98,080.95 |
| 2025-08 | +7.62% | $+7,405.83 | $104,553.31 |
| 2025-09 | +10.44% | $+10,692.90 | $113,096.77 |
| 2025-10 | +0.81% | $+910.59 | $113,849.04 |
| 2025-11 | -4.64% | $-5,300.58 | $108,914.69 |
| 2025-12 | -3.71% | $-4,032.34 | $104,690.70 |

- **Positive months**: 8/16
- **Avg monthly return**: +0.70%
- **Best month**: +10.44%
- **Worst month**: -7.11%

## Notable Trades

- **Best**: AVGO on 2024-12-09 — $+4,706.00 (+25.64%)
- **Worst**: AVGO on 2025-01-17 — $-1,977.33 (-14.78%)

## Parameters Used

- **Momentum min**: 40.0
- **RS min**: 1.0
- **ATR stop multiplier**: 2.0x
- **Risk per trade**: 1.0%

## Recommendations

**VERDICT: MARGINAL EDGE** — positive but needs parameter tuning.

- Max drawdown exceeds 15% — reduce risk_pct or tighten stops
- Sharpe < 0.5 — returns are not well-compensated for risk taken

