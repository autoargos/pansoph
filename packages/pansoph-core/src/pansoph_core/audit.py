"""Append-only audit log.

Every connector action and admin operation, and every retrieval on behalf of a
user, is recorded as a structured JSON line on the dedicated `pansoph.audit` logger.
An operator routes that logger to an immutable sink (an append-only file, or a
write-once store). This is the audit trail the security model requires.

The log never contains document contents or secrets, only who did what, over which
tenant, and the shape of the result.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_logger = logging.getLogger("pansoph.audit")


def audit(action: str, *, actor: str, tenant: str, **fields: Any) -> None:
    """Record one audit event. Returns nothing; never raises on logging failure."""
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "action": action,
        "actor": actor,
        "tenant": tenant,
        **fields,
    }
    try:
        _logger.info(json.dumps(record, default=str))
    except Exception:  # noqa: BLE001  audit must never break the request path
        _logger.info("audit-serialization-failed action=%s actor=%s", action, actor)
