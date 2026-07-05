"""Durable execution wrapper for the ingestion pipeline, using DBOS.

DBOS makes the per-instance sync resumable: each document ingest is a durable step,
so a crash at document 8,000 of 10,000 resumes rather than restarts, and the cursor
is checkpointed as a step output. This wrapper keeps the engine out of the pipeline
and the connectors: they only ever see plain async calls.

DBOS is an optional dependency (install pansoph-ingestion[dbos]) and must be
configured and launched by agora before these workflows run. Hatchet is the
documented scale step up and would implement the same shape.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from pansoph_types import DocRef

from .pipeline import IngestionPipeline


def register_sync_workflow(
    pipeline: IngestionPipeline, *, connector_name: str, tenant_id: str
) -> Callable[[str | None], Awaitable[str]]:
    """Register a durable sync workflow for one connector instance and return it.

    The returned coroutine runs the instance's sync under DBOS: list_changes drives
    the loop, each document is ingested in a durable step, and the latest cursor is
    returned (and checkpointed by DBOS) so a re-run resumes from it.
    """
    from dbos import DBOS

    @DBOS.step()
    async def _ingest_step(ref: DocRef) -> None:
        await pipeline.ingest_one(connector_name=connector_name, tenant_id=tenant_id, ref=ref)

    @DBOS.step()
    async def _delete_step(ref: DocRef) -> None:
        # A tombstone is a durable step too, so a crash mid-purge resumes rather than
        # leaving a half-deleted document indexed but tuple-less (or vice versa).
        await pipeline.delete_one(connector_name=connector_name, ref=ref)

    @DBOS.workflow()
    async def _sync(cursor: str | None = None) -> str:
        next_cursor = cursor or ""
        async for ref, cur in pipeline.connector.list_changes(cursor):
            if ref.deleted:
                await _delete_step(ref)
            else:
                await _ingest_step(ref)
            next_cursor = cur
        return next_cursor

    return _sync
