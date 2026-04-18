"""slack/callbacks.py — Slack interaction callbacks (block actions).

Currently registers placeholder ack-only handlers so Slack doesn't show
"this app is not responding" when Heir posts buttons in future work.
"""

from __future__ import annotations

import logging

from slack_bolt.async_app import AsyncApp

logger = logging.getLogger(__name__)


def register_callbacks(app: AsyncApp) -> None:
    @app.action("heir_ack")
    async def _ack_placeholder(ack, body):
        await ack()
        logger.debug("heir_ack action received: %s", body.get("user", {}).get("id"))

    @app.action("heir_approve")
    async def _approve_placeholder(ack, body, client):
        await ack()
        # Future: mark recommendation as approved in Redis
        logger.debug("heir_approve action received")

    @app.action("heir_reject")
    async def _reject_placeholder(ack, body, client):
        await ack()
        logger.debug("heir_reject action received")
