"""scheduler/jobs.py — Heir strategic jobs.

Three jobs:
  1. weekly_strategic_review()  — Mon 09:00 Europe/Podgorica (Opus)
  2. finance_pulse_report()     — Fri 09:15 Europe/Podgorica (Sonnet)
  3. opportunity_scan()         — Daily 10:00 Europe/Podgorica (Sonnet)

Each job:
  - Builds the prompt from live data (finance pulse, SK, sibling services)
  - Calls Anthropic with appropriate model
  - Posts result to Stefan's Slack DM (if configured)
  - Logs the output to Redis under a job-specific key
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from anthropic import AsyncAnthropic

import config
from finance.pulse import get_pulse
from state import redis_client
from superknowledge import client as sk

logger = logging.getLogger(__name__)

_client: Optional[AsyncAnthropic] = None


def _anthropic() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


# ── Weekly strategic review (Monday) ──────────────────────────────────

async def weekly_strategic_review(
    focus: Optional[str] = None,
    source: str = "scheduler",
) -> dict:
    """Generate the Mon 09:00 strategic brief."""
    pulse = await get_pulse(months_back=3)
    sk_ctx = await sk.load_strategic_context()
    recent_recs = await redis_client.get_recent_recommendations(n=10)

    apps_summary = await _grant_applications_summary()

    focus_clause = (
        f"Focus this review on: {focus}" if focus else "Cover all three workstreams."
    )

    user_prompt = f"""You are Heir. Generate this week's strategic review for Stefan.

{focus_clause}

═══ FINANCE PULSE (pre-computed) ═══
{_safe_json(pulse)}

═══ GRANT APPLICATIONS ═══
{_safe_json(apps_summary)}

═══ RECENT RECOMMENDATIONS YOU'VE LOGGED ═══
{_safe_json(recent_recs)}

═══ TASK ═══
Produce a Markdown brief with these sections:
1. **Headline** — one-sentence verdict on MLA's financial position
2. **€200k deficit recovery** — progress since last review, in absolute €
3. **Workstream status** — Grants / BI-SaaS / Revenue-diversification, one paragraph each
4. **3 concrete next actions for Stefan this week** — numbered, with owner and deadline
5. **Risks** — at most 3 bullets

Tone: senior operator, Montenegrin if SK context suggests Stefan last wrote
in Montenegrin, otherwise English. Short sentences. No corporate mush."""

    system_prompt = _system_with_sk(sk_ctx)

    try:
        client = _anthropic()
        resp = await client.messages.create(
            model=config.BD_OPUS_MODEL,
            max_tokens=4000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _extract_text(resp.content) or "(no content)"
    except Exception as e:
        logger.exception("weekly_strategic_review LLM call failed")
        text = f"(Heir weekly review failed: {type(e).__name__}: {e})"

    item = {
        "type": "weekly_strategic_review",
        "source": source,
        "focus": focus,
        "body": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.log_recommendation(item)
    await redis_client.set_cache("last_weekly_review", item, ttl=14 * 86400)

    sent, slack_err = await _try_dm_stefan(
        f"*Heir — Weekly Strategic Review*\n\n{text}"
    )
    return {"text": text, "slack_sent": sent, "slack_error": slack_err}


# ── Finance pulse (Friday) ────────────────────────────────────────────

async def finance_pulse_report(source: str = "scheduler") -> dict:
    pulse = await get_pulse(months_back=3)
    sk_ctx = await sk.load_strategic_context()
    last_pulse = await redis_client.get_cache("last_finance_pulse")

    user_prompt = f"""You are Heir. Produce the Friday 09:15 finance pulse for Stefan.

═══ THIS WEEK'S SNAPSHOT ═══
{_safe_json(pulse)}

═══ LAST WEEK'S SNAPSHOT (for delta) ═══
{_safe_json(last_pulse) if last_pulse else '(none — first run)'}

═══ TASK ═══
Produce a Markdown report with:
1. **Headline** — cash position delta in €, runway in months
2. **Receivables red flags** — any bucket over €5k in 61_90 or 90_plus
3. **Burn trajectory** — vs last week, any abnormal expenses?
4. **Grant pipeline** — changes this week
5. **One-line recommendation** — what Stefan should do before end of next week

Short. Numbers first. If any alert fires, start the message with 🚨."""

    system_prompt = _system_with_sk(sk_ctx)

    try:
        client = _anthropic()
        resp = await client.messages.create(
            model=config.BD_SONNET_MODEL,
            max_tokens=2500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _extract_text(resp.content) or "(no content)"
    except Exception as e:
        logger.exception("finance_pulse_report LLM call failed")
        text = f"(Finance pulse report failed: {type(e).__name__}: {e})"

    item = {
        "type": "finance_pulse",
        "source": source,
        "pulse": pulse,
        "body": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.log_recommendation(item)
    await redis_client.set_cache("last_finance_pulse", pulse, ttl=14 * 86400)

    sent, slack_err = await _try_dm_stefan(
        f"*Heir — Friday Finance Pulse*\n\n{text}"
    )
    return {"text": text, "pulse": pulse, "slack_sent": sent, "slack_error": slack_err}


# ── Opportunity scan (daily) ──────────────────────────────────────────

async def opportunity_scan(
    query: Optional[str] = None,
    max_results: int = 8,
    source: str = "scheduler",
) -> dict:
    query = query or "Montenegro luxury tourism business development BI automation"
    tavily_results = await _tavily_search(query, max_results=max_results)
    sk_ctx = await sk.load_strategic_context()

    user_prompt = f"""You are Heir scanning for revenue opportunities.

═══ EXTERNAL SIGNAL (Tavily) ═══
{_safe_json(tavily_results)}

═══ TASK ═══
From the signal above, extract a RANKED list (highest revenue potential first) of:
- BI-as-a-service leads (Montenegro businesses that could buy MLA's automation stack)
- Partnership angles (luxury tourism operators we don't yet have)
- Sponsorship / event opportunities

For each, output:
- **Name** / opportunity title
- **Why relevant** (1 line)
- **Estimated €-potential** (your best guess; mark "rough" if uncertain)
- **Heir's recommended next action** (1 line)

If nothing interesting, say so — don't fabricate."""

    system_prompt = _system_with_sk(sk_ctx)

    try:
        client = _anthropic()
        resp = await client.messages.create(
            model=config.BD_SONNET_MODEL,
            max_tokens=2500,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = _extract_text(resp.content) or "(no content)"
    except Exception as e:
        logger.exception("opportunity_scan LLM call failed")
        text = f"(Opportunity scan failed: {type(e).__name__}: {e})"

    item = {
        "type": "opportunity_scan",
        "source": source,
        "query": query,
        "body": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.log_recommendation(item)
    return {"text": text, "query": query}


# ── Helpers ───────────────────────────────────────────────────────────

async def _grant_applications_summary() -> dict:
    import httpx
    if not config.GRANT_WRITER_URL:
        return {"error": "GRANT_WRITER_URL not configured"}
    headers = {}
    if getattr(config, "GRANT_WRITER_API_KEY", ""):
        headers["Authorization"] = f"Bearer {config.GRANT_WRITER_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{config.GRANT_WRITER_URL.rstrip('/')}/api/applications",
                headers=headers,
            )
            if r.status_code != 200:
                return {"error": f"grant-writer HTTP {r.status_code}"}
            return r.json() or {}
    except Exception as e:
        return {"error": f"grant-writer unreachable: {e}"}


async def _tavily_search(query: str, max_results: int = 8) -> list:
    if not getattr(config, "TAVILY_API_KEY", ""):
        return [{"error": "TAVILY_API_KEY not set"}]
    import httpx
    payload = {
        "api_key": config.TAVILY_API_KEY,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post("https://api.tavily.com/search", json=payload)
            if r.status_code != 200:
                return [{"error": f"Tavily HTTP {r.status_code}"}]
            data = r.json() or {}
            return data.get("results", [])
    except Exception as e:
        return [{"error": f"Tavily failed: {e}"}]


async def _try_dm_stefan(text: str) -> tuple[bool, Optional[str]]:
    try:
        from slack.notify import send_dm_to_stefan
        ok = await send_dm_to_stefan(text)
        return (ok, None)
    except Exception as e:
        return (False, str(e))


def _system_with_sk(sk_ctx: str) -> str:
    from brain.heir import PERSONA
    parts = [PERSONA]
    if sk_ctx:
        parts.append(sk_ctx)
    return "\n\n".join(parts)


def _extract_text(content_blocks: list) -> str:
    parts: list[str] = []
    for b in content_blocks:
        if getattr(b, "type", None) == "text":
            parts.append(b.text)
    return "\n".join(parts).strip()


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, default=str, indent=2)[:4000]
    except Exception:
        return str(obj)[:4000]
