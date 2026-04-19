"""brain/dispatch.py — Map tool_name -> async implementation.

Keeps implementations lazy-imported so a missing module (e.g. scheduler
not yet live) degrades gracefully.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def dispatch(tool_name: str, tool_input: dict) -> Any:
    """Route a tool call to its implementation and return JSON-serializable result."""
    try:
        # ── Shared doc reader ─────────────────────────────────────────
        if tool_name == "read_document":
            import asyncio
            import config
            from shared.doc_reader import read_document_url
            bot_token = getattr(config, "SLACK_HEIR_BOT_TOKEN", "") or ""
            try:
                res = await asyncio.to_thread(
                    read_document_url,
                    tool_input["url"],
                    slack_bot_token=bot_token or None,
                )
            except Exception as e:
                return {"success": False, "error": f"read_document raised: {e}"}
            if not res.get("ok"):
                return {"success": False, "error": res.get("error") or "unknown doc-reader error"}
            return {
                "success": True,
                "source": res.get("source"),
                "filename": res.get("filename"),
                "char_count": res.get("char_count"),
                "truncated": res.get("truncated"),
                "text": res.get("text", ""),
            }

        # ── Strategic reviews ──────────────────────────────────────────
        if tool_name == "run_weekly_strategic_review":
            from scheduler.jobs import weekly_strategic_review
            return await weekly_strategic_review(
                focus=tool_input.get("focus"),
                source="tool_call",
            )

        if tool_name == "run_opportunity_scan":
            from scheduler.jobs import opportunity_scan
            return await opportunity_scan(
                query=tool_input["query"],
                max_results=tool_input.get("max_results", 8),
            )

        # ── Finance ───────────────────────────────────────────────────
        if tool_name == "get_finance_pulse":
            from finance.pulse import get_pulse
            return await get_pulse(months_back=tool_input.get("months_back", 3))

        # ── Recommendations ────────────────────────────────────────────
        if tool_name == "recommend_to_stefan":
            return await _recommend_to_stefan(tool_input)

        if tool_name == "advise_agent":
            return await _advise_agent(tool_input)

        if tool_name == "get_recent_recommendations":
            from state import redis_client
            recs = await redis_client.get_recent_recommendations(
                n=tool_input.get("n", 10)
            )
            return {"recommendations": recs, "count": len(recs)}

        # ── SK loopback ────────────────────────────────────────────────
        if tool_name == "save_strategic_informator":
            from superknowledge import client as sk
            return await sk.save_strategic_informator(
                topic=tool_input["topic"],
                content=tool_input["content"],
                category=tool_input["category"],
                applies_to=tool_input.get("applies_to"),
            )

        if tool_name == "sk_recall":
            from superknowledge import client as sk
            entries = await sk.recall(
                query=tool_input["query"],
                categories=tool_input.get("categories"),
                max_results=tool_input.get("max_results", 10),
            )
            return {"entries": entries, "count": len(entries)}

        # ── Sibling visibility ─────────────────────────────────────────
        if tool_name == "list_grant_applications":
            return await _list_grant_applications()

        return {"error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        logger.exception(f"dispatch({tool_name}) failed")
        return {"error": f"{tool_name} failed: {type(e).__name__}: {e}"}


# ── Implementations that live inside brain/ ───────────────────────────

async def _recommend_to_stefan(args: dict) -> dict:
    """Log recommendation + DM Stefan with its body."""
    from datetime import datetime, timezone
    from state import redis_client

    item = {
        "topic": args["topic"],
        "body": args["body"],
        "impact_eur": args.get("impact_eur"),
        "deadline": args.get("deadline"),
        "status": "open",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.log_recommendation(item)

    sent, slack_err = False, None
    try:
        from slack.notify import send_dm_to_stefan
        text = _format_recommendation(item)
        sent = await send_dm_to_stefan(text)
    except Exception as e:
        slack_err = str(e)

    return {
        "logged": True,
        "slack_sent": sent,
        "slack_error": slack_err,
        "recommendation_id": item["created_at"],
    }


def _format_recommendation(item: dict) -> str:
    lines = [f"*Heir ↠ Stefan — Recommendation*", f"*Topic:* {item['topic']}"]
    if item.get("impact_eur") is not None:
        lines.append(f"*Impact:* €{item['impact_eur']:,.0f}")
    if item.get("deadline"):
        lines.append(f"*Decide by:* {item['deadline']}")
    lines.append("")
    lines.append(item["body"])
    return "\n".join(lines)


async def _advise_agent(args: dict) -> dict:
    """Synchronous review — returns a structured verdict.

    Heir's persona handles the actual reasoning; this tool just packages
    the review payload for logging and returning to the caller.
    """
    from datetime import datetime, timezone

    review = {
        "agent": args["agent"],
        "artifact_type": args.get("artifact_type", "unspecified"),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "content_preview": args["content"][:400],
        "note": (
            "Review issued by Heir. Full verdict returned inline by the model "
            "that called this tool — this payload is the audit trail only."
        ),
    }
    return review


async def _list_grant_applications() -> dict:
    import httpx
    import config

    if not config.GRANT_WRITER_URL:
        return {"error": "GRANT_WRITER_URL not configured"}

    headers = {}
    if config.GRANT_WRITER_API_KEY:
        headers["Authorization"] = f"Bearer {config.GRANT_WRITER_API_KEY}"

    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(
                f"{config.GRANT_WRITER_URL.rstrip('/')}/api/applications",
                headers=headers,
            )
            if r.status_code != 200:
                return {"error": f"grant-writer HTTP {r.status_code}"}
            return r.json() or {"applications": []}
    except Exception as e:
        return {"error": f"grant-writer unreachable: {e}"}
