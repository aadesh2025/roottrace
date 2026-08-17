"""The 14 pipeline stages (`03` §4).

Each stage is a module here. Stages are **pure with respect to orchestration**:
they take a validated input, return a validated output, and know nothing about
queues, retries, `pipeline_steps` rows or WebSocket frames. The orchestrator
(T8.2) owns all six universal invariants in `03` §8.2 — a stage that wrote its
own step row would write a second one when the orchestrator resumed it.
"""

from __future__ import annotations
