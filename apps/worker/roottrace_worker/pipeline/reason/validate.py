"""Evidence binding (`03` §S6 "Hard rule — evidence binding") — T5.3.

> Every `finding` must carry an `evidence` array where each entry
> references a real artefact in the context bundle. ... Any finding
> failing validation is discarded before it reaches the user.

`06` §4.2's S6 row names the same four checks precisely: every evidence
reference resolves to retrieved content; quoted excerpts match source
(whitespace-normalised); cited commits exist in the bundle;
`files_to_modify` ⊆ retrieved paths. This module is the deterministic,
non-negotiable second validation layer `06` §4.2 describes — schema
validation (did the reply parse into `ReasonReply`) is necessary but not
sufficient; this is what actually catches a hallucinated citation, because
a fabricated excerpt cannot survive a literal string comparison against
retrieved source.

**A step or hypothesis with zero declared evidence is not thereby
invalid.** `03` §S6's own worked example has a `type: "hypothesise"` step
with no `evidence` key at all — a hypothesis is proposed, not yet grounded,
and grounding is exactly what step 3 (`TEST`) exists to add. What is
rejected is a step that *claims* evidence and gets any of it wrong, not a
step that is honestly speculative."""

from __future__ import annotations

from collections.abc import Sequence

from roottrace_worker.pipeline.reason.contracts import (
    EliminatedHypothesis,
    Evidence,
    ReasoningStep,
)
from roottrace_worker.pipeline.reason.extraction_schema import (
    ReasonEliminatedHypothesis,
    ReasonEvidence,
    ReasonFixStrategy,
    ReasonReply,
    ReasonStep,
)
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle


def _normalise_whitespace(text: str) -> str:
    return " ".join(text.split())


def _file_content_by_path(bundle: ContextBundle) -> dict[str, tuple[tuple[int, int], str]]:
    return {file.repo_path: (file.line_range, file.content) for file in bundle.files}


def _commit_shas(bundle: ContextBundle) -> frozenset[str]:
    shas = set()
    if bundle.history.blame_commit is not None:
        shas.add(bundle.history.blame_commit.sha)
    shas.update(commit.sha for commit in bundle.history.recent_commits)
    return frozenset(shas)


def evidence_is_bound(
    item: ReasonEvidence,
    *,
    bundle: ContextBundle,
    breadcrumb_count: int,
    files_by_path: dict[str, tuple[tuple[int, int], str]] | None = None,
    commit_shas: frozenset[str] | None = None,
) -> bool:
    """The four `06` §4.2 checks, for one evidence entry."""
    if item.kind == "file":
        if item.repo_path is None or item.line_range is None or item.excerpt is None:
            return False
        files = files_by_path if files_by_path is not None else _file_content_by_path(bundle)
        entry = files.get(item.repo_path)
        if entry is None:
            return False
        (file_start, file_end), content = entry
        start, end = item.line_range
        if not (file_start <= start <= end <= file_end):
            return False
        return _normalise_whitespace(item.excerpt) in _normalise_whitespace(content)

    if item.kind == "breadcrumb":
        return item.index is not None and 0 <= item.index < breadcrumb_count

    shas = commit_shas if commit_shas is not None else _commit_shas(bundle)
    return item.sha is not None and item.sha in shas


def _bind_evidence_list(
    items: Sequence[ReasonEvidence],
    *,
    bundle: ContextBundle,
    breadcrumb_count: int,
    files_by_path: dict[str, tuple[tuple[int, int], str]],
    commit_shas: frozenset[str],
) -> tuple[Evidence, ...] | None:
    """`None` means the whole finding is discarded — at least one declared
    piece of evidence failed to bind. An empty tuple (no evidence declared
    at all) is a valid, distinct outcome: the finding is speculative, not
    unsupported."""
    if not items:
        return ()
    for item in items:
        if not evidence_is_bound(
            item,
            bundle=bundle,
            breadcrumb_count=breadcrumb_count,
            files_by_path=files_by_path,
            commit_shas=commit_shas,
        ):
            return None
    return tuple(
        Evidence(
            kind=item.kind,
            repo_path=item.repo_path,
            line_range=item.line_range,
            excerpt=item.excerpt,
            index=item.index,
            sha=item.sha,
        )
        for item in items
    )


def validate_reasoning_chain(
    steps: Sequence[ReasonStep], *, bundle: ContextBundle, breadcrumb_count: int
) -> tuple[tuple[ReasoningStep, ...], tuple[str, ...]]:
    files_by_path = _file_content_by_path(bundle)
    commit_shas = _commit_shas(bundle)
    kept: list[ReasoningStep] = []
    dropped: list[str] = []

    for step in steps:
        evidence = _bind_evidence_list(
            step.evidence,
            bundle=bundle,
            breadcrumb_count=breadcrumb_count,
            files_by_path=files_by_path,
            commit_shas=commit_shas,
        )
        if evidence is None:
            dropped.append(f"reasoning_chain[step={step.step}]: unbound evidence")
            continue
        kept.append(
            ReasoningStep(
                step=step.step or len(kept) + 1,
                type=step.type,
                statement=step.statement,
                prior=step.prior,
                supports=tuple(step.supports),
                evidence=evidence,
            )
        )

    return tuple(kept), tuple(dropped)


def validate_eliminated_hypotheses(
    items: Sequence[ReasonEliminatedHypothesis], *, bundle: ContextBundle, breadcrumb_count: int
) -> tuple[tuple[EliminatedHypothesis, ...], tuple[str, ...]]:
    files_by_path = _file_content_by_path(bundle)
    commit_shas = _commit_shas(bundle)
    kept: list[EliminatedHypothesis] = []
    dropped: list[str] = []

    for item in items:
        evidence = _bind_evidence_list(
            item.evidence,
            bundle=bundle,
            breadcrumb_count=breadcrumb_count,
            files_by_path=files_by_path,
            commit_shas=commit_shas,
        )
        if evidence is None:
            dropped.append(f"eliminated_hypotheses: unbound evidence for {item.statement!r}")
            continue
        kept.append(
            EliminatedHypothesis(
                statement=item.statement,
                eliminated_because=item.eliminated_because,
                evidence=evidence,
            )
        )

    return tuple(kept), tuple(dropped)


def fix_strategy_is_grounded(fix_strategy: ReasonFixStrategy, *, bundle: ContextBundle) -> bool:
    """`06` §4.2: `files_to_modify` ⊆ retrieved paths. A patch stage (T5.4)
    editing a file S6 never actually retrieved would be acting on an
    invented target."""
    retrieved = {file.repo_path for file in bundle.files}
    return all(path in retrieved for path in fix_strategy.files_to_modify)


def primary_finding_survives(
    kept_steps: Sequence[ReasoningStep], *, reply: ReasonReply, bundle: ContextBundle
) -> bool:
    """`03` §S6: "If the primary root-cause finding fails validation, the
    stage retries once ... if it fails again, terminal `insufficient_context`."

    The primary finding is judged, not asserted: it survives only if the
    chain that was supposed to justify it actually did — at least one
    `conclude`-type step bound to real evidence, and the fix strategy
    targets only files S5 actually retrieved. A `root_cause` object with
    prose but no surviving chain behind it is exactly the "confident wrong
    answer" `A2` §2 rule 3 calls the worst possible output."""
    has_conclusion = any(step.type == "conclude" for step in kept_steps)
    return has_conclusion and fix_strategy_is_grounded(reply.fix_strategy, bundle=bundle)
