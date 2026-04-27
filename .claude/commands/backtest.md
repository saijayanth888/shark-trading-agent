# /backtest — Weekly Backtest

Runs the weekly backtesting phase locally. Simulates the current strategy against the last 12 months of historical data. Generates BACKTEST-REPORT.md with metrics (Sharpe, max drawdown, win rate, profit factor, alpha vs SPY) and parameter recommendations.

## Run

```bash
python shark/run.py backtest
```

Python handles everything: git pull → load 12 months of bars → bar-by-bar simulation → regime detection → RS filtering → position sizing → exit logic → BACKTEST-REPORT.md → git commit + push.

## Dry Run (preview without writing report)

```bash
python shark/run.py backtest --dry-run
```

## Tune Parameters (override via env vars)

```bash
BACKTEST_CAPITAL=50000 BACKTEST_RS_MIN=1.5 python shark/run.py backtest
```

## On Error

```bash
tail -30 memory/error.log
```

Common cause: Alpaca data API rate limit or missing bars for tickers. Check error.log for the failing ticker.
