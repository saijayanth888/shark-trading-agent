# /daily-summary — End-of-Day Portfolio Snapshot

Run at 4:15 PM ET after market close. Snapshot portfolio state and send email digest.

## Steps

1. Get final account state:
   ```bash
   bash scripts/alpaca.sh account
   bash scripts/alpaca.sh positions
   ```

2. Calculate today's performance:
   - Portfolio value vs yesterday's close
   - Each position: entry price, current price, unrealized P&L $/%
   - Realized P&L from any trades closed today

3. Check circuit breaker: if portfolio value < (peak_equity × 0.85), activate circuit breaker
   - Update memory/PROJECT-CONTEXT.md: set `circuit_breaker: ACTIVE`

4. Write daily summary to memory/TRADE-LOG.md:
   ```
   ## [DATE] EOD Summary
   **Portfolio:** $[VALUE] ([+/-CHANGE] / [+/-PCT]% today)
   **Open positions:** [N]
   [TICKER]: [SHARES] @ $[ENTRY] → $[CURRENT] | [P&L$] ([P&L%])
   **Realized today:** $[AMOUNT]
   **Cash:** $[AMOUNT] ([PCT]%)
   **Circuit breaker:** [ACTIVE/INACTIVE]
   ```

5. Send email via Gmail connector (call Gmail MCP `create_draft` to build the draft, then immediately call `send_draft` with the returned draft ID to actually send it — do NOT stop at draft, do NOT use notify.sh in cloud):
   - **to:** sharkwaveai@gmail.com
   - **subject:** `Shark EOD [DATE]: $[VALUE] ([+/-PCT]% today) | [N] positions | Cash [PCT]%`
   - **body (HTML):** Dark-themed email:
     - Header: portfolio value + day P&L in $ and %
     - Table: each position → ticker, shares, entry $, current $, unrealized P&L $ and %
     - Footer: cash $, cash %, circuit breaker status, weekly trade count
   - Fallback if Gmail MCP unavailable: `bash scripts/notify.sh "Shark EOD [DATE]: $[VALUE] ([PCT]% today)"`

6. Git commit (MANDATORY — data is lost if not committed):
   ```bash
   git add memory/ && git commit -m "eod: daily snapshot [DATE] portfolio=[VALUE]" && git push origin HEAD:main
   ```
   If push fails: retry up to 3 times with `git pull --rebase origin main && git push origin HEAD:main`
