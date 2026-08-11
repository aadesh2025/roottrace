"""Phase 1 workspace integrity check.

This is not a placeholder. A uv workspace can be misconfigured in ways that
produce no error until something imports across package boundaries: a member
missing from ``[tool.uv.workspace]``, a wheel-packages entry that does not
match the directory, or a name collision between two members. This test fails
on all three, which is exactly the class of defect T1.1 can ship.
"""

import pytest

import roottrace_api


@pytest.mark.unit
def test_package_imports_and_declares_version() -> None:
    assert roottrace_api.__version__ == "0.1.0"
