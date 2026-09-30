"""Make pansoph audit events observable (CM-M8, local POC scope).

pansoph writes audit events as JSON lines on the `pansoph.audit` logger, but nothing in
the repository attaches a handler, so under Python defaults those INFO records are
dropped. This attaches one stdout handler, so `docker compose logs` (and
`company-memory audit`) show who queried, when, which tool, the tenant, the decision, and
the source ids returned. The events never contain tokens, secrets, or document text.
A production deployment would ship these lines to an append-only store instead.
"""

from __future__ import annotations

import logging
import sys

_MARK = "_company_memory_audit_handler"


def configure_audit_logging() -> None:
    logger = logging.getLogger("pansoph.audit")
    if any(getattr(h, _MARK, False) for h in logger.handlers):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("AUDIT %(message)s"))
    setattr(handler, _MARK, True)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
