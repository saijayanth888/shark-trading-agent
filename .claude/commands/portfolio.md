---
description: Read-only snapshot of Alpaca account, positions, open orders, and stops
---
Show current portfolio status. No trades, no file writes.

1. bash scripts/alpaca.sh account
2. bash scripts/alpaca.sh positions
3. bash scripts/alpaca.sh orders

Format as clean summary:
Portfolio — [date]
Equity: $X | Cash: $X (X%) | Buying power: $X | Daytrade count: N

Positions:
  SYM | Qty | Entry → Now | Unrealized P&L | Stop %

Open orders:
  TYPE | SYM | Qty | Trail/Stop | Order ID

Warnings: flag any position without a stop, or stop below current price.
