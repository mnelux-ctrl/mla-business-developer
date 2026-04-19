"""scheduler/jobs.py — Heir strategic jobs.

Three jobs:
  1. weekly_strategic_review()  — Mon 09:00 Europe/Podgorica
  2. finance_pulse_report()     — Fri 09:15 Europe/Podgorica
  3. opportunity_scan()         — Daily 10:00 Europe/Podgorica

Stefan budget rule: all three jobs now run on GPT-5.4 (not Opus/Sonnet).
Switched 2026-04-19. Claude reserved for Viktorija draft_full_proposal only.

Each job:
  - Builds the prompt from live data (finance pulse, SK, sibling services)
  - Calls OpenAI GPT-5.4
  - Posts result to Stefan's Slack DM (via TM relay preferred)
  - Logs output to Redis under a job-specific key
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

import config
from finance.pulse import get_pulse
from state import redis_client
from superknowledge import client as sk

logger = logging.getLogger(__name__)


async def _openai_chat(system: str, user: str, max_tokens: int = 2500) -> str:
    """Call GPT-5.4 chat completion. Returns content text or empty string."""
    api_key = getattr(config, "OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set — Heir GPT call skipped")
        return ""
    model = getattr(config, "BD_REASONER_MODEL", "gpt-5.4")
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_completion_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
            )
            if r.status_code != 200:
                logger.warning(f"GPT call non-200: {r.status_code} {r.text[:300]}")
                return ""
            data = r.json()
            choices = data.get("choices", [])
            if not choices:
                return ""
            return (choices[0].get("message") or {}).get("content", "").strip()
    except Exception as e:
        logger.warning(f"GPT call failed: {e}")
        return ""


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

    text = await _openai_chat(system_prompt, user_prompt, max_tokens=4000)
    if not text:
        text = "(Heir weekly review failed — check OPENAI_API_KEY or logs)"

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

    text = await _openai_chat(system_prompt, user_prompt, max_tokens=2500)
    if not text:
        text = "(Finance pulse report failed — check OPENAI_API_KEY or logs)"

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

    text = await _openai_chat(system_prompt, user_prompt, max_tokens=2500)
    if not text:
        text = "(Opportunity scan failed — check OPENAI_API_KEY or logs)"

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


def _safe_json(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, ensure_ascii=False, default=str, indent=2)[:4000]
    except Exception:
        return str(obj)[:4000]
