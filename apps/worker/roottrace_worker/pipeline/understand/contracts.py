"""The `ErrorUnderstanding` contract (`03` §S4 "Output contract").

**R4:** *input and output are validated Pydantic models. An LLM returning
malformed JSON fails loudly at the boundary, not silently three stages later.*
That is the reason these are Pydantic and not the frozen dataclasses used
elsewhere in the worker: this is the first contract in the system whose
producer is a language model, and `A2` §1 requires the schema shown in the
prompt and the schema used to validate the reply to be the same object.

Two settings carry weight:

- **`extra="forbid"`.** A model that invents a field is not producing the
  contract, and silently dropping the extra field would hide that. It is also
  the cheapest possible defence against a prompt-injected payload persuading
  the extractor to emit something the downstream stages were not written for.
- **`frozen=True`.** S5 ranks against these values and S6 binds evidence to
  them by literal comparison (H1/H2). A stage that could mutate the
  understanding it was handed could change what a later stage believes was
  extracted.

`flags` is the one field not shown in `03` §S4's example JSON. The failure-mode
table immediately below that example requires `low_frame_confidence` to be
flagged and requires an absent stack trace to be "flagged prominently in the
UI", and there was nowhere to put either. Added here and recorded in `03` §S4
in the same commit.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ExceptionFamily(StrEnum):
    """The nine families of `03` §S4, plus an honest tenth.

    `UNCLASSIFIED` is not in the spec's table and is deliberate. The table
    covers the exception types a Python service raises in practice, not every
    type it *can* raise, and the alternative to admitting ignorance is
    defaulting to a family whose retrieval hint then sends S5 to fetch the
    wrong code. A wrong hint is worse than no hint, and unlike no hint it is
    invisible. It also keeps the family-accuracy metric honest: a fallback that
    quietly said `type_mismatch` would score as a correct answer whenever the
    truth happened to be `type_mismatch`.
    """

    NULL_UNDEFINED = "null_undefined"
    TYPE_MISMATCH = "type_mismatch"
    KEY_INDEX = "key_index"
    INTEGRATION = "integration"
    DATA_DB = "data_db"
    CONCURRENCY = "concurrency"
    RESOURCE = "resource"
    AUTH = "auth"
    SERIALIZATION = "serialization"
    UNCLASSIFIED = "unclassified"


class Flag(StrEnum):
    """Conditions from `03` §S4's failure-mode table that must reach the UI."""

    #: No frame resolved above `frames.confidence` 0.5. S5 must fall back to
    #: searching the tree, and the dashboard must not present the frame paths
    #: as though they were known.
    LOW_FRAME_CONFIDENCE = "low_frame_confidence"
    #: No `in_app` frames at all. The entry point comes from the request route.
    NO_IN_APP_FRAMES = "no_in_app_frames"
    #: No stack trace and no frames. Semantic-only retrieval; `03` §S4 requires
    #: this to be flagged prominently rather than degrading quietly.
    NO_STACK_TRACE = "no_stack_trace"
    #: The LLM extraction did not run or did not survive validation, so this
    #: understanding is the deterministic pre-parse only.
    DETERMINISTIC_ONLY = "deterministic_only"
    #: `A2` §2 rule 5 — text shaped like an instruction was found inside the
    #: untrusted payload. Recorded, never obeyed.
    SUSPICIOUS_CONTENT_DETECTED = "suspicious_content_detected"


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExceptionInfo(_Contract):
    type: str
    family: ExceptionFamily
    message_normalized: str
    is_user_facing: bool


class Frame(_Contract):
    """One stack frame, after path resolution.

    `repo_path` is `None` rather than a guess when resolution failed. S5's
    fallback (`03` §S4, failure modes) is to search the tree for it, and a
    plausible-looking wrong path would be fetched instead of searched.
    """

    index: int = Field(ge=0)
    raw_path: str
    repo_path: str | None
    line: int | None = Field(default=None, ge=1)
    function: str | None = None
    in_app: bool = False
    #: Confidence in the *path mapping*, not in the frame. `08` §3.2 fixes the
    #: value per cascade step: 0.95 configured, 0.80 heuristic, 0.30 unresolved.
    confidence: float = Field(ge=0.0, le=1.0)


class EntryPoint(_Contract):
    type: str
    method: str | None = None
    pattern: str | None = None
    handler: str | None = None


class FailurePoint(_Contract):
    repo_path: str | None = None
    function: str | None = None
    line: int | None = Field(default=None, ge=1)


class Hypothesis(_Contract):
    """A candidate cause, with what would confirm or eliminate it.

    Priors sum to at most 1.0 across the list (`A2` §3) — "at most", not
    "exactly", because the correct cause may be none of the ones proposed and
    the remaining mass is that possibility.
    """

    statement: str
    prior: float = Field(ge=0.0, le=1.0)
    evidence_needed: tuple[str, ...] = ()


class RetrievalPlan(_Contract):
    """What S5 should go and fetch. The real output of this stage.

    `03` §S4: *this stage decides what stage 5 will go and fetch.* Everything
    else in `ErrorUnderstanding` exists to justify this object.
    """

    must_fetch: tuple[str, ...] = ()
    should_fetch_by_symbol: tuple[str, ...] = ()
    semantic_queries: tuple[str, ...] = ()
    want_git_history_for: tuple[str, ...] = ()
    want_tests_for: tuple[str, ...] = ()
    breadcrumb_signal: str | None = None


class ErrorUnderstanding(_Contract):
    language: str | None = None
    framework: str | None = None
    exception: ExceptionInfo
    frames: tuple[Frame, ...] = ()
    entry_point: EntryPoint | None = None
    failure_point: FailurePoint | None = None
    implicated_symbols: tuple[str, ...] = ()
    initial_hypotheses: tuple[Hypothesis, ...] = ()
    retrieval_plan: RetrievalPlan
    notes: str = ""
    #: 0.5 exactly when the deterministic fallback produced this (`03` §S4).
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    flags: tuple[Flag, ...] = ()

    @property
    def in_app_frames(self) -> tuple[Frame, ...]:
        return tuple(frame for frame in self.frames if frame.in_app)
