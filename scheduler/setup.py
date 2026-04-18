"""scheduler/setup.py — APScheduler bootstrap for Heir.

Hooks the 3 strategic jobs onto Europe/Podgorica cron triggers and
exposes `start_scheduler()` / `stop_scheduler()` for the FastAPI
lifespan.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

import config
from scheduler.jobs import (
    finance_pulse_report,
    opportunity_scan,
    weekly_strategic_review,
)

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def _enabled(flag_name: str) -> bool:
    raw = str(getattr(config, flag_name, "on") or "").strip().lower()
    return raw not in {"", "0", "off", "false", "no", "disabled"}


def start_scheduler() -> Optional[AsyncIOScheduler]:
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    tz = pytz.timezone(config.TIMEZONE)
    _scheduler = AsyncIOScheduler(timezone=tz)

    if _enabled("HEIR_WEEKLY_ENABLED"):
        _scheduler.add_job(
            _run_weekly,
            CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=tz),
            id="heir_weekly_strategic_review",
            replace_existing=True,
        )
        logger.info("Scheduled: weekly_strategic_review — Mon 09:00 %s", config.TIMEZONE)

    if _enabled("HEIR_FINANCE_ENABLED"):
        _scheduler.add_job(
            _run_finance_pulse,
            CronTrigger(day_of_week="fri", hour=9, minute=15, timezone=tz),
            id="heir_finance_pulse",
            replace_existing=True,
        )
        logger.info("Scheduled: finance_pulse_report — Fri 09:15 %s", config.TIMEZONE)

    if _enabled("HEIR_SCAN_ENABLED"):
        _scheduler.add_job(
            _run_opportunity_scan,
            CronTrigger(hour=10, minute=0, timezone=tz),
            id="heir_opportunity_scan",
            replace_existing=True,
        )
        logger.info("Scheduled: opportunity_scan — Daily 10:00 %s", config.TIMEZONE)

    _scheduler.start()
    logger.info("Heir scheduler started with %d jobs", len(_scheduler.get_jobs()))
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        try:
            _scheduler.shutdown(wait=False)
        except Exception as e:
            logger.warning(f"Scheduler shutdown error: {e}")
        _scheduler = None


# ── Thin wrappers that log exceptions instead of crashing the loop ──

async def _run_weekly() -> None:
    try:
        await weekly_strategic_review(source="cron")
    except Exception as e:
        logger.exception("weekly_strategic_review cron failed: %s", e)


async def _run_finance_pulse() -> None:
    try:
        await finance_pulse_report(source="cron")
    except Exception as e:
        logger.exception("finance_pulse_report cron failed: %s", e)


async def _run_opportunity_scan() -> None:
    try:
        await opportunity_scan(source="cron")
    except Exception as e:
        logger.exception("opportunity_scan cron failed: %s", e)
