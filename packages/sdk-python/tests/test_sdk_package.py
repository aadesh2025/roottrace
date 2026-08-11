"""Phase 1 workspace integrity check — see ``apps/api/tests/test_api_package.py``."""

import pytest

import roottrace_sdk


@pytest.mark.unit
def test_package_imports_and_declares_version() -> None:
    assert roottrace_sdk.__version__ == "0.1.0"
