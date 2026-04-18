"""Heir Slack integration — bot wiring + Stefan DM helpers.

Graceful degradation: if SLACK_HEIR_BOT_TOKEN is missing, get_slack_app()
returns None and the FastAPI lifespan skips the mount.
"""
