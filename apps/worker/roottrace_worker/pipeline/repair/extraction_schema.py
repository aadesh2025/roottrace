"""The model's own JSON reply shape for S9 — deliberately one field.
`strategy`/`reroute_to_stage` are never asked of the model at all
(`routing.py` already knows them deterministically from `03` §S9's fixed
table); asking anyway would let a model's guess override a fact this
stage already has, which `03` §S9's routing table never leaves open to
interpretation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RepairReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction_delta: str = Field(min_length=1)
