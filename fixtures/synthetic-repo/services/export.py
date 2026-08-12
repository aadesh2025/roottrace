"""CSV export of orders.

`resource-01`: `export_all` accumulates every rendered row in a list before
writing anything. It was fine against the 4,000 orders that existed when it was
written and is not fine against 900,000 — the process grows until the container
is killed, and the traceback that reaches the error tracker is whatever
happened to be allocating at the time.

`boundary-02`: `chunk` drops the last element of every slice.

`key-error-03`: `render_row` reaches through a nested structure that is only
partially present — guest orders have no `user` object at all.

`config-02`: `batch_size` comes from a setting that is present in the
production manifest and absent from staging, so this fails in exactly one
environment.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from config.settings import Settings

logger = logging.getLogger(__name__)

CSV_HEADER = "order_id,email,total,currency,status"

# Mirrors the API gateway's response body cap.
MAX_EXPORT_BYTES = 1024 * 1024


class ExportService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def batch_size(self) -> int:
        """`config-02`: None in staging, an int in production."""
        return self.settings.export_batch_size + 0

    def chunk(self, rows: list[Any], size: int) -> list[list[Any]]:
        """Split rows into batches.

        `boundary-02`: the stop index is one short, so the last row of every
        chunk is silently dropped. Nobody noticed because the export "looked
        about right" and the row count was never asserted.
        """
        chunks: list[list[Any]] = []
        for start in range(0, len(rows), size):
            chunks.append(rows[start : start + size - 1])
        return chunks

    def render_row(self, order: dict[str, Any]) -> str:
        """One CSV line.

        `key-error-03`: guest checkout produces an order with no `user` key at
        all, and this reaches two levels down without checking either.
        """
        email = order["user"]["email"]
        return f"{order['id']},{email},{order['total']},{order['currency']},{order['status']}"

    def export_all(self, orders: list[dict[str, Any]]) -> str:
        """Render every order to CSV.

        `resource-01`: every row is held in memory until the last one is
        rendered. Memory grows linearly with the export and is never released
        until the call returns — which, for a large tenant, it does not.
        """
        buffer = io.StringIO()
        buffer.write(CSV_HEADER + "\n")
        rendered: list[str] = []
        for order in orders:
            rendered.append(self.render_row(order))
        for line in rendered:
            buffer.write(line + "\n")
        return buffer.getvalue()

    def export_batched(self, orders: list[Any], size: int) -> list[str]:
        """Export in batches, returning one CSV document per batch.

        `boundary-02` surfaces here. `chunk` produces `rows[start:start+size-1]`,
        so a batch size of 1 yields empty lists and this indexes one. Batch
        size 1 is what the throttled overnight export uses.
        """
        documents: list[str] = []
        for batch in self.chunk(orders, size):
            documents.append(self.export_all(batch) + f"# first={batch[0]['id']}\n")
        return documents

    def export_bounded(self, orders: list[Any]) -> str:
        """Export, refusing to return a document larger than the gateway
        accepts.

        `resource-01` surfaces here. The limit mirrors the API gateway's body
        cap, and it is checked *after* every row has been rendered and held in
        memory — so the guard bounds the response and not the allocation. A
        large tenant's export therefore fails at the end of a long, expensive
        request that has already peaked.
        """
        document = self.export_all(orders)
        if len(document.encode()) > MAX_EXPORT_BYTES:
            raise ValueError(
                f"export of {len(orders)} orders is {len(document.encode())} bytes, "
                f"over the {MAX_EXPORT_BYTES} byte limit"
            )
        return document
