# Project Context

## Mission
Generate $5,000–$10,000/month by end of 2026 via autonomous trading + paid signals subscription.
Approach: "Shark Signals" — trading income + subscription revenue combined.

## Current Status
- **Mode:** PAPER_TRADING
- **Circuit breaker:** INACTIVE
- **Weekly trade count:** 0 / 3 max (resets each Monday)
- **Paper trading since:** 2026-04-25
- **Go-live target:** 2026-05-23 (after 4 weeks clean paper trading)

## Account Snapshot
- Last updated: 2026-04-25
- Portfolio value: [update each EOD run]
- Peak equity (rolling high): [update when new high set]
- Open positions: 0
- Available cash: [update each EOD run]

## Signals Business Status
- Subscriber count: 0
- Platform: Not yet launched (target: after 4 weeks paper trading)
- Revenue: $0/month

## API Configuration
- Alpaca: Paper trading endpoint active
- Perplexity: CONFIGURED (sonar-pro validated 2026-04-25)
- Anthropic: [set ANTHROPIC_API_KEY in routine env vars]
- SendGrid: [set SENDGRID_API_KEY and NOTIFY_EMAIL in routine env vars]

## Cloud Routines Status
| Routine | Cron (ET) | Status |
|---|---|---|
| pre-market.md | 6:00 AM Mon-Fri | ACTIVE (Idle) |
| pre-execute.md | 9:45 AM Mon-Fri | ACTIVE (Idle) |
| market-open.md | 10:00 AM Mon-Fri | ACTIVE (Idle) |
| midday.md | 1:00 PM Mon-Fri | ACTIVE (Idle) |
| daily-summary.md | 4:15 PM Mon-Fri | ACTIVE (Idle) |
| weekly-review.md | 5:00 PM Fri | ACTIVE (Idle — next run May 1) |

## Sector Failure Tracking
| Sector | Consecutive Failures | Status |
|---|---|---|
| Technology | 0 | OK |
| Financials | 0 | OK |
| Healthcare | 0 | OK |
| Energy | 0 | OK |
| Consumer Discretionary | 0 | OK |

## Circuit Breaker Logic
- Trigger threshold: portfolio < (peak_equity × 0.85)
- Current status: INACTIVE
- To reset: review trades, fix strategy issue, manually set status to INACTIVE here

## Notes
- Repository: shark-trading-agent (GitHub)
- All state is in this memory/ directory — committed after every routine run
- Hard rules enforced by both Python guardrails (shark/execution/guardrails.py) AND this CLAUDE.md

## Machine-Readable State (parsed by shark/memory/state.py — do not reformat these lines)
circuit_breaker_triggered: false
peak_equity: 100000.00
weekly_trade_count: 0
weekly_trade_count_reset: 2026-04-28
current_mode: paper
