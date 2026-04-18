"""slack/handlers.py — Heir's Slack message + voice handlers.

message.im   → text DM from Stefan → brain.chat.chat_with_heir()
file_shared  → voice note (audio/*) → Whisper → brain.chat.chat_with_heir()
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from slack_bolt.async_app import AsyncApp

import config
from brain.chat import chat_with_heir
from state import redis_client
from voice.transcriber import transcribe_slack_audio

logger = logging.getLogger(__name__)

DEDUP_TTL = 3600  # 1h


def register_handlers(app: AsyncApp) -> None:
    @app.event("message")
    async def on_message(body: dict, event: dict, client, ack):
        await ack()
        if event.get("bot_id") or event.get("subtype") in {
            "bot_message",
            "message_changed",
            "message_deleted",
        }:
            return

        # file-only messages are handled by file_shared below
        if event.get("files") and not (event.get("text") or "").strip():
            return

        channel = event.get("channel")
        ts = event.get("ts")
        text = (event.get("text") or "").strip()
        if not channel or not ts or not text:
            return

        if not await _claim_event(ts):
            return

        try:
            reply = await chat_with_heir(channel_id=channel, user_message=text)
        except Exception as e:
            logger.exception("chat_with_heir failed")
            reply = f"(Heir error: {type(e).__name__}: {e})"

        await _post(client, channel, reply)

        # SuperKnowledge auto-learn from Stefan's DM (Stefan directive 2026-04-19:
        # "važno je samo šta god kome da kažem, da se iz toga builduje njihovo znanje")
        try:
            if len(text) >= 30 and getattr(config, "SUPERKNOWLEDGE_URL", "") \
                    and getattr(config, "SUPERKNOWLEDGE_API_KEY", ""):
                import httpx as _hx
                lower = text.lower()
                is_rule = any(k in lower for k in (
                    "uvijek", "nikad", "nikada", "pravilo", "always", "never", "rule",
                ))
                category = "preference" if is_rule else "fact"
                audience = ["all"] if is_rule else ["heir"]
                async with _hx.AsyncClient(timeout=15) as _c:
                    await _c.post(
                        f"{config.SUPERKNOWLEDGE_URL.rstrip('/')}/api/learn",
                        headers={"Authorization": f"Bearer {config.SUPERKNOWLEDGE_API_KEY}"},
                        json={
                            "content": text[:500],
                            "category": category,
                            "source_agent": "stefan",
                            "metadata": {
                                "channel": "slack_heir",
                                "response_preview": (reply or "")[:200],
                                "applies_to": audience,
                            },
                        },
                    )
        except Exception as e:
            logger.warning(f"Heir auto-learn failed (non-fatal): {e}")

    @app.event("file_shared")
    async def on_file_shared(body: dict, event: dict, client, ack):
        await ack()
        file_id = event.get("file_id") or (event.get("file") or {}).get("id")
        channel = event.get("channel_id") or event.get("channel")
        if not file_id or not channel:
            return

        if not await _claim_event(f"file:{file_id}"):
            return

        try:
            info = await client.files_info(file=file_id)
            f = (info or {}).get("file", {}) or {}
            mime = (f.get("mimetype") or "").lower()
            if not (mime.startswith("audio/") or mime.startswith("video/")):
                # Heir doesn't process non-audio uploads in this scaffold
                return

            url_private = f.get("url_private_download") or f.get("url_private")
            url_public = f.get("permalink_public") or f.get("permalink")
            text, err = await transcribe_slack_audio(
                audio_url=url_public or url_private or "",
                slack_token=config.SLACK_HEIR_BOT_TOKEN,
                download_url=url_private,
            )
            if err or not text:
                await _post(client, channel, f"🎙️ Couldn't transcribe: {err or 'empty'}")
                return

            await _post(client, channel, f"🎙️ _Heard:_ {text}")
            reply = await chat_with_heir(channel_id=channel, user_message=text)
            await _post(client, channel, reply)
        except Exception as e:
            logger.exception("file_shared handler failed")
            await _post(client, channel, f"(Heir voice error: {type(e).__name__}: {e})")


# ── Utilities ─────────────────────────────────────────────────────────

async def _claim_event(event_id: str) -> bool:
    """Best-effort dedup via Redis SETNX. Returns True if first seen."""
    try:
        r = redis_client._get_redis()
        key = f"{redis_client.KEY_PREFIX}dedup:{event_id}"
        ok = await r.set(key, "1", ex=DEDUP_TTL, nx=True)
        return bool(ok)
    except Exception as e:
        logger.warning(f"dedup claim failed (allowing event): {e}")
        return True


async def _post(client: Any, channel: str, text: str) -> None:
    # Slack hard-limits messages to 40k chars; chunk conservatively
    chunks = _chunk(text, 3800)
    for idx, c in enumerate(chunks):
        try:
            await client.chat_postMessage(channel=channel, text=c)
            if idx < len(chunks) - 1:
                await asyncio.sleep(0.3)
        except Exception as e:
            logger.warning(f"chat_postMessage failed: {e}")
            break


def _chunk(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    out: list[str] = []
    lines = text.split("\n")
    buf: list[str] = []
    cur = 0
    for line in lines:
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
