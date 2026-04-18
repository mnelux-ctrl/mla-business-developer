"""finance/pulse.py — Heir's lite AI-Accountant.

Pull:
  - Cash balance (INVOICES paid − EXPENSES paid, last N months)
  - 30/60/90 receivables (INVOICES sent, not paid, bucketed by aging)
  - Monthly burn (EXPENSES avg over months_back)
  - Grant pipeline value (GRANT_APPLICATION open/draft/submitted, eur_amount)
  - BI-lead pipeline value (SK recall of bi_lead entries with eur_potential)
  - Runway months = cash / monthly_burn

Swappable: if config.FINANCE_PULSE_IMPL == "accountant_service", delegate
to the dedicated AI Accountant service at config.ACCOUNTANT_URL.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

import config

logger = logging.getLogger(__name__)


# ── Public entry point ───────────────────────────────────────────────

async def get_pulse(months_back: int = 3) -> dict:
    """Return the finance pulse snapshot."""
    impl = (config.FINANCE_PULSE_IMPL or "local").lower()
    if impl == "accountant_service":
        return await _remote_pulse(months_back)
    return await _local_pulse(months_back)


# ── Local implementation (Airtable direct) ───────────────────────────

async def _local_pulse(months_back: int) -> dict:
    if not (config.AIRTABLE_PAT and config.AIRTABLE_BASE_ID):
        return {
            "error": "Airtable not configured",
            "impl": "local",
        }

    try:
        from pyairtable import Api
    except ImportError:
        return {"error": "pyairtable not installed", "impl": "local"}

    api = Api(config.AIRTABLE_PAT)
    base = api.base(config.AIRTABLE_BASE_ID)

    invoices = await _safe_all(base, "INVOICES")
    expenses = await _safe_all(base, "EXPENSES")
    grants = await _safe_all(base, "GRANT_APPLICATION")

    cash_balance = _cash_from(invoices, expenses, months_back)
    receivables = _receivables_buckets(invoices)
    monthly_burn = _monthly_burn(expenses, months_back)
    grant_pipeline = _grant_pipeline(grants)
    bi_leads = await _bi_leads_from_sk()

    runway = None
    if monthly_burn and monthly_burn > 0:
        runway = round(cash_balance / monthly_burn, 1)

    return {
        "impl": "local",
        "as_of": datetime.now(timezone.utc).isoformat(),
        "months_back_window": months_back,
        "cash_balance_eur": round(cash_balance, 2),
        "receivables_eur": receivables,
        "monthly_burn_eur": round(monthly_burn, 2) if monthly_burn else 0.0,
        "runway_months": runway,
        "grant_pipeline": grant_pipeline,
        "bi_leads_pipeline": bi_leads,
        "alerts": _alerts(cash_balance, monthly_burn, runway),
    }


async def _remote_pulse(months_back: int) -> dict:
    if not config.ACCOUNTANT_URL:
        return {
            "error": "ACCOUNTANT_URL missing — cannot delegate",
            "impl": "accountant_service",
        }
    headers: dict[str, str] = {}
    if getattr(config, "ACCOUNTANT_API_KEY", ""):
        headers["Authorization"] = f"Bearer {config.ACCOUNTANT_API_KEY}"
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"{config.ACCOUNTANT_URL.rstrip('/')}/api/pulse",
                json={"months_back": months_back},
                headers=headers,
            )
            if r.status_code != 200:
                return {
                    "error": f"accountant HTTP {r.status_code}",
                    "impl": "accountant_service",
                }
            data = r.json() or {}
            data.setdefault("impl", "accountant_service")
            return data
    except Exception as e:
        return {"error": f"accountant unreachable: {e}", "impl": "accountant_service"}


# ── Airtable helpers ──────────────────────────────────────────────────

async def _safe_all(base: Any, table_name: str) -> list[dict]:
    try:
        tbl = base.table(table_name)
        # pyairtable sync .all() — run in thread-safe manner
        import asyncio
        records = await asyncio.to_thread(tbl.all)
        return records or []
    except Exception as e:
        logger.warning(f"Airtable table '{table_name}' not readable: {e}")
        return []


def _f(fields: dict, *keys: str, default: Optional[float] = 0.0) -> Optional[float]:
    for k in keys:
        v = fields.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except Exception:
            continue
    return default


def _d(fields: dict, *keys: str) -> Optional[datetime]:
    for k in keys:
        v = fields.get(k)
        if not v:
            continue
        try:
            if isinstance(v, str):
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:
            continue
    return None


def _cash_from(invoices: list, expenses: list, months_back: int) -> float:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months_back)
    paid_in = 0.0
    paid_out = 0.0

    for inv in invoices:
        f = inv.get("fields", {})
        if (f.get("status") or "").lower() not in ("paid", "plaćen", "placen"):
            continue
        paid_at = _d(f, "paid_at", "date_paid", "paid_date", "payment_date")
        if paid_at and paid_at < cutoff:
            continue
        paid_in += _f(f, "amount_eur", "amount", "total_eur", "total") or 0.0

    for exp in expenses:
        f = exp.get("fields", {})
        if (f.get("status") or "").lower() not in ("paid", "plaćen", "placen"):
            continue
        paid_at = _d(f, "paid_at", "date_paid", "payment_date", "date")
        if paid_at and paid_at < cutoff:
            continue
        paid_out += _f(f, "amount_eur", "amount", "total_eur", "total") or 0.0

    return paid_in - paid_out


def _receivables_buckets(invoices: list) -> dict:
    now = datetime.now(timezone.utc)
    buckets = {"0_30": 0.0, "31_60": 0.0, "61_90": 0.0, "90_plus": 0.0, "total": 0.0}
    for inv in invoices:
        f = inv.get("fields", {})
        status = (f.get("status") or "").lower()
        if status in ("paid", "plaćen", "placen", "void", "cancelled"):
            continue
        amt = _f(f, "amount_eur", "amount", "total_eur", "total") or 0.0
        if amt <= 0:
            continue
        due = _d(f, "due_date", "due", "date_due")
        if not due:
            # fallback to issue date if no due
            due = _d(f, "issued_at", "date", "issue_date")
        if not due:
            continue
        age_days = (now - due).days
        if age_days < 0:
            continue
        if age_days <= 30:
            buckets["0_30"] += amt
        elif age_days <= 60:
            buckets["31_60"] += amt
        elif age_days <= 90:
            buckets["61_90"] += amt
        else:
            buckets["90_plus"] += amt
        buckets["total"] += amt
    return {k: round(v, 2) for k, v in buckets.items()}


def _monthly_burn(expenses: list, months_back: int) -> float:
    if months_back <= 0 or not expenses:
        return 0.0
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months_back)
    total_out = 0.0
    for exp in expenses:
        f = exp.get("fields", {})
        d = _d(f, "paid_at", "date_paid", "payment_date", "date")
        if d and d < cutoff:
            continue
        total_out += _f(f, "amount_eur", "amount", "total_eur", "total") or 0.0
    return total_out / max(1, months_back)


def _grant_pipeline(grants: list) -> dict:
    pipeline = {"open": 0.0, "submitted": 0.0, "won": 0.0, "lost": 0.0, "count": 0}
    for g in grants:
        f = g.get("fields", {})
        status = (f.get("status") or "open").lower()
        amt = _f(f, "eur_amount", "amount_eur", "requested_eur", "amount") or 0.0
        pipeline["count"] += 1
        if "won" in status or "approved" in status:
            pipeline["won"] += amt
        elif "lost" in status or "rejected" in status:
            pipeline["lost"] += amt
        elif "submit" in status or "sent" in status:
            pipeline["submitted"] += amt
        else:
            pipeline["open"] += amt
    return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in pipeline.items()}


async def _bi_leads_from_sk() -> dict:
    from superknowledge import client as sk
    entries = await sk.recall(
        query="BI-as-a-service lead",
        categories=["project", "business_model"],
        max_results=20,
    )
    if not entries:
        return {"count": 0, "eur_potential": 0.0}
    total = 0.0
    for e in entries:
        meta = e.get("metadata") or {}
        val = meta.get("eur_potential") or meta.get("deal_size_eur") or 0
        try:
            total += float(val)
        except Exception:
            continue
    return {"count": len(entries), "eur_potential": round(total, 2)}


def _alerts(cash: float, burn: float, runway: Optional[float]) -> list[str]:
    out: list[str] = []
    if runway is not None and runway < 3:
        out.append(f"RUNWAY CRITICAL: {runway} months — under 3-month threshold")
    elif runway is not None and runway < 6:
        out.append(f"Runway warning: {runway} months")
    if cash < 0:
        out.append(f"Cash balance negative: €{cash:,.0f}")
    if burn == 0:
        out.append("Burn rate is 0 — likely missing EXPENSES data")
    return out
