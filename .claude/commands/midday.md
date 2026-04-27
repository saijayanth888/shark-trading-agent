# /midday — Midday Portfolio Scan

Run at 1:00 PM ET. Check all open positions, cut hard losers, tighten stops on winners.

## Steps

1. Get current positions:
   ```bash
   bash scripts/alpaca.sh positions
   ```

2. For each open position, calculate unrealized P&L %:
   - If unrealized_pct <= -7%: **CLOSE IMMEDIATELY, no exceptions**
     ```bash
     bash scripts/alpaca.sh close <TICKER>
     ```
     Log: `[DATE] 13:00 STOPPED OUT [TICKER] @ $[PRICE] | P&L: [%] | Reason: -7% hard stop`

3. Check thesis validity for remaining positions:
   ```bash
   bash scripts/perplexity.sh "Quick thesis check for [TICKERS]: any major news or catalyst invalidation in the last 4 hours? Return JSON: {ticker: {valid: bool, reason: str}}"
   ```
   - If thesis broken (even if not at -7%): close position, log reason

4. Tighten trailing stops on winners:
   - Position up >= +20%: update trail to 5%
   - Position up >= +15%: update trail to 7%
   ```bash
   bash scripts/alpaca.sh order <TICKER> <SHARES> trailing_stop <NEW_PCT>
   ```
   (Cancel existing trailing stop order first, then place new one)

5. Append to memory/TRADE-LOG.md:
   ```
   ## [DATE] 13:00 Midday Scan
   - Positions reviewed: [N]
   - Stopped out: [TICKERS or none]
   - Stops tightened: [TICKERS or none]
   - Thesis broken exits: [TICKERS or none]
   ```

6. Send alert via Gmail connector only if action was taken (stopped out OR thesis broken):
   - Use Gmail MCP `send_email` tool
   - **to:** sharkwaveai@gmail.com
   - **subject:** `Shark Alert [DATE] 13:00: [ACTION SUMMARY]`
   - **body:** What was closed and why, remaining positions with current P&L
   - Skip email entirely if no positions were closed or stops tightened

7. Git commit:
   ```bash
   git add memory/TRADE-LOG.md && git commit -m "scan: midday [DATE]" && git push origin main
   ```
