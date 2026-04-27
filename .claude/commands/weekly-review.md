# /weekly-review — Weekly Performance Review

Run Friday at 5:00 PM ET. Grade the week, update strategy, decide watchlist changes.

## Steps

1. Read memory/TRADE-LOG.md — extract all trades from this week (Mon-Fri)
2. Read memory/RESEARCH-LOG.md — extract all research from this week
3. Read memory/TRADING-STRATEGY.md — current watchlist and sector allocations
4. Get current account:
   ```bash
   bash scripts/alpaca.sh account
   ```

5. Calculate weekly stats:
   - Trades placed: [N] (limit 3)
   - Win rate: [wins]/[total] ([PCT]%)
   - Total realized P&L: $[AMOUNT]
   - Best trade: [TICKER] +[PCT]%
   - Worst trade: [TICKER] -[PCT]%
   - Portfolio vs S&P 500 this week (use Perplexity for SPY weekly return)

6. Grade the week: A (>5%), B (2-5%), C (0-2%), D (-2-0%), F (<-2%)

7. Strategy mutation check (only if grade <= C for 2 consecutive weeks):
   - Is the core strategy broken or was this market conditions?
   - Max one adjustment: sector rotation, watchlist refresh, or position sizing tweak
   - Document the change and reason in TRADING-STRATEGY.md

8. Update memory/WEEKLY-REVIEW.md:
   ```
   ## Week of [DATE]
   **Grade:** [LETTER] | **P&L:** $[AMOUNT] ([PCT]%)
   **vs S&P:** [+/-PCT]pp alpha
   **Trades:** [N] placed, [N] closed, [WIN_RATE]% win rate
   **Top winner:** [TICKER] +[PCT]%
   **Top loser:** [TICKER] -[PCT]%
   **Strategy notes:** [one sentence on what worked/didn't]
   **Next week focus:** [sectors/themes to watch]
   ```

9. Send email via Gmail connector (use Gmail MCP `send_email` tool — do NOT use notify.sh in cloud):
   - **to:** sharkwaveai@gmail.com
   - **subject:** `Shark Weekly [DATE]: Grade [LETTER] | $[P&L] | vs SPY [ALPHA]pp`
   - **body (HTML):** Dark-themed email:
     - Header: grade, week P&L, alpha vs SPY
     - Table: all trades this week — ticker, side, entry, exit, P&L $/%
     - Open positions summary
     - Strategy notes for next week
   - Fallback if Gmail MCP unavailable: `bash scripts/notify.sh "Shark Weekly [DATE]: Grade [LETTER] | P&L $[AMOUNT] | vs SPY [ALPHA]pp"`

10. Git commit:
    ```bash
    git add memory/ && git commit -m "review: weekly [DATE] grade=[LETTER] pnl=[AMOUNT]" && git push origin main
    ```
