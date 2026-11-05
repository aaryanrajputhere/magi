"""Citation contracts used by candidate audits."""
import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


class Finding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    explanation: str = Field(max_length=1200)
    statements: list[Annotated[str, Field(max_length=800)]] = Field(min_length=2, max_length=4)
    source_ids: list[str]
    affected_asset_ids: list[str]
    question: str = Field(max_length=800)
    options: list[Annotated[str, Field(max_length=400)]] = Field(min_length=2, max_length=5)


class Findings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    conflicts: list[Finding]


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_id: str
    status: Literal['not_applicable', 'consistent', 'conflict']
    reason: str = Field(max_length=240)
    conflict_indices: list[int]


def coverage_schema(evidence, assessment):
    entry = create_model('SourceCoverageEntry', __config__=ConfigDict(extra='forbid'),
        **{name: (field.annotation, field) for name, field in assessment.model_fields.items()
           if name != 'source_id'})
    return create_model('RequiredSourceCoverage', __config__=ConfigDict(extra='forbid'),
                        **{f'source_{i}': (entry, ...) for i in range(len(evidence))})


def source_assessments(parsed, evidence, assessment):
    checks = getattr(parsed, 'source_checks', None)
    if checks is None:
        return []
    values = checks.model_dump()
    return [assessment(source_id=e['chunk_id'], **values[f'source_{i}'])
            for i, e in enumerate(evidence)]


def findings_schema(evidence):
    """Limit model citations to this retrieval's chunk IDs, not document/message IDs."""
    ids = tuple(dict.fromkeys(item['chunk_id'] for item in evidence))
    references = list[Literal[ids]] if ids else list[str]
    finding = create_model('ReferencedFinding', __base__=Finding,
        source_ids=(references, Field(..., **({} if ids else {'max_length': 0}))))
    return create_model('ReferencedFindings', __base__=Findings,
                        conflicts=(list[finding], ...),
                        source_checks=(coverage_schema(evidence, EvidenceAssessment), ...))


def normalize_findings(result, evidence):
    """Keep questions available even when an older response has unusable citations."""
    allowed = {e['chunk_id'] for e in evidence}
    findings = []
    for item in result.conflicts:
        value = item.model_dump()
        rejected = sorted(set(value['source_ids']) - allowed)
        value['source_ids'] = list(dict.fromkeys(i for i in value['source_ids'] if i in allowed))
        identity = hashlib.sha256(json.dumps([value['statements'], value['source_ids']], sort_keys=True).encode()).hexdigest()[:24]
        findings.append(dict(value, id=identity, status='unresolved',
                             unverified_source_ids=rejected))
    return findings


