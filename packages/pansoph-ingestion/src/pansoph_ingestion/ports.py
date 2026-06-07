"""Ingestion ports: Parser, LLM, and the durable IngestionWorkflow seam."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar, runtime_checkable

from pansoph_types import RawDoc, Section
from pydantic import BaseModel

T = TypeVar("T")


class ParsedDoc(BaseModel):
    """The structured result of parsing a RawDoc: ordered sections plus metadata."""

    title: str | None = None
    lang: str | None = None
    sections: list[Section] = []


@runtime_checkable
class Parser(Protocol):
    async def parse(self, raw: RawDoc) -> ParsedDoc:
        """Parse a raw document into ordered, layout aware sections. BYO parser."""
        ...


@runtime_checkable
class LLM(Protocol):
    model: str

    async def complete(self, prompt: str, *, cache: bool = False) -> str:
        """Single completion. `cache` requests prompt caching, mandatory for
        contextual retrieval cost control. Provider agnostic, BYOM."""
        ...


@runtime_checkable
class Step(Protocol):
    """A durable, idempotent, separately retryable unit of work.

    All side effects (HTTP fetches, embedding calls, upserts) live in steps, never
    in the orchestrator, so the orchestrator stays deterministic and replayable.
    """

    def __call__(self, fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]: ...


@runtime_checkable
class IngestionWorkflow(Protocol):
    """The durable execution seam. Implemented over DBOS (Phase 1) or Hatchet (scale).

    Connector code never touches engine APIs; it composes steps and the engine
    provides checkpoint, replay, retry, backoff, cron, and webhook signals.
    """

    step: Step

    async def start(self, instance_id: str) -> str:
        """Start (or resume) the durable sync workflow for a connector instance."""
        ...
