"""state/redis_client.py — Redis client for Heir.

Prefix: "heir:"
Uses:
  - Slack conversation history (TTL 4h)
  - Event dedup (TTL 1h)
  - Recent recommendations log (list, capped at 50)
  - Strategic notes cache (TTL 7 days)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import redis.asyncio as aioredis

import config

logger = logging.getLogger(__name__)

KEY_PREFIX = "heir:"
CONVERSATION_TTL = 4 * 3600       # 4 hours
RECOMMENDATIONS_CAP = 50          # keep last 50 recommendations

_redis: Optional[aioredis.Redis] = None


def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            config.REDIS_URL, decode_responses=True, encoding="utf-8"
        )
    return _redis


async def ping() -> bool:
    try:
        r = _get_redis()
        return bool(await r.ping())
    except Exception as e:
        logger.error(f"Redis ping failed: {e}")
        return False


# ── Conversation history (Slack DM) ──────────────────────────────────────

async def get_conversation(channel_id: str) -> list[dict]:
    r = _get_redis()
    key = f"{KEY_PREFIX}conv:{channel_id}"
    raw = await r.get(key)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


async def save_conversation(channel_id: str, history: list[dict]) -> None:
    r = _get_redis()
    # Trim to last 40 turns
    if len(history) > 40:
        history = history[-40:]
    key = f"{KEY_PREFIX}conv:{channel_id}"
    await r.set(key, json.dumps(history, ensure_ascii=False), ex=CONVERSATION_TTL)


async def clear_conversation(channel_id: str) -> None:
    r = _get_redis()
    await r.delete(f"{KEY_PREFIX}conv:{channel_id}")


# ── Recommendations log ──────────────────────────────────────────────────

async def log_recommendation(item: dict) -> None:
    r = _get_redis()
    key = f"{KEY_PREFIX}recommendations"
    await r.lpush(key, json.dumps(item, ensure_ascii=False, default=str))
    await r.ltrim(key, 0, RECOMMENDATIONS_CAP - 1)


async def get_recent_recommendations(n: int = 20) -> list[dict]:
    r = _get_redis()
    key = f"{KEY_PREFIX}recommendations"
    raws = await r.lrange(key, 0, n - 1)
    out: list[dict] = []
    for raw in raws:
        try:
            out.append(json.loads(raw))
        except Exception:
            pass
    return out


# ── Strategic cache (for idempotent weekly reviews) ──────────────────────

async def set_cache(key: str, value: Any, ttl: int = 7 * 86400) -> None:
    r = _get_redis()
    await r.set(f"{KEY_PREFIX}cache:{key}", json.dumps(value, ensure_ascii=False, default=str), ex=ttl)


async def get_cache(key: str) -> Optional[Any]:
    r = _get_redis()
    raw = await r.get(f"{KEY_PREFIX}cache:{key}")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
