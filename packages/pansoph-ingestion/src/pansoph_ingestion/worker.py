"""scribe worker entry point.

The real worker registers the durable ingestion workflow with the execution
engine (DBOS) and serves it. That lands with task #16. For now this is a holding
process so the container image builds, runs, and stays healthy without pretending
to do work it does not yet do.
"""

from __future__ import annotations

import time


def main() -> None:
    print("pansoph scribe: ingestion worker placeholder. Workflow lands with ADR-0002 task #16.")
    # Stay alive so the workers compose profile does not crash loop, but do nothing.
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
