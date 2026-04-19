"""brain/tools.py — Anthropic tool definitions for Heir.

Every tool here maps 1:1 to a dispatch branch in brain/dispatch.py.
Tools are intentionally strategy-first: recommend, review, scan, brief —
NOT operational execution.
"""

from __future__ import annotations

# Imported here so the shared doc_reader tool schema is always in sync with
# the service contract — never forked.
from shared.doc_reader import CLAUDE_TOOL_SCHEMA as _READ_DOCUMENT_TOOL

TOOLS = [
    # ── DOCUMENT READING (shared MLA service) ─────────────────────────
    _READ_DOCUMENT_TOOL,
    # ── Strategic reviews ──────────────────────────────────────────────
    {
        "name": "run_weekly_strategic_review",
        "description": (
            "Run the full Monday 09:00 strategic review: revenue trajectory, "
            "grant pipeline progress, BI-as-a-service leads, and 3 concrete "
            "recommended actions for Stefan this week. Uses Opus. Output is "
            "a Markdown brief."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "description": (
                        "Optional focus area: 'grants', 'bi_saas', 'partnerships'. "
                        "Omit for full review."
                    ),
                }
            },
        },
    },
    {
        "name": "run_opportunity_scan",
        "description": (
            "Scan external signals (news, LinkedIn trends, Montenegro business "
            "press, tourism sector reports) for new BI-as-a-service leads or "
            "partnership angles. Returns a ranked list of opportunities."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search angle, e.g. 'luxury hotels digitization Montenegro'.",
                },
                "max_results": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    },
    # ── Finance ────────────────────────────────────────────────────────
    {
        "name": "get_finance_pulse",
        "description": (
            "Pull the lite AI-Accountant snapshot: cash balance, 30/60/90 "
            "receivables, monthly burn rate, grant pipeline value, "
            "BI-lead pipeline value, runway months. Reads Airtable INVOICES "
            "+ EXPENSES + GRANT_APPLICATION. Use this before any financial "
            "recommendation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "months_back": {
                    "type": "integer",
                    "default": 3,
                    "description": "How many months of burn to average over.",
                }
            },
        },
    },
    # ── Recommendations & advising ─────────────────────────────────────
    {
        "name": "recommend_to_stefan",
        "description": (
            "Write a strategic proposal directly to Stefan's Slack DM and "
            "log it in Redis recommendations history. Use this for any "
            "revenue-generating or cost-cutting action where you want a "
            "yes/no decision from Stefan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "body": {
                    "type": "string",
                    "description": "The full proposal body in Markdown.",
                },
                "impact_eur": {
                    "type": "number",
                    "description": "Estimated €-impact (upside or cost save).",
                },
                "deadline": {
                    "type": "string",
                    "description": "When Stefan needs to decide by (ISO date or natural).",
                },
            },
            "required": ["topic", "body"],
        },
    },
    {
        "name": "advise_agent",
        "description": (
            "Peer quality review for another MLA agent (viktorija, "
            "administrator, research, coo, marketing_executive). Be "
            "brutally honest. If the output would embarrass MLA in front "
            "of a Fortune 500 client, say so."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "enum": [
                        "viktorija",
                        "administrator",
                        "research",
                        "coo",
                        "marketing_executive",
                        "other",
                    ],
                },
                "artifact_type": {
                    "type": "string",
                    "description": "e.g. 'grant_draft', 'partner_email', 'blog_post'.",
                },
                "content": {"type": "string"},
            },
            "required": ["agent", "content"],
        },
    },
    # ── SK loopback (Stage 5) ──────────────────────────────────────────
    {
        "name": "save_strategic_informator",
        "description": (
            "Save a strategic conversation fragment to SuperKnowledge as "
            "a structured informator. Use proactively when Stefan shares "
            "vision, business model, financial direction, brand stance, "
            "or organizational structure."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "content": {"type": "string"},
                "category": {
                    "type": "string",
                    "enum": [
                        "org_vision",
                        "financial_context",
                        "business_model",
                        "brand_identity",
                        "org_structure",
                        "stefan_strategic_context",
                    ],
                },
                "applies_to": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Which agents should see this. Default ['all']. "
                        "Use ['coo','heir'] for Stefan-private strategic context."
                    ),
                },
            },
            "required": ["topic", "content", "category"],
        },
    },
    {
        "name": "sk_recall",
        "description": (
            "Search SuperKnowledge for prior informators, decisions, "
            "facts, or partner notes. Scoped to Heir's agent view."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
    },
    # ── Recommendation log ─────────────────────────────────────────────
    {
        "name": "get_recent_recommendations",
        "description": "Return the last N recommendations Heir has logged.",
        "input_schema": {
            "type": "object",
            "properties": {"n": {"type": "integer", "default": 10}},
        },
    },
    # ── Sibling-service visibility ─────────────────────────────────────
    {
        "name": "list_grant_applications",
        "description": (
            "Ping mla-grant-writer for the current state of all grant "
            "applications (which draft stage, which field, which score)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def tool_names() -> list[str]:
    return [t["name"] for t in TOOLS]
