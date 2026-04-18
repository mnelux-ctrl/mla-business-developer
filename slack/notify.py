"""slack/notify.py — Heir outbound notification helpers.

Routing (sensible-defaults Rule 1):
  1. PREFERRED: POST to Team Manager /api/tm/receive-agent-report
     (aggregation layer — TM formats + DMs Stefan via TM bot).
  2. FALLBACK: direct DM via Heir's own Slack bot (legacy path,
     used only when TEAM_MANAGER_API_KEY is unset).

Used by:
  - dispatch.recommend_to_stefan → DM body + logging
  - scheduler.jobs.*             → post weekly / finance / scan outputs
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from slack_sdk.web.async_client import AsyncWebClient

import config

logger = logging.getLogger(__name__)

_client: Optional[AsyncWebClient] = None


def _client_or_none() -> Optional[AsyncWebClient]:
    global _client
    if _client is not None:
        return _client
    token = config.SLACK_HEIR_BOT_TOKEN
    if not token:
        return None
    _client = AsyncWebClient(token=token)
    return _client


async def _relay_via_team_manager(
    title: str, body: str, kind: str = "self_report", severity: str = "info"
) -> bool:
    """POST to TM /api/tm/receive-agent-report. Returns True on success."""
    base = (getattr(config, "TEAM_MANAGER_URL", "") or "").rstrip("/")
    key = getattr(config, "TEAM_MANAGER_API_KEY", "") or ""
    if not base or not key:
        return False
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{base}/api/tm/receive-agent-report",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "agent": "heir",
                    "kind": kind,
                    "title": title,
                    "body": body,
                    "severity": severity,
                    "audience": "stefan",
                },
            )
            j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            if not j.get("ok"):
                logger.warning(f"TM relay failed: status={r.status_code} body={j or r.text[:200]}")
                return False
            return True
    except Exception as e:
        logger.warning(f"TM relay exception: {e}")
        return False


def _split_title_body(text: str) -> tuple[str, str]:
    """Split a long DM text into (title, body) for TM relay.

    Heuristic: first non-blank line (stripped of leading emoji/markdown)
    up to 120 chars is the title; remainder is body. If text is a single
    line, use it as title and leave body empty.
    """
    text = (text or "").strip()
    if not text:
        return "Heir report", ""
    lines = text.split("\n")
    first = lines[0].strip()
    title = first[:120] if first else "Heir report"
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    return title, body


async def send_dm_to_stefan(text: str, kind: str = "self_report") -> bool:
    """Notify Stefan. Tries TM relay first; falls back to direct Heir DM.

    Returns True on success (either path).
    """
    # Preferred: TM aggregation relay
    title, body = _split_title_body(text)
    if await _relay_via_team_manager(title=title, body=body or title, kind=kind):
        return True

    # Fallback: direct Heir bot DM
    client = _client_or_none()
    if client is None:
        logger.info("Slack notify skipped — TM relay + Heir bot token both unavailable")
        return False
    if not config.SLACK_STEFAN_USER_ID:
        logger.warning("Slack notify skipped — SLACK_STEFAN_USER_ID missing")
        return False

    try:
        im = await client.conversations_open(users=config.SLACK_STEFAN_USER_ID)
        channel = (im.get("channel") or {}).get("id")
        if not channel:
            logger.warning("Could not open IM with Stefan")
            return False
        # Chunk long messages
        for chunk in _chunk(text, 3800):
            await client.chat_postMessage(channel=channel, text=chunk)
        return True
    except Exception as e:
        logger.warning(f"send_dm_to_stefan failed: {e}")
        return False


def _chunk(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    out: list[str] = []
    buf: list[str] = []
    cur = 0
    for line in text.split("\n"):
        add = len(line) + 1
        if cur + add > size and buf:
            out.append("\n".join(buf))
            buf, cur = [line], len(line)
        else:
            buf.append(line)
            cur += add
    if buf:
        out.append("\n".join(buf))
    return out
