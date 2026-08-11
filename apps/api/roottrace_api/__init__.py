"""RootTrace AI public API.

Serves ingest (``POST /v1/events``), the dashboard REST surface, and the
WebSocket hub. Runs as ``RT_SERVICE_NAME=api``.

This service **must not** hold ``RT_SUPABASE_SERVICE_ROLE_KEY``; a boot
invariant in ``docs/A3`` §6 refuses to start if it does.
"""

__version__ = "0.1.0"
