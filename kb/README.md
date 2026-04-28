# Shark Trading Agent — Knowledge Base (KB)

Self-contained historical trading intelligence stored as JSON files in this folder.
The KB is the **edge** that lets the agent score trades using accumulated wisdom
instead of starting from scratch every morning.

## Why JSON in Git?

- **No external database** — fully self-contained, deploys with the code
- **Git-tracked** — full audit trail of every change, easy rollback
- **Fast load** — pre-market routine reads cached stats instead of hitting APIs
- **Token-efficient** — Perplexity/Anthropic only called for fresh news, not base rates
- **Inspectable** — humans can `cat` any file to verify what the agent knows

## Folder Structure

```
kb/
├── universe/
│   └── sp500.json              # Current S&P 500 constituents (auto-updated)
├── historical_bars/
│   ├── _meta.json              # last_updated, ticker_count, source feed
│   ├── NVDA.json               # 504 daily bars (2 years) per ticker
│   └── ... (~503 ticker files)
├── trades/                     # One JSON per closed trade — the core data
│   └── 2026-04-25_NVDA_long.json
├── daily/                      # Daily market snapshots (predictions vs actuals)
│   └── 2026-04-28.json
├── earnings/                   # Per-ticker earnings reaction history
│   └── NVDA.json               # Last 8 quarters: surprise, gap, drift
├── events/                     # Macro events + market reactions
│   └── 2026-04-29_FOMC.json
├── patterns/                   # Auto-extracted statistical edges
│   ├── ticker_base_rates.json  # Win rate per (ticker, regime)
│   ├── calendar_effects.json   # PEAD, pre-FOMC drift, seasonality
│   ├── sector_rotation.json    # Sector momentum patterns
│   ├── anti_patterns.json      # KNOWN FAILURES — auto-reject setups
│   └── regime_outcomes.json    # Performance per regime type
└── lessons/                    # Auto-extracted actionable lessons
    └── compiled_lessons.json
```

## Refresh Cadence

| Routine | When | What |
|---|---|---|
| `kb-refresh` | Sunday 8 AM ET | Full rebuild: pull 504 bars × 503 tickers, recompute all patterns |
| `kb-update` | Daily 5:30 PM ET (Mon-Fri) | Append yesterday's bar, refresh rolling stats |
| `daily-summary` | Daily 4:15 PM ET (Mon-Fri) | Write today's trades to `kb/trades/` and snapshot to `kb/daily/` |
| `weekly-review` | Friday 5:00 PM ET | Trigger pattern re-extraction from accumulated trades |

## Schema References

### `kb/historical_bars/{TICKER}.json`
```json
{
  "symbol": "NVDA",
  "last_updated": "2026-04-28",
  "feed": "iex",
  "bars": [
    {"date": "2024-04-29", "o": 95.2, "h": 96.8, "l": 94.1, "c": 95.5, "v": 285000000},
    ...
  ]
}
```

### `kb/trades/{date}_{symbol}_{side}.json`
```json
{
  "ticker": "NVDA",
  "side": "long",
  "entry_date": "2026-04-25",
  "exit_date": "2026-04-28",
  "entry_price": 142.50,
  "exit_price": 148.20,
  "qty": 50,
  "pnl_dollars": 285.00,
  "pnl_pct": 4.0,
  "regime": "BULL_QUIET",
  "rs_score": 1.45,
  "sentiment_score": 0.7,
  "catalyst": "AI chip export ban lifted",
  "catalyst_specific": true,
  "exit_reason": "trailing_stop",
  "sector": "Technology",
  "pre_fomc": false,
  "earnings_within_days": null,
  "grade": "A",
  "lesson": "Specific catalyst + RS>1.4 + BULL_QUIET = high conviction"
}
```

### `kb/patterns/ticker_base_rates.json`
```json
{
  "NVDA": {
    "BULL_QUIET":    {"trades": 12, "wins": 9, "win_rate": 0.75, "avg_pnl": 4.2, "expectancy": 2.1},
    "BULL_VOLATILE": {"trades": 5,  "wins": 2, "win_rate": 0.40, "avg_pnl": -0.8, "expectancy": -0.3}
  }
}
```

### `kb/patterns/anti_patterns.json` (the gold mine)
```json
{
  "TSLA_GAP_UP_FADE": {
    "description": "TSLA gap-ups >2% fade by close",
    "occurrences": 8,
    "fade_rate": 0.875,
    "action": "REJECT entry on TSLA gaps >2%"
  }
}
```

### `kb/patterns/calendar_effects.json`
```json
{
  "pre_fomc_drift": {
    "description": "SPY return in 24h before FOMC",
    "n": 16,
    "avg_return_pct": 0.5,
    "win_rate": 0.69,
    "action": "Allow long entries day before FOMC if SPY in uptrend"
  },
  "post_earnings_drift": {
    "description": "60-day drift after positive earnings surprise >5%",
    "n": 142,
    "avg_return_pct": 3.2,
    "win_rate": 0.62,
    "action": "Hold winners 5-10 days post-beat"
  }
}
```

## Querying the KB

From any phase:
```python
from shark.data.knowledge_base import (
    load_ticker_base_rate,
    load_anti_patterns,
    load_calendar_edge,
    load_historical_bars,
)

base_rate = load_ticker_base_rate("NVDA", regime="BULL_QUIET")
# {"trades": 12, "win_rate": 0.75, "avg_pnl": 4.2}

anti = load_anti_patterns(symbol="TSLA", setup="GAP_UP")
# [{"action": "REJECT", "fade_rate": 0.875}]

bars = load_historical_bars("NVDA", days=60)
# pandas DataFrame ready for technical analysis
```

## Storage Footprint

| Layer | Size | Notes |
|---|---|---|
| Historical bars (503 tickers × 504 bars) | ~8 MB compressed | ~30 MB raw |
| Trade records (1 yr expected) | ~500 KB | ~250 trades |
| Daily snapshots (1 yr) | ~1.5 MB | 252 trading days |
| Pattern files | ~2 MB | Aggregated stats |
| **Total expected (1 yr in)** | **~12 MB** | Well within git limits |

## Maintenance

- **Never edit `historical_bars/` manually** — managed by `kb-refresh`
- **Never edit `patterns/` manually** — managed by `extract_patterns.py`
- **`trades/`, `daily/`, `events/`** — append-only, written by phases
- **Old data**: trades older than 2 years archived to `kb/archive/` quarterly

## See Also

- `shark/data/knowledge_base.py` — read/write API
- `shark/phases/kb_refresh.py` — Sunday full rebuild
- `shark/phases/kb_update.py` — daily incremental
- `scripts/extract_patterns.py` — pattern computation
- `routines/kb-refresh.md`, `routines/kb-update.md` — cloud routine prompts
