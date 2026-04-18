"""
finance/airtable_query.py — Heir's ad-hoc Airtable query tool (T3).

Heir's finance_pulse gives fixed aggregates. This tool answers ad-hoc
questions Stefan asks in chat:
    "Koje prijave su u planiranju, deadline < 30 dana?"
    "Poslednja tri poslana invoice-a, koliko para čekamo?"
    "Lista partnera u CRM-u koji imaju tag 'luxury' i nisu kontaktirani 60+ dana"

Read-only. Accepts Airtable formula + optional sort/fields/max. Returns
records plus a short summary so Heir can cite numbers directly.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import config

logger = logging.getLogger(__name__)

# Known tables in the MLA base — helps Heir pick one when Stefan is vague
KNOWN_TABLES = {
    "grant_application": "GRANT_APPLICATION",
    "grant_call": "GRANT_CALL",
    "grant_partner": "GRANT_PARTNER",
    "grant_document": "GRANT_DOCUMENT",
    "invoice": "INVOICE",
    "expense": "EXPENSE",
    "contact": "CONTACT",
    "business_partners": "Business Partners",
    "project": "PROJECT",
    "meeting": "MEETING",
    "email_log": "EMAIL_LOG",
}

MAX_RECORDS_HARD_CAP = 200


def _resolve_table(table_name: str) -> str:
    """Normalise — accept snake_case or canonical name."""
    if not table_name:
        return ""
    return KNOWN_TABLES.get(table_name.lower().strip(), table_name)


async def query_airtable(
    table_name: str,
    filter_formula: Optional[str] = None,
    fields: Optional[list[str]] = None,
    max_records: int = 50,
    sort: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Run a read-only Airtable query.

    Args:
        table_name: 'GRANT_APPLICATION' or alias like 'grant_application'
        filter_formula: Airtable formula, e.g. "AND({status}='PLANNING', IS_BEFORE({deadline}, DATEADD(TODAY(),30,'days')))"
        fields: list of fields to return (defaults to all if omitted)
        max_records: cap returned rows (default 50, max 200)
        sort: [{'field': 'created_at', 'direction': 'desc'}]

    Returns: {ok, table, count, records, summary}
    """
    pat = getattr(config, "AIRTABLE_PAT", "")
    base_id = getattr(config, "AIRTABLE_BASE_ID", "")
    if not pat or not base_id:
        return {"ok": False, "error": "AIRTABLE_PAT / AIRTABLE_BASE_ID not configured"}

    resolved = _resolve_table(table_name)
    if not resolved:
        return {"ok": False, "error": "table_name required"}

    cap = max(1, min(int(max_records or 50), MAX_RECORDS_HARD_CAP))

    try:
        from pyairtable import Api
        api = Api(pat)
        table = api.table(base_id, resolved)
        kwargs: dict[str, Any] = {"max_records": cap}
        if filter_formula:
            kwargs["formula"] = filter_formula
        if fields:
            kwargs["fields"] = fields
        if sort:
            kwargs["sort"] = [
                f"-{s['field']}" if s.get("direction", "asc").lower() == "desc" else s["field"]
                for s in sort
            ]
        import asyncio
        records = await asyncio.to_thread(table.all, **kwargs)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "table": resolved}

    # Strip Airtable envelope for brevity
    clean_records = [
        {"id": r.get("id"), **(r.get("fields") or {})}
        for r in records
    ]

    # Short summary Heir can cite
    summary = _summarise(clean_records, fields=fields)

    return {
        "ok": True,
        "table": resolved,
        "count": len(clean_records),
        "records": clean_records,
        "summary": summary,
        "query": {
            "formula": filter_formula,
            "fields": fields,
            "sort": sort,
            "max_records": cap,
        },
    }


def _summarise(records: list[dict], fields: Optional[list[str]] = None) -> str:
    """Return a one-sentence summary helpful for Heir to inline in replies."""
    if not records:
        return "Nema rezultata za zadati filter."

    n = len(records)
    # Try to find numeric money-like fields for a quick total
    money_keys = ("amount", "iznos", "budget", "budget_eur", "amount_eur",
                  "total", "requested_amount", "awarded_amount", "invoice_total",
                  "sum")
    total_eur: Optional[float] = None
    money_field: Optional[str] = None
    for k in money_keys:
        values = []
        for rec in records:
            v = rec.get(k)
            if isinstance(v, (int, float)):
                values.append(float(v))
        if values:
            total_eur = sum(values)
            money_field = k
            break

    if total_eur is not None and money_field:
        formatted = f"{int(total_eur):,}".replace(",", ".")
        return f"{n} zapisa; ukupno `{money_field}` ≈ €{formatted}."
    return f"{n} zapisa vraćeno."
