"""Process entrypoint.

`uvicorn roottrace_api.main:create_app --factory` works, but uvicorn configures
its own logging and emits its first lines ("Uvicorn running on…") *before* it
builds the app — so those lines escape the processor chain and arrive as plain
text. An aggregator then sees a handful of unparseable lines at every boot, and
the "structured logging" property is true only after the first request.

Starting uvicorn from here fixes the ordering: settings are validated, the chain
is installed, and only then does the server start. The boot invariants also run
before the port is opened, so a misconfigured deployment never accepts a single
request.
"""

from __future__ import annotations

import uvicorn

from roottrace_api.auth.dependencies import get_settings
from roottrace_api.log import configure_logging, get_logger

#: `13` §3. Two per container; horizontal scale is the platform's job.
WORKERS = 2
PORT = 8000


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    get_logger(__name__).info("server_starting", workers=WORKERS, port=PORT)

    uvicorn.run(
        "roottrace_api.main:create_app",
        factory=True,
        # Containers must bind every interface; the port is published by the
        # platform, not exposed by this process.
        host="0.0.0.0",  # noqa: S104
        port=PORT,
        workers=WORKERS,
        # None, not a config dict: our chain is already installed, and
        # uvicorn's default config would replace the root handler with a plain
        # text one.
        log_config=None,
        # Our middleware emits one structured line per request, carrying the
        # request id and the duration that uvicorn's access log lacks.
        access_log=False,
    )


if __name__ == "__main__":
    main()
