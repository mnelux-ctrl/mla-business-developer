"""main.py — Heir (MLA Business Developer) FastAPI service.

Lifespan:
  1. Validate env
  2. Ping Redis
  3. Pull strategic context from SK (log counts)
  4. Register agent spec with SK (best-effort)
  5. Start APScheduler (3 jobs)
  6. Mount Slack Bolt app if SLACK_HEIR_BOT_TOKEN present
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

import config
from state import redis_client

# Configure logging before anything else
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("heir.main")


# ── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Heir starting up…")

    try:
        config.validate_all()
    except AssertionError as e:
        logger.error(f"Env validation failed: {e}")

    # Redis
    redis_ok = await redis_client.ping()
    logger.info(f"Redis ping: {'ok' if redis_ok else 'FAILED'}")

    # SK context
    try:
        from superknowledge import client as sk
        ctx = await sk.load_strategic_context()
        count = ctx.count("[") if ctx else 0  # rough
        logger.info(f"SK strategic context loaded: {count} informators")
        # Best-effort: register agent spec
        try:
            await sk.register_agent_spec()
        except Exception as e:
            logger.debug(f"register_agent_spec non-fatal: {e}")
    except Exception as e:
        logger.warning(f"SK startup skipped: {e}")

    # Scheduler
    try:
        from scheduler.setup import start_scheduler
        start_scheduler()
    except Exception as e:
        logger.warning(f"Scheduler failed to start: {e}")

    # Slack Bolt
    _mount_slack(app)

    logger.info("Heir startup complete")
    yield

    logger.info("Heir shutting down…")
    try:
        from scheduler.setup import stop_scheduler
        stop_scheduler()
    except Exception:
        pass


def _mount_slack(app: FastAPI) -> None:
    try:
        from slack.bot import get_slack_app
        slack_app = get_slack_app()
        if slack_app is None:
            logger.info("Heir Slack mount skipped — not configured")
            return

        from slack_bolt.adapter.fastapi.async_handler import AsyncSlackRequestHandler
        handler = AsyncSlackRequestHandler(slack_app)

        @app.post("/slack/heir/events")
        async def _slack_events(req: Request):
            return await handler.handle(req)

        @app.post("/slack/heir/interactions")
        async def _slack_interactions(req: Request):
            return await handler.handle(req)

        logger.info("Heir Slack mounted at /slack/heir/events + /interactions")
    except Exception as e:
        logger.warning(f"Heir Slack mount failed: {e}")


app = FastAPI(title="Heir — MLA Business Developer", lifespan=lifespan)


# ── Auth dependency ──────────────────────────────────────────────────

async def require_bd_key(request: Request) -> None:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = header.split(" ", 1)[1].strip()
    if token != config.BD_API_KEY:
        raise HTTPException(status_code=401, detail="bad token")


# ── Routes ───────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    redis_ok = await redis_client.ping()
    slack_status = _slack_status()
    sk_configured = bool(config.SUPERKNOWLEDGE_URL and config.SUPERKNOWLEDGE_API_KEY)
    return {
        "service": "heir",
        "agent": config.AGENT_NAME,
        "status": "ok",
        "redis": "ok" if redis_ok else "down",
        "slack_bot": slack_status,
        "superknowledge": "configured" if sk_configured else "missing",
        "finance_pulse_impl": config.FINANCE_PULSE_IMPL,
        "timezone": config.TIMEZONE,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _slack_status() -> str:
    if not (config.SLACK_HEIR_BOT_TOKEN and config.SLACK_HEIR_SIGNING_SECRET):
        return "disabled"
    try:
        from slack.bot import get_slack_app
        return "connected" if get_slack_app() is not None else "disabled"
    except Exception:
        return "error"


@app.post("/api/bd/weekly-review", dependencies=[Depends(require_bd_key)])
async def api_weekly_review(payload: dict) -> dict:
    from scheduler.jobs import weekly_strategic_review
    return await weekly_strategic_review(
        focus=(payload or {}).get("focus"),
        source="api",
    )


@app.post("/api/bd/opportunity-scan", dependencies=[Depends(require_bd_key)])
async def api_opportunity_scan(payload: dict) -> dict:
    from scheduler.jobs import opportunity_scan
    return await opportunity_scan(
        query=(payload or {}).get("query"),
        max_results=(payload or {}).get("max_results", 8),
        source="api",
    )


@app.post("/api/bd/finance-pulse", dependencies=[Depends(require_bd_key)])
async def api_finance_pulse(payload: dict) -> dict:
    months_back = (payload or {}).get("months_back", 3)
    # Two modes: "snapshot" returns raw pulse; anything else runs the Friday
    # narrative pipeline.
    if (payload or {}).get("snapshot"):
        from finance.pulse import get_pulse
        return await get_pulse(months_back=months_back)
    from scheduler.jobs import finance_pulse_report
    return await finance_pulse_report(source="api")


@app.post("/api/bd/recommend-to-stefan", dependencies=[Depends(require_bd_key)])
async def api_recommend(payload: dict) -> dict:
    from brain.dispatch import _recommend_to_stefan
    if not payload or "topic" not in payload or "body" not in payload:
        raise HTTPException(status_code=400, detail="topic and body are required")
    return await _recommend_to_stefan(payload)


@app.post("/api/bd/advise-agent", dependencies=[Depends(require_bd_key)])
async def api_advise_agent(payload: dict) -> dict:
    """Peer review endpoint other agents call for Heir's quality take.

    This endpoint runs a Sonnet call with Heir's persona as the reviewer.
    """
    if not payload or "content" not in payload or "agent" not in payload:
        raise HTTPException(status_code=400, detail="agent and content required")

    from anthropic import AsyncAnthropic
    from brain.heir import PERSONA
    from superknowledge import client as sk

    sk_ctx = await sk.load_strategic_context()
    system = PERSONA + ("\n\n" + sk_ctx if sk_ctx else "")

    user = (
        f"You are reviewing output from agent: {payload['agent']} "
        f"(artifact type: {payload.get('artifact_type','unspecified')}).\n\n"
        f"QUALITY STANDARD RULE: if this would embarrass MLA in front of a "
        f"Fortune 500 client, say so directly.\n\n"
        f"═══ CONTENT TO REVIEW ═══\n{payload['content']}\n\n"
        f"═══ YOUR TASK ═══\n"
        f"Return a Markdown verdict with:\n"
        f"1. One-line verdict: SHIP / REVISE / REWRITE\n"
        f"2. Strengths (bullets)\n"
        f"3. Specific required fixes (bullets, concrete)\n"
        f"4. Score /10"
    )

    client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        resp = await client.messages.create(
            model=config.BD_SONNET_MODEL,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text_parts = [
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ]
        verdict = "\n".join(text_parts).strip() or "(no content)"
    except Exception as e:
        logger.exception("advise-agent LLM call failed")
        return {"error": f"{type(e).__name__}: {e}"}

    return {
        "agent_reviewed": payload["agent"],
        "artifact_type": payload.get("artifact_type"),
        "verdict": verdict,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/bd/query-airtable", dependencies=[Depends(require_bd_key)])
async def api_query_airtable(payload: dict) -> dict:
    """T3 — Ad-hoc Airtable query for Heir.

    Body:
      - table_name: str  ('GRANT_APPLICATION', 'INVOICE', alias accepted)
      - filter_formula: str (Airtable formula — optional)
      - fields: list[str] (optional; defaults to all)
      - max_records: int (default 50, cap 200)
      - sort: list[{field, direction}] (optional)
    """
    from finance.airtable_query import query_airtable
    if not payload or "table_name" not in payload:
        raise HTTPException(status_code=400, detail="table_name required")
    return await query_airtable(
        table_name=payload["table_name"],
        filter_formula=payload.get("filter_formula"),
        fields=payload.get("fields"),
        max_records=payload.get("max_records", 50),
        sort=payload.get("sort"),
    )


@app.post("/api/bd/save-strategic-note", dependencies=[Depends(require_bd_key)])
async def api_save_note(payload: dict) -> dict:
    from superknowledge import client as sk
    if not payload or not all(k in payload for k in ("topic", "content", "category")):
        raise HTTPException(status_code=400, detail="topic, content, category required")
    return await sk.save_strategic_informator(
        topic=payload["topic"],
        content=payload["content"],
        category=payload["category"],
        applies_to=payload.get("applies_to"),
    )


@app.get("/api/bd/recent-recommendations", dependencies=[Depends(require_bd_key)])
async def api_recent_recs(n: int = 20) -> dict:
    recs = await redis_client.get_recent_recommendations(n=n)
    return {"count": len(recs), "recommendations": recs}


# ── Error handler to keep logs tidy ──────────────────────────────────

@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"error": f"{type(exc).__name__}: {exc}"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=config.PORT, reload=False)
