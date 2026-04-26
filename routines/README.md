# Shark Agent — Cloud Routines

Five scheduled routines run Monday–Friday. Configure in Claude Code Cloud → Routines.

| Routine | File | Cron (America/New_York) | Time ET |
|---------|------|--------------------------|---------|
| Pre-market research | pre-market.md | `0 6 * * 1-5` | 6:00 AM |
| Pre-execute validation | pre-execute.md | `45 9 * * 1-5` | 9:45 AM |
| Market-open execution | market-open.md | `0 10 * * 1-5` | 10:00 AM |
| Midday scan | midday.md | `0 13 * * 1-5` | 1:00 PM |
| Daily summary | daily-summary.md | `15 16 * * 1-5` | 4:15 PM |
| Weekly review | weekly-review.md | `0 17 * * 5` | 5:00 PM Fri |

## Critical Setup (from Nate Herk guide)
1. Install the Claude GitHub App on this repo
2. Enable "Allow unrestricted branch pushes" on EVERY routine
3. Set ALL env vars on the routine (NOT in a .env file)

## Required Env Vars (set on each routine)
- ALPACA_API_KEY, ALPACA_SECRET_KEY, ALPACA_BASE_URL
- PERPLEXITY_API_KEY, PERPLEXITY_MODEL
- ANTHROPIC_API_KEY
- SENDGRID_API_KEY, NOTIFY_EMAIL, NOTIFY_FROM_EMAIL
- TRADING_MODE (paper or live)
