"""slack/bot.py — Slack Bolt app singleton for Heir.

Enforces stefan_only middleware, registers message / voice handlers and
interaction callbacks.
"""

from __future__ import annotations

import logging
from typing import Optional

from slack_bolt.async_app import AsyncApp

import config
from slack.handlers import register_handlers
from slack.callbacks import register_callbacks
from slack.middleware import stefan_only

logger = logging.getLogger(__name__)

_app: Optional[AsyncApp] = None


def get_slack_app() -> Optional[AsyncApp]:
    """Return the Bolt app, or None if Slack isn't configured (graceful)."""
    global _app
    if _app is not None:
        return _app

    token = config.SLACK_HEIR_BOT_TOKEN
    secret = config.SLACK_HEIR_SIGNING_SECRET
    if not token or not secret:
        logger.warning("Heir Slack disabled — SLACK_HEIR_BOT_TOKEN or SIGNING_SECRET missing")
        return None

    app = AsyncApp(
        token=token,
        signing_secret=secret,
        # Reduce default request-verification noise in Railway logs
        raise_error_for_unhandled_request=False,
    )

    # Stefan-only guard
    app.use(stefan_only)

    register_handlers(app)
    register_callbacks(app)

    _app = app
    logger.info("Heir Slack Bolt app initialised")
    return _app
