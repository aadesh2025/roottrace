"""S7 `patch` (`03` §S7) — T5.4.

**No `PatchOutcome` wrapper, unlike `reason.stage.ReasonOutcome`.** S6 needed
one because it has two real, differently-shaped terminal states —
`RootCauseAnalysis` or a populated `InsufficientContext` explanation — and
the wrapper carries `dropped_claims` alongside either. S7 has no equivalent
second shape: `04`'s `patches` table has no "failed" row at all (every
column is `not null` except the two genuinely optional ones), so a
terminal S7 failure is not a value to return, it is `PatcherUnavailable`
propagating to whatever calls this — inventing an always-empty failure
branch here just to match T5.3's shape would be the abstraction `CLAUDE.md`
says not to add.

`patch_id` and `base_commit` are caller-supplied, same precedent
`ranking.build_context_bundle`'s own `bundle_id` parameter sets: minting
identifiers and pinning provenance is an orchestration concern, not
something a pipeline stage does for itself."""

from __future__ import annotations

from typing import Any

from roottrace_worker.pipeline.patch.contracts import (
    Alternative,
    FileChange,
    Patch,
    RegressionTest,
    RiskAssessment,
    TokenUsage,
)
from roottrace_worker.pipeline.patch.diffing import parse_diff
from roottrace_worker.pipeline.patch.extraction_schema import PatchReply
from roottrace_worker.pipeline.patch.patcher import (
    PatchErrorCode,
    PatcherUnavailable,
    PatchRequest,
    StructuredPatcher,
)
from roottrace_worker.pipeline.patch.validate import validate_patch
from roottrace_worker.pipeline.reason.contracts import RootCauseAnalysis
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle


async def patch(
    bundle: ContextBundle,
    analysis: RootCauseAnalysis,
    *,
    patcher: StructuredPatcher,
    patch_id: str,
    base_commit: str,
) -> Patch:
    request = PatchRequest(bundle=bundle, analysis=analysis)

    # Typed `Any` deliberately, same reasoning as `reason.stage.reason`'s
    # identical `raw: Any` — the Protocol promises a `Mapping`, but the
    # implementation behind this seam is a model reply and this stage must
    # never crash on one.
    raw: Any = await patcher.patch(request)

    if not isinstance(raw, dict):
        raise PatcherUnavailable("patcher returned a non-mapping reply", error_code="RT-AI-0006")

    call_meta = {k: raw.get(k) for k in ("model", "prompt_version", "tokens")}
    reply = PatchReply.model_validate({k: v for k, v in raw.items() if k not in call_meta})

    # The stage does not trust a patcher's word for it either — same
    # discipline `reason.stage.reason` documents for its own re-check of
    # `primary_finding_survives` after `GatewayReasoner` already ran it.
    validation = validate_patch(reply, bundle=bundle, analysis=analysis)
    if not validation.ok:
        reason = (
            validation.scope_violation.reason
            if validation.scope_violation is not None
            else (
                validation.applicability_failure.reason if validation.applicability_failure else ""
            )
        )
        code: PatchErrorCode = (
            "RT-AI-0005" if validation.scope_violation is not None else "RT-AI-0006"
        )
        raise PatcherUnavailable(f"a patcher returned an invalid patch: {reason}", error_code=code)

    parsed = parse_diff(reply.diff)
    assert parsed.patch_set is not None  # noqa: S101 - `validation.ok` guarantees a clean parse
    test_paths = {t.repo_path for t in bundle.tests.found}
    files_changed = tuple(
        FileChange(
            repo_path=stat.repo_path,
            additions=stat.additions,
            deletions=stat.deletions,
            hunks=stat.hunks,
            is_new_test=stat.is_added_file and stat.repo_path not in test_paths,
        )
        for stat in parsed.file_stats
    )

    regression_test = (
        RegressionTest(**reply.regression_test.model_dump())
        if reply.regression_test is not None
        else None
    )
    risk_assessment = RiskAssessment(**reply.risk_assessment.model_dump())
    alternatives = tuple(Alternative(**a.model_dump()) for a in reply.alternatives_considered)

    scope_warning = validation.scope_warning
    if validation.manifest_warning is not None:
        scope_warning = (
            validation.manifest_warning
            if scope_warning is None
            else f"{scope_warning}; {validation.manifest_warning}"
        )

    tokens_raw = call_meta.get("tokens") or {}

    return Patch(
        patch_id=patch_id,
        base_commit=base_commit,
        diff=reply.diff,
        files_changed=files_changed,
        explanation=reply.explanation,
        regression_test=regression_test,
        risk_assessment=risk_assessment,
        alternatives_considered=alternatives,
        scope_warning=scope_warning,
        model=str(call_meta.get("model") or "unknown"),
        prompt_version=str(call_meta.get("prompt_version") or "unknown"),
        tokens=TokenUsage(**tokens_raw) if isinstance(tokens_raw, dict) else TokenUsage(),
    )
