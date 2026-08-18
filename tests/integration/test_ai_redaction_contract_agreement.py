"""The one place `apps/api` and `apps/worker` are imported together to
catch drift between two intentionally-duplicated pattern tables — same
precedent as `test_sdk_contract_agreement.py`.

`apps/worker/roottrace_worker/ai/redaction.py` deliberately duplicates
`apps/api/roottrace_api/ingest/sanitise.py`'s secret-pattern table rather
than importing it (`apps/worker` declares no dependency on `apps/api` —
separate deployables, separate privilege boundaries). Duplication without a
drift check is how a security control quietly stops meaning what its
comment says it means; this is that check, and it belongs at this shared
top-level location because it is the only test in the suite that imports
both packages.
"""

from __future__ import annotations

import pytest

from roottrace_api.ingest import sanitise as ingest_sanitise
from roottrace_worker.ai import redaction as ai_redaction

pytestmark = pytest.mark.integration


def test_shared_pattern_kinds_use_identical_regexes() -> None:
    ingest_patterns = dict(ingest_sanitise._PATTERNS)
    ai_patterns = dict(ai_redaction._PATTERNS)

    shared_kinds = set(ingest_patterns) & set(ai_patterns)
    assert shared_kinds, "expected at least one shared pattern kind between the two modules"
    for kind in shared_kinds:
        assert ingest_patterns[kind].pattern == ai_patterns[kind].pattern, (
            f"{kind}: apps/api ingest and apps/worker ai/redaction patterns have drifted apart"
        )


def test_entropy_thresholds_agree() -> None:
    assert ingest_sanitise.ENTROPY_THRESHOLD == ai_redaction.ENTROPY_THRESHOLD
    assert ingest_sanitise.ENTROPY_MIN_LENGTH == ai_redaction.ENTROPY_MIN_LENGTH
