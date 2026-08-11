"""RootTrace AI Python SDK.

``init`` · ``capture_exception`` · ``add_breadcrumb`` · FastAPI middleware,
with batching, retry, and a local buffer (T2.5, ``docs/15`` §4).

**Never-raises guarantee.** This library runs inside someone else's production
request path. A failure to report an error must never become an error.
"""

__version__ = "0.1.0"
