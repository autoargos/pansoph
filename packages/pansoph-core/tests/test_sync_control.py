"""Hermetic tests for the background sync control: start, stop, and orphan cleanup.

run_instance itself resolves real adapters and Postgres, so it is monkeypatched with a
controllable coroutine; these tests pin the registry behavior around it (a second sync is
refused, a Stop cancels the task, a Stop with no live task closes out an orphaned run, and
startup interrupts leftover runs). No em dashes anywhere.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pansoph_core import connectors as C


class _FakeManager:
    """Records the run-status calls the control paths make; no database."""

    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.interrupted = 0

    async def mark_running_stopped(self, instance_id: str) -> int:
        self.stopped.append(instance_id)
        return 1

    async def mark_all_running_interrupted(self) -> int:
        self.interrupted += 1
        return 3


@pytest.fixture(autouse=True)
def _clear_registry() -> Any:
    C._running.clear()
    yield
    C._running.clear()


async def test_start_sync_refuses_a_second_concurrent_run(monkeypatch: Any) -> None:
    gate = asyncio.Event()

    async def _slow(_manager: Any, _instance: dict[str, Any]) -> dict[str, Any]:
        await gate.wait()
        return {"status": "success", "docs_indexed": 0}

    monkeypatch.setattr(C, "run_instance", _slow)
    mgr = _FakeManager()
    inst = {"id": "i1"}

    first = await C.start_sync(mgr, inst)
    assert first == {"status": "started"}
    assert C.is_running("i1")
    # A second start while the first is in flight is refused, not run concurrently.
    assert await C.start_sync(mgr, inst) == {"status": "already_running"}

    gate.set()
    await asyncio.sleep(0)  # let the task finish and deregister
    await asyncio.sleep(0)
    assert not C.is_running("i1")


async def test_stop_sync_cancels_a_live_task(monkeypatch: Any) -> None:
    async def _forever(_manager: Any, _instance: dict[str, Any]) -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"status": "success", "docs_indexed": 0}

    monkeypatch.setattr(C, "run_instance", _forever)
    mgr = _FakeManager()
    await C.start_sync(mgr, {"id": "i2"})
    assert C.is_running("i2")

    assert await C.stop_sync(mgr, "i2") == {"status": "stopping"}
    await asyncio.sleep(0)  # let the cancellation unwind and deregister
    await asyncio.sleep(0)
    assert not C.is_running("i2")
    # A live task was cancelled, so the orphan-row fallback was not used.
    assert mgr.stopped == []


async def test_stop_sync_with_no_live_task_clears_an_orphaned_run() -> None:
    mgr = _FakeManager()
    # No task registered (e.g. the sync was orphaned by a restart): the lingering run row
    # is closed out instead.
    assert await C.stop_sync(mgr, "orphan") == {"status": "stopped"}
    assert mgr.stopped == ["orphan"]


async def test_cleanup_orphan_runs_interrupts_leftover_runs() -> None:
    mgr = _FakeManager()
    assert await C.cleanup_orphan_runs(mgr) == 3
    assert mgr.interrupted == 1
