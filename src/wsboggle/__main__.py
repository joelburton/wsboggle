"""Entry point for ``python -m wsboggle``.

Runs the Uvicorn ASGI server with the FastAPI app from
``wsboggle.app``. Honours ``HOST`` / ``PORT`` / ``WSBOGGLE_DEV`` from
the environment via :mod:`wsboggle.config`.
"""

from __future__ import annotations

import uvicorn

from wsboggle.config import settings


def main() -> None:
    """Boot the server."""
    uvicorn.run(
        "wsboggle.app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.dev,
    )


if __name__ == "__main__":
    main()
