"""Entry point: `pansoph` runs the API with uvicorn."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .settings import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
