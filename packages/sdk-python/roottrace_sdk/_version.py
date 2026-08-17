"""The single source of the version.

Separate from ``__init__`` so that internal modules — the transport needs it
for its ``User-Agent`` — can read it without importing the package root, which
imports them back.
"""

__version__ = "0.1.0"
