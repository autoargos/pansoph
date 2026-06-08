"""A simple in-process interval scheduler for connector syncs.

It periodically runs each enabled instance whose schedule has elapsed since its last
run. This is the local default. A production deployment runs syncs on the durable
execution engine (DBOS/Hatchet), which survives restarts and coordinates across
replicas; this loop does neither and is intentionally simple.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from .connectors import ConnectorManager, run_instance

_log = logging.getLogger("pansoph.scheduler")


def _is_due(instance: dict, now: datetime) -> bool:
    if not instance.get("enabled"):
        return False
    last = instance.get("last_run")
    # A run still in progress is not due again.
    if last and last.get("status") == "running":
        return False
    last_started = (
        datetime.fromisoformat(last["started_at"]) if last and last.get("started_at") else None
    )

    cron = instance.get("schedule_cron")
    if cron:
        from croniter import croniter

        try:
            prev_slot = croniter(cron, now).get_prev(datetime)
        except (ValueError, KeyError):
            return False
        return last_started is None or last_started < prev_slot

    schedule = instance.get("schedule_seconds") or 0
    if schedule <= 0:
        return False
    if last_started is None:
        return True
    return (now - last_started).total_seconds() >= schedule


async def scheduler_loop(manager: ConnectorManager, tick_seconds: int = 15) -> None:
    while True:
        try:
            now = datetime.now(UTC)
            for instance in await manager.list_instances():
                if _is_due(instance, now):
                    _log.info("scheduler: syncing instance %s", instance["id"])
                    await run_instance(manager, instance)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001  the scheduler must keep running
            _log.exception("scheduler tick failed")
        await asyncio.sleep(tick_seconds)
