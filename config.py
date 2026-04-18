"""config.py — Heir (MLA Business Developer)."""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)


# ── AI ─────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = _require("ANTHROPIC_API_KEY")
OPENAI_API_KEY: str = _optional("OPENAI_API_KEY", "")  # Whisper only

# Heir uses Opus for strategic reasoning, Sonnet for daily ops / chat.
BD_OPUS_MODEL: str = _optional("BD_OPUS_MODEL", "claude-opus-4-5-20251001")
BD_SONNET_MODEL: str = _optional("BD_SONNET_MODEL", "claude-sonnet-4-6-20251001")

# ── API Security ───────────────────────────────────────────────────────────
BD_API_KEY: str = _require("BD_API_KEY")

# ── Infra ──────────────────────────────────────────────────────────────────
REDIS_URL: str = _require("REDIS_URL")
PORT: int = int(_optional("PORT", "8009"))

# ── Airtable (for finance pulse + opportunity tracking) ────────────────────
AIRTABLE_PAT: str = _optional("AIRTABLE_PAT", "")
AIRTABLE_BASE_ID: str = _optional("AIRTABLE_BASE_ID", "")

# Finance pulse implementation: "local" = read Airtable directly in this
# service; "accountant_service" = future dedicated AI Accountant endpoint.
FINANCE_PULSE_IMPL: str = _optional("FINANCE_PULSE_IMPL", "local")
ACCOUNTANT_URL: str = _optional("ACCOUNTANT_URL", "")
ACCOUNTANT_API_KEY: str = _optional("ACCOUNTANT_API_KEY", "")

# ── Tavily (opportunity scanning) ──────────────────────────────────────────
TAVILY_API_KEY: str = _optional("TAVILY_API_KEY", "")

# ── SuperKnowledge ─────────────────────────────────────────────────────────
SUPERKNOWLEDGE_URL: str = _optional("SUPERKNOWLEDGE_URL", "")
SUPERKNOWLEDGE_API_KEY: str = _optional("SUPERKNOWLEDGE_API_KEY", "")

# Heir acts on behalf of ecosystem — recall with his name
AGENT_NAME: str = "heir"

# ── Sibling services (for advise-agent calls) ──────────────────────────────
COO_URL: str = _optional("COO_URL", "")
COO_API_KEY: str = _optional("COO_API_KEY", "")
GRANT_RESEARCH_URL: str = _optional("GRANT_RESEARCH_URL", "")
GRANT_RESEARCH_API_KEY: str = _optional("GRANT_RESEARCH_API_KEY", "")
GRANT_WRITER_URL: str = _optional("GRANT_WRITER_URL", "")
GRANT_WRITER_API_KEY: str = _optional("GRANT_WRITER_API_KEY", "")

# ── Slack ──────────────────────────────────────────────────────────────────
SLACK_HEIR_BOT_TOKEN: str = _optional("SLACK_HEIR_BOT_TOKEN", "")
SLACK_HEIR_SIGNING_SECRET: str = _optional("SLACK_HEIR_SIGNING_SECRET", "")
SLACK_STEFAN_USER_ID: str = _optional("SLACK_STEFAN_USER_ID", "")

# Scheduled self-reports (Europe/Podgorica)
# Weekly strategic review: Monday 09:00
# Friday finance pulse: Friday 09:15
# Daily opportunity scan: Daily 10:00
HEIR_WEEKLY_ENABLED: str = _optional("HEIR_WEEKLY_ENABLED", "on")
HEIR_FINANCE_ENABLED: str = _optional("HEIR_FINANCE_ENABLED", "on")
HEIR_SCAN_ENABLED: str = _optional("HEIR_SCAN_ENABLED", "on")

TIMEZONE: str = _optional("TIMEZONE", "Europe/Podgorica")

# ── Organization ───────────────────────────────────────────────────────────
ORGANIZATION: str = "Montenegro Luxury Association"


def validate_all() -> None:
    """Runtime validation; called in lifespan."""
    assert ANTHROPIC_API_KEY
    assert BD_API_KEY
    assert REDIS_URL
    # Airtable is required for finance pulse local impl; warn if missing.
    if FINANCE_PULSE_IMPL == "local" and not (AIRTABLE_PAT and AIRTABLE_BASE_ID):
        import logging
        logging.getLogger(__name__).warning(
            "FINANCE_PULSE_IMPL=local but AIRTABLE_PAT/BASE_ID missing — "
            "finance pulse will return empty."
        )
