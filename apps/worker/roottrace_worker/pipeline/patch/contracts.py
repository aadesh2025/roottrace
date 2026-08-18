"""S7 `patch` (`03` §S7) — T5.4. Trusted, post-validation output contract.

Same two-model split established at T5.3: this module is the frozen,
`extra="forbid"` shape a caller can trust; `extraction_schema.py` is the loose
shape the model's raw JSON is checked against. `patch_id`, `base_commit`,
`files_changed`, `scope_warning`, `model`, `prompt_version`, and `tokens` all
have no field on `PatchReply` at all — every one of them is either assigned by
our own code (`patch_id`, `base_commit`) or computed deterministically from the
parsed diff (`files_changed`, `scope_warning`) or the real `LLMResult`
(`model`, `prompt_version`, `tokens`), never trusted from the model's own
JSON, extending T5.3's `model`/`prompt_version`/`tokens` rule to every field
here that our own code can independently know or compute."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RiskLevel = Literal["low", "medium", "high"]
RegressionExpectation = Literal["fail", "pass"]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FileChange(_Contract):
    repo_path: str
    additions: int = Field(ge=0)
    deletions: int = Field(ge=0)
    hunks: int = Field(ge=0)
    is_new_test: bool = False


class RegressionTest(_Contract):
    repo_path: str
    test_name: str
    reproduces_original_error: bool
    expected_before_patch: RegressionExpectation
    expected_after_patch: RegressionExpectation


class RiskAssessment(_Contract):
    level: RiskLevel
    breaking_change: bool = False
    breaking_change_note: str | None = None
    touches_auth: bool = False
    touches_data_migration: bool = False
    touches_public_api: bool = False


class Alternative(_Contract):
    approach: str
    rejected_because: str


class TokenUsage(_Contract):
    prompt: int = 0
    completion: int = 0


class Patch(_Contract):
    patch_id: str
    base_commit: str
    diff: str
    files_changed: tuple[FileChange, ...]
    explanation: str
    regression_test: RegressionTest | None = None
    risk_assessment: RiskAssessment
    alternatives_considered: tuple[Alternative, ...] = ()
    scope_warning: str | None = None
    model: str
    prompt_version: str
    tokens: TokenUsage = Field(default_factory=TokenUsage)
