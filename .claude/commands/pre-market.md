# /pre-market — Pre-Market Research

Run full pre-market research cycle. Use this locally to preview what the 6am routine will do.

## Steps

1. Read memory/TRADING-STRATEGY.md — get current watchlist
2. Read memory/PROJECT-CONTEXT.md — check mode (paper/live) and circuit breaker status
3. Read memory/TRADE-LOG.md (last 20 lines) — current open positions

**If circuit breaker is active, stop here.** Log to RESEARCH-LOG.md and exit.

4. Run market intel for watchlist tickers:
   ```bash
   bash scripts/perplexity.sh "Pre-market research for: AAPL MSFT NVDA TSLA AMZN. For each: sentiment (bullish/bearish/neutral), key catalyst today, headline risk, price target. Return JSON."
   ```

5. For each ticker with a catalyst:
   ```bash
   bash scripts/alpaca.sh bars <TICKER> 1Day 30
   ```
   Assess trend: above SMA20? Volume surge? RSI direction?

6. Score each opportunity: catalyst strength (1-5) × momentum alignment (1-5) = conviction score

7. Select top 3 candidates (conviction >= 15). For each, write 3-bullet thesis:
   - Catalyst: [what/why today]
   - Technical: [price action context]
   - Risk: [what invalidates]

8. Append to memory/RESEARCH-LOG.md:
   ```
   ## [DATE] Pre-Market Research
   **Watchlist scanned:** [tickers]
   **Top candidates:**
   - [TICKER]: conviction [score] — [one-line thesis]
   **Rejected:** [tickers with reason]
   ```

9. Send research summary via Gmail connector (call Gmail MCP `create_draft` to build the draft, then immediately call `send_draft` with the returned draft ID — do NOT stop at draft):
   - **to:** sharkwaveai@gmail.com
   - **subject:** `Shark Pre-Market [DATE]: [N] candidates | Top: [TICKER] (conviction [score])`
   - **body:** Top 3 candidates with thesis bullets, rejected tickers with reason
   - Fallback if Gmail MCP unavailable: skip (research is in RESEARCH-LOG.md)

10. Git commit:
    ```bash
    git add memory/RESEARCH-LOG.md && git commit -m "research: pre-market [DATE]" && git push origin HEAD:main
    ```
