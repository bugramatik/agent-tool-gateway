"""Entry point: build settings from the environment and serve the gateway with uvicorn."""

from __future__ import annotations

import logging

import uvicorn

from gateway.api import create_app
from gateway.config import get_settings


def main() -> None:
    """Run the gateway on the host and port given by the environment."""
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
