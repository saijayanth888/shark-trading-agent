---
description: Manual trade with full rule validation. Usage: /trade SYMBOL SHARES buy|sell
---
Execute a manual trade. Refuse if any rule fails.

Args: SYMBOL SHARES SIDE. Ask if missing.

1. Pull: bash scripts/alpaca.sh account; bash scripts/alpaca.sh positions; bash scripts/alpaca.sh quote SYMBOL
2. Run buy-side gate: positions <=6, trades this week <=3, cost <=20% equity, cash buffer >=15%, circuit breaker off
3. Ask for catalyst if not in today's RESEARCH-LOG.md
4. Print order JSON + validation result. Ask "Execute? (y/n)"
5. On y: bash scripts/alpaca.sh order '{"symbol":"...","qty":"...","side":"...","type":"market","time_in_force":"day"}'
6. Place 10% trailing stop GTC immediately after fill
7. Append to memory/TRADE-LOG.md
8. bash scripts/notify.sh "Shark Manual Trade" "[details]"
