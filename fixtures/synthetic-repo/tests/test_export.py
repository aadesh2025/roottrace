"""Export — 4 tests, **1 deliberately failing**.

`test_header_includes_created_at` fails and always has. Finance asked for the
column, the header constant was never updated, and the export has shipped
without it for three releases.

Like the failure in `test_webhooks.py`, it is deliberately unrelated to the 25
fixture bugs so that gate G6's `already_failing` classification is exercised by
something no patch will ever accidentally fix.
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from services.export import CSV_HEADER, ExportService


@pytest.fixture
def service() -> ExportService:
    return ExportService(
        Settings(
            environment="test",
            tax_service_url="http://tax.internal",
            inventory_service_url="http://inv",
            payment_service_url="http://pay",
            export_batch_size=100,
        )
    )


def test_batch_size_reads_the_setting(service):
    assert service.batch_size() == 100


def test_render_row(service):
    row = service.render_row(
        {
            "id": "ord_1",
            "user": {"email": "ada@example.com"},
            "total": "49.99",
            "currency": "USD",
            "status": "paid",
        }
    )
    assert row.startswith("ord_1,ada@example.com")


def test_export_all_writes_a_header(service):
    csv = service.export_all([])
    assert csv.splitlines()[0] == CSV_HEADER


def test_header_includes_created_at():
    """FAILS ON PURPOSE — see the module docstring."""
    assert "created_at" in CSV_HEADER
