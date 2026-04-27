# /market-open — Market Open Execution

Execute trades from today's pre-market research. Run at 10:00 AM ET after 30-minute open settles.

## Steps

1. Read memory/RESEARCH-LOG.md (last 40 lines) — get today's top candidates
2. Read memory/TRADE-LOG.md (last 20 lines) — count open positions and this-week trades
3. Read memory/PROJECT-CONTEXT.md — confirm mode and circuit breaker status

**Hard gate check before any trade:**
```bash
bash scripts/alpaca.sh account
```
- Open positions <= 5 (leaving room for this trade)
- Weekly trade count < 3
- Cash >= 15% of equity
- Circuit breaker NOT active

If any gate fails: log reason and exit. No trades today.

4. For each candidate from pre-market research, in conviction order:

   a. Get live quote:
   ```bash
   bash scripts/alpaca.sh quote <TICKER>
   ```

   b. Run full analysis:
   ```python
   python -c "
   import asyncio
   from shark.agents.analyst_bull import generate_bull_thesis
   from shark.agents.analyst_bear import generate_bear_thesis
   from shark.agents.risk_manager import check_risk
   from shark.agents.decision_arbiter import make_decision
   # ... see routines/market-open.md for full workflow
   "
   ```

   c. If decision == BUY and confidence >= 0.70:
   ```bash
   bash scripts/alpaca.sh order <TICKER> <SHARES> buy
   # Then immediately set trailing stop:
   bash scripts/alpaca.sh order <TICKER> <SHARES> trailing_stop 10
   ```

5. After all trades: update memory/TRADE-LOG.md with each new position:
   ```
   [DATE] BUY [TICKER] [SHARES] @ $[PRICE] | Stop: 10% trail | Thesis: [one line]
   ```

6. Send trade alert via Gmail connector (call Gmail MCP `create_draft` to build the draft, then immediately call `send_draft` with the returned draft ID — do NOT stop at draft):
   - **to:** sharkwaveai@gmail.com
   - **subject:** `Shark Trade [DATE]: [TICKERS bought or "No trades"] | [N] positions open`
   - **body:** Bullet list of each trade placed — ticker, shares, price, stop level, thesis. If no trades, state reason (gate failed / no qualifying candidates).
   - Fallback if Gmail MCP unavailable: skip (non-critical for market-open)

7. Git commit:
   ```bash
   git add memory/ && git commit -m "trades: market-open [DATE] — [TICKERS or 'no trades']" && git push origin HEAD:main
   ```
