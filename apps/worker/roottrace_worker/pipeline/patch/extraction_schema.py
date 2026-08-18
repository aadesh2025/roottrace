"""The model-facing JSON shape for S7 (`03` §S7, T5.4). Loose (`extra="ignore"`)
— parsed straight from the model's reply, trusted for nothing until
`validate.py` has run every deterministic check `03` §S7's constraint table
names. Fields the model is never asked to produce at all —
`patch_id`, `base_commit`, `files_changed`, `scope_warning`, `model`,
`prompt_version`, `tokens` — have no place here; see `contracts.py`'s
module docstring for why."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from roottrace_worker.pipeline.patch.contracts import RegressionExpectation, RiskLevel


class _LooseModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class PatchRegressionTest(_LooseModel):
    repo_path: str = ""
    test_name: str = ""
    reproduces_original_error: bool = True
    expected_before_patch: RegressionExpectation = "fail"
    expected_after_patch: RegressionExpectation = "pass"


class PatchRiskAssessment(_LooseModel):
    level: RiskLevel = "medium"
    breaking_change: bool = False
    breaking_change_note: str | None = None
    touches_auth: bool = False
    touches_data_migration: bool = False
    touches_public_api: bool = False


class PatchAlternative(_LooseModel):
    approach: str = ""
    rejected_because: str = ""


class PatchReply(_LooseModel):
    diff: str
    explanation: str = ""
    regression_test: PatchRegressionTest | None = None
    risk_assessment: PatchRiskAssessment
    alternatives_considered: tuple[PatchAlternative, ...] = ()
    suspicious_content_detected: bool = False
