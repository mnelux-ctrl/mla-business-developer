"""brain/chat.py — Heir's conversational tool-use loop.

Sonnet 4.6 by default; switchable to Opus for weighty strategic work
(weekly reviews, deep finance pulse narratives).

Conversation history is stored in Redis under "heir:conv:{channel_id}".
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from anthropic import AsyncAnthropic

import config
from brain import dispatch, tools
from brain.heir import build_system_prompt
from state import redis_client
from superknowledge import client as sk

logger = logging.getLogger(__name__)

MAX_ROUNDS = 6
MAX_TOKENS = 4000


_client: Optional[AsyncAnthropic] = None


def _anthropic() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


async def chat_with_heir(
    channel_id: str,
    user_message: str,
    use_opus: bool = False,
    extra_system: str = "",
) -> str:
    """Run one Stefan turn. Returns Heir's final reply string."""
    history = await redis_client.get_conversation(channel_id)
    history.append({"role": "user", "content": user_message})

    sk_ctx = await sk.load_strategic_context()
    recent_recs = await redis_client.get_recent_recommendations(n=10)
    system_prompt = build_system_prompt(
        sk_context=sk_ctx,
        recent_recommendations=recent_recs,
    )
    if extra_system:
        system_prompt += "\n\n" + extra_system

    model = config.BD_OPUS_MODEL if use_opus else config.BD_SONNET_MODEL
    messages = _to_anthropic_messages(history)

    reply_text, updated_msgs = await _tool_loop(
        model=model,
        system=system_prompt,
        messages=messages,
    )

    # Convert back to lightweight history entries for Redis persistence
    new_history = list(history)
    # Drop the last user turn we already appended; rewrite from updated_msgs
    # Anthropic message format -> {role, content} with content possibly list
    new_history = _from_anthropic_messages(updated_msgs)
    # Final assistant text is reply_text — ensure it's the last entry
    if new_history and new_history[-1]["role"] != "assistant":
        new_history.append({"role": "assistant", "content": reply_text})

    await redis_client.save_conversation(channel_id, new_history)
    return reply_text


async def _tool_loop(
    model: str,
    system: str,
    messages: list[dict],
) -> tuple[str, list[dict]]:
    """Execute up to MAX_ROUNDS tool-use rounds. Returns (final_text, messages)."""
    client = _anthropic()
    msgs = list(messages)

    for round_idx in range(MAX_ROUNDS):
        try:
            resp = await client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                tools=tools.TOOLS,
                messages=msgs,
            )
        except Exception as e:
            logger.exception("Anthropic call failed")
            return (f"(Heir error: {type(e).__name__}: {e})", msgs)

        # Append assistant turn
        msgs.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            text = _extract_text(resp.content)
            return (text or "(no text returned)", msgs)

        # Collect tool_use blocks, dispatch, and append tool_result
        tool_results: list[dict] = []
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                result = await dispatch.dispatch(block.name, block.input or {})
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": _stringify(result),
                })

        if not tool_results:
            # Anthropic said tool_use but we found none — bail safely
            text = _extract_text(resp.content) or "(tool-use stop without blocks)"
            return (text, msgs)

        msgs.append({"role": "user", "content": tool_results})

    return ("(Heir reached max tool-use rounds — cutting off.)", msgs)


# ── Helpers ───────────────────────────────────────────────────────────

def _extract_text(content_blocks: list) -> str:
    parts: list[str] = []
    for b in content_blocks:
        if getattr(b, "type", None) == "text":
            parts.append(b.text)
    return "\n".join(parts).strip()


def _stringify(obj: Any) -> str:
    import json
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)[:8000]
    except Exception:
        return str(obj)[:8000]


def _to_anthropic_messages(history: list[dict]) -> list[dict]:
    """Redis history can contain strings or Anthropic content blocks.

    We normalise to [{role, content}] where content is a string OR a list
    of content blocks (tool_use/tool_result round-trip).
    """
    out: list[dict] = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if not role or content is None:
            continue
        out.append({"role": role, "content": content})
    return out


def _from_anthropic_messages(msgs: list[dict]) -> list[dict]:
    """Serialise content blocks to JSON-safe dicts for Redis storage."""
    import json

    out: list[dict] = []
    for m in msgs:
        role = m["role"]
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        # List of content blocks (from assistant tool_use or user tool_result)
        serialised: list = []
        for b in content:
            if isinstance(b, dict):
                serialised.append(b)
                continue
            # Anthropic SDK object
            block_type = getattr(b, "type", None)
            if block_type == "text":
                serialised.append({"type": "text", "text": b.text})
            elif block_type == "tool_use":
                serialised.append({
                    "type": "tool_use",
                    "id": b.id,
                    "name": b.name,
                    "input": b.input,
                })
            elif block_type == "tool_result":
                serialised.append({
                    "type": "tool_result",
                    "tool_use_id": getattr(b, "tool_use_id", ""),
                    "content": _stringify(getattr(b, "content", "")),
                })
            else:
                serialised.append({"type": block_type or "unknown", "raw": str(b)})
        try:
            json.dumps(serialised, default=str)
            out.append({"role": role, "content": serialised})
        except Exception:
            out.append({"role": role, "content": _stringify(serialised)})
    return out
