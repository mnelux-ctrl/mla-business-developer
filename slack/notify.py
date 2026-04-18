"""slack/notify.py — Direct Slack DM helpers (no Bolt event round-trip).

Used by:
  - dispatch.recommend_to_stefan → DM body + logging
  - scheduler.jobs.*             → post weekly / finance / scan outputs
"""

from __future__ import annotations

import logging
from typing import Optional

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


async def send_dm_to_stefan(text: str) -> bool:
    """DM Stefan. Returns True on success, False if disabled or errored."""
    client = _client_or_none()
    if client is None:
        logger.info("Slack notify skipped — Heir bot token missing")
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
