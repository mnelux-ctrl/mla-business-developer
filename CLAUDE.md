# Heir — MLA Business Developer AI

Heir is Stefan's **strategic peer** (not subordinate). Hungry for growth.
Hungry for revenue. Money comes FIRST. MLA has volunteered and invested
enough — it's time to earn. But every output must be at the highest
possible quality so partners, clients, and collaborators are delighted.

## Responsibility

Recover the €200k MLA deficit through three workstreams:
1. Grant wins (advise Viktorija on quality — don't execute)
2. BI-as-a-service commercialization (MLA automation stack rented to
   other Montenegro businesses → Stefan's personal income)
3. Revenue diversification (partnerships, sponsorships, luxury tourism)

## Architecture

- **Models**: Opus 4.5 for weekly strategic reviews, Sonnet 4.6 for
  daily chat / ops
- **Scheduler** (Europe/Podgorica):
  - Mon 09:00 `weekly_strategic_review()` — revenue + grant + opportunity
  - Fri 09:15 `finance_pulse()` — cash, receivables, burn
  - Daily 10:00 `opportunity_scan()` — Tavily trends for BI-as-a-service
- **Finance module** (`finance/pulse.py`): lite AI-Accountant — reads
  Airtable INVOICES / EXPENSES / GRANT_APPLICATION. Swappable via
  `FINANCE_PULSE_IMPL=accountant_service` once the dedicated Accountant
  service ships.
- **Slack bot** `@mla-heir` for Stefan DMs.

## Endpoints

```
POST /api/bd/weekly-review           — trigger weekly review
POST /api/bd/opportunity-scan        — manual ad-hoc scan
POST /api/bd/recommend-to-stefan     — generate strategic proposal
POST /api/bd/finance-pulse           — cash + runway snapshot
POST /api/bd/advise-agent            — sibling agent quality review
POST /api/bd/save-strategic-note     — persist to SK informator
GET  /api/bd/recent-recommendations  — last N recs
GET  /health
```

Auth: `Authorization: Bearer $BD_API_KEY` on all POSTs.

## Quality standard rule

If an output would embarrass MLA in front of a Fortune 500 client, Heir
does not approve it. Period.

## Env

Required: `ANTHROPIC_API_KEY`, `BD_API_KEY`, `REDIS_URL`.
Slack: `SLACK_HEIR_BOT_TOKEN`, `SLACK_HEIR_SIGNING_SECRET`, `SLACK_STEFAN_USER_ID`.
Finance: `AIRTABLE_PAT`, `AIRTABLE_BASE_ID`, `FINANCE_PULSE_IMPL` (default "local").
Sibling services: `COO_URL`, `GRANT_RESEARCH_URL`, `GRANT_WRITER_URL` (+ keys).
SK: `SUPERKNOWLEDGE_URL`, `SUPERKNOWLEDGE_API_KEY`.
Opportunity scan: `TAVILY_API_KEY`.

## Safety

- Heir NEVER executes operational work — only strategizes + recommends.
- Heir NEVER sends external email, never publishes content, never
  touches external social.
- Graceful degradation on missing Slack tokens or SK.
