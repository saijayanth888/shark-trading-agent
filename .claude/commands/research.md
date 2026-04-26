---
description: On-demand research for a ticker. Usage: /research SYMBOL
---
Run full research for a ticker.

1. Ask for SYMBOL if not provided
2. bash scripts/perplexity.sh "[SYMBOL] stock news catalysts analysis today"
3. bash scripts/alpaca.sh bars SYMBOL 1Day 60
4. Compute: SMA20, SMA50, RSI14, volume vs avg
5. Output:
   **[SYMBOL] Research — [date]**
   Sentiment: [score] | Sector momentum: [up/down/neutral]
   Technical: Price $X | SMA20 $X | SMA50 $X | RSI [X]
   Catalysts: [list]
   Risks: [list]
   Entry zone: $X–$X | Stop: $X | Target: $X | R:R: X:1
   Decision: [TRADE / WATCH / PASS]
