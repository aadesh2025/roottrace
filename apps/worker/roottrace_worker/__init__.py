"""RootTrace AI pipeline workers.

Runs the 14 stages (``docs/03``) on ARQ. Connects as ``service_role`` and
therefore **bypasses RLS** — so every tenant-table query goes through
``TenantRepository``, which raises if constructed without an explicit
``project_id`` (``docs/04`` §12 "Worker access").
"""

__version__ = "0.1.0"
