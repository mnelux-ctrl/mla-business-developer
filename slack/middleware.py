"""slack/middleware.py — Bolt middleware to gate Heir to Stefan only."""

from __future__ import annotations

import logging

import config

logger = logging.getLogger(__name__)


async def stefan_only(body, next, logger=logger):  # noqa: A002
    """Block every event whose user isn't Stefan.

    Works as Bolt global middleware. The `body` is the raw event payload
    from Slack. For DM message events, `body['event']['user']` is the
    sender. We allow the request to continue only if the sender matches
    SLACK_STEFAN_USER_ID (exact match).
    """
    stefan_id = config.SLACK_STEFAN_USER_ID
    if not stefan_id:
        # Defensive: no lockdown configured → refuse to route anything
        logger.warning("SLACK_STEFAN_USER_ID missing — blocking all events")
        return

    event = (body or {}).get("event") or {}
    user_id = (
        event.get("user")
        or (event.get("user_id"))
        or (body.get("user") or {}).get("id")
        or (body or {}).get("user_id")
    )

    if user_id and user_id != stefan_id:
        logger.info("Ignoring event from non-Stefan user %s", user_id)
        return  # short-circuit — do NOT call next

    await next()
