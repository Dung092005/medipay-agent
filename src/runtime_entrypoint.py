"""Distroless-compatible API entrypoint.

The production image has no shell, so the port and worker settings are read
directly from the environment before starting Uvicorn.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        workers=max(1, int(os.environ.get("WEB_CONCURRENCY", "1"))),
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
