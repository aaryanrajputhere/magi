"""Candidate contracts shared by extraction and read-only inspection."""

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EXTRACTOR_VERSION = "policy-candidates-v2"
PROMPT_VERSION = "policy-prompt-v2"
MAX_DOCUMENT_CHARS = 24_000
MAX_OUTPUT_TOKENS = 6_000


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PolicyScope(ClosedModel):
    subjects: list[str] | None
    channels: list[str] | None
    audiences: list[str] | None
    regions: list[str] | None
    applicable_dates: list[str] | None


class EvidenceQuote(ClosedModel):
    quotation: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)


class PolicyCandidate(ClosedModel):
    rule_type: Literal[
        "prohibition", "permission", "required_wording", "qualifier", "approval_requirement"
    ]
    description: str = Field(min_length=1)
    exact_wording: str | None
    scope: PolicyScope
    conditions: list[str] | None
    exceptions: list[str] | None
    evidence: list[EvidenceQuote] = Field(min_length=1)
    status: Literal["candidate"]


class PolicyBatch(ClosedModel):
    candidates: list[PolicyCandidate]


class GroundedEvidence(EvidenceQuote):
    match_method: Literal["exact", "boundary_ellipsis"] = "exact"
    model_quotation: str | None = None
    start_offset: int
    end_offset: int


class GroundedCandidate(PolicyCandidate):
    id: str
    evidence: list[GroundedEvidence]


SCHEMA_HASH = hashlib.sha256(
    json.dumps(PolicyBatch.model_json_schema(), sort_keys=True).encode()
).hexdigest()


def extraction_current(batch, document_hash: str) -> bool:
    return (
        batch.source_hash == document_hash
        and batch.extractor_version == EXTRACTOR_VERSION
        and batch.prompt_version == PROMPT_VERSION
        and batch.schema_hash == SCHEMA_HASH
        and batch.output_limit == MAX_OUTPUT_TOKENS
    )
