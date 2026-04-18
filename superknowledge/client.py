"""superknowledge/client.py — Heir's SK client.

Reads: strategic informators (org_vision, business_model, financial_context,
brand_identity, stefan_strategic_context) at startup.
Writes: save_strategic_informator when Stefan dictates strategic context
via Slack — Stage 5 loopback pattern.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

import config

logger = logging.getLogger(__name__)

STRATEGIC_CATEGORIES = [
    "org_vision",
    "financial_context",
    "business_model",
    "brand_identity",
    "org_structure",
    "stefan_strategic_context",
]


def _configured() -> bool:
    return bool(config.SUPERKNOWLEDGE_URL) and bool(config.SUPERKNOWLEDGE_API_KEY)


def _base() -> str:
    return config.SUPERKNOWLEDGE_URL.rstrip("/")


def _auth() -> dict:
    return {"Authorization": f"Bearer {config.SUPERKNOWLEDGE_API_KEY}"}


async def recall(
    query: str,
    categories: Optional[list[str]] = None,
    max_results: int = 10,
) -> list[dict]:
    """Recall SK entries scoped to `heir`."""
    if not _configured():
        return []
    payload = {
        "query": query,
        "agent": config.AGENT_NAME,
        "include_graph": False,
        "include_similar": True,
        "depth": 0,
        "max_results": max_results,
    }
    if categories:
        payload["categories"] = categories
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(f"{_base()}/api/recall", json=payload, headers=_auth())
            if r.status_code != 200:
                logger.warning(f"SK recall HTTP {r.status_code}: {r.text[:200]}")
                return []
            data = r.json() or {}
            return (
                data.get("knowledge_entries")
                or data.get("entries")
                or data.get("results")
                or []
            )
    except Exception as e:
        logger.warning(f"SK recall failed: {e}")
        return []


async def load_strategic_context() -> str:
    """Pull all strategic informators at startup. Returns a text block
    suitable for injecting into Heir's system prompt.
    """
    entries = await recall(
        query="organizational strategic context for MLA",
        categories=STRATEGIC_CATEGORIES,
        max_results=40,
    )
    if not entries:
        return ""
    chunks = []
    for e in entries:
        cat = e.get("category", "?")
        content = (e.get("content") or e.get("text") or "").strip()
        if content:
            chunks.append(f"[{cat}] {content[:1200]}")
    header = "═══ MLA STRATEGIC CONTEXT (from SuperKnowledge) ═══\n"
    return header + "\n\n".join(chunks)


async def save_strategic_informator(
    topic: str,
    content: str,
    category: str,
    applies_to: Optional[list[str]] = None,
) -> dict:
    """Save a Stage 5 loopback informator — called by Heir when Stefan
    dictates new strategic context.
    """
    if not _configured():
        return {"error": "SuperKnowledge not configured"}
    if category not in STRATEGIC_CATEGORIES:
        return {"error": f"category must be one of {STRATEGIC_CATEGORIES}"}

    payload = {
        "topic": topic,
        "content": content,
        "category": category,
        "applies_to": applies_to or ["all"],
        "source_agent": "stefan",  # Stefan dictated this
        "metadata": {"recorded_by": config.AGENT_NAME},
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{_base()}/api/informator/save",
                json=payload, headers=_auth(),
            )
            if r.status_code != 200:
                return {"error": f"SK save HTTP {r.status_code}: {r.text[:200]}"}
            return r.json() or {"saved": True}
    except Exception as e:
        logger.error(f"save_strategic_informator failed: {e}")
        return {"error": str(e)}


async def register_agent_spec() -> None:
    """Register Heir's capabilities with SK (optional, best-effort)."""
    if not _configured():
        return
    spec = {
        "agent": config.AGENT_NAME,
        "display_name": "Heir — MLA Business Developer",
        "model_primary": config.BD_OPUS_MODEL,
        "model_chat": config.BD_SONNET_MODEL,
        "capabilities": [
            "weekly_strategic_review",
            "finance_pulse",
            "opportunity_scan",
            "recommend_to_stefan",
            "advise_agent_quality_review",
        ],
        "reads_categories": STRATEGIC_CATEGORIES,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{_base()}/api/agents/register",
                json=spec, headers=_auth(),
            )
    except Exception as e:
        logger.debug(f"register_agent_spec non-fatal: {e}")
