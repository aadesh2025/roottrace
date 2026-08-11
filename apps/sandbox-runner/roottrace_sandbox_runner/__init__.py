"""RootTrace AI sandbox runner.

Executes gates G0-G8 (``docs/07`` §6) inside the hardened container: no
network, no credentials, read-only rootfs, non-root, all capabilities dropped.

Everything this module touches is hostile by default — the diff, the source,
and the model output alike.
"""

__version__ = "0.1.0"
