from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.api.campaigns import PlanOutline
from app.brand.evidence_scope import focused_rules, select_stage
from app.brand.plan_checks import mechanical_findings
from app.brand.retrieval import RetrievalError
from app.llm.client import strict_output_schema
from app.llm.prompt_payloads import shared_rule_context, stable_source_ref
from app.research import research_needs


def passage(key, heading, content='Exact original.', **kwargs):
    return {'chunk_id': key, 'document_id': 'doc', 'relative_path': 'brand/style/rules.md',
            'heading_path': ['Rules', heading], 'content': content, **kwargs}


def test_stage_defers_only_known_execution_sections_and_restores_dependencies():
    evidence = [passage('grammar', 'Grammar and punctuation'),
                passage('claim', 'Approved claims', 'Claim with exact qualifier.', dependency_ids=['grammar']),
                passage('unknown', 'Something unfamiliar'),
                passage('format', 'Typography'),
                passage('mixed', 'Grammar and punctuation', 'Claim exception: preserve this.')]
    before = deepcopy(evidence)
    ids, audit = select_stage(evidence, {'blog'})
    assert ids == {'grammar', 'claim', 'unknown', 'mixed'}
    assert audit['deferred_ids'] == ['format']
    assert audit['reasons']['grammar'] == 'retained: explicit source dependency'
    assert evidence == before
    assert select_stage(evidence, {'blog'}, defer_execution=False)[0] == {p['chunk_id'] for p in evidence}


def test_channel_scope_preserves_authority_counterparts_and_unknown_ownership():
    evidence = [passage('email', 'Email', relative_path='brand/channels/email-playbook.md'),
                passage('old', 'Older claim', document_id='older'),
                passage('new', 'New claim', document_id='newer'), passage('unknown', 'Unknown')]
    authority = {'groups': [{'document_ids': ['older'], 'channels': ['email']}],
                 'supersessions': [{'older_id': 'older', 'newer_id': 'newer'}]}
    ids, audit = select_stage(evidence, {'blog'}, authority=authority)
    assert ids == {'old', 'new', 'unknown'}
    assert audit['deferred_ids'] == ['email']


def test_focused_confirmation_keeps_definitions_exceptions_and_dependencies_not_whole_document():
    evidence = [passage('claim', 'Claims', 'Exact approved wording.', dependency_ids=['other']),
        passage('exception', 'Exceptions', 'Exact exception.'), passage('definition', 'Definitions'),
        passage('sibling', 'Claims', 'A sibling qualification.'), passage('grammar', 'Punctuation'),
        passage('other', 'Other', document_id='different')]
    packs = {'blog': {'format': 'blog', 'evidence': evidence, 'limits': []}}
    before = deepcopy(packs)
    focused, reason = focused_rules(packs, {'claim'})
    assert {p['chunk_id'] for p in focused['blog']['evidence']} == {'claim', 'sibling', 'exception', 'definition', 'other'}
    assert reason.startswith('focused:')
    assert packs == before
    assert focused_rules(packs, set())[1].startswith('expanded:')
    with pytest.raises(RetrievalError, match='dependency unavailable'):
        focused_rules({'blog': {'evidence': [evidence[0]]}}, {'claim'})


def test_stable_rule_references_do_not_change_when_packet_is_narrowed():
    a, b = passage('immutable-a', 'Claims'), passage('immutable-b', 'Definitions')
    for evidence in ([a, b], [b]):
        packet = shared_rule_context({'blog': {'evidence': evidence}}, stable_refs=True)
        assert all(p['ref'] == stable_source_ref(p['chunk_id']) for p in packet['evidence'])
        assert stable_source_ref('immutable-b') in packet['source_groups'][packet['channels']['blog']['evidence_group']]


@pytest.mark.parametrize('kind,destination', [('on_page_form', None), (None, 'On-page demo request form.')])
def test_on_page_demo_form_is_not_an_invented_url(kind, destination):
    asset = {'id': 'page', 'format': 'landing_page', 'audience': 'Leads', 'purpose': 'Explain',
        'hook': 'Hello', 'angle': 'Evidence', 'cta': 'Book a demo', 'timing': None,
        'cta_destination_kind': kind, 'cta_destination': destination}
    plan = {'goal': 'Awareness', 'assumptions': [], 'constraints': [], 'questions': [], 'assets': [asset]}
    assert not mechanical_findings(plan)
    strict_output_schema(PlanOutline)
    asset['format'] = 'email'
    assert any(d['severity'] == 'material' for d in mechanical_findings(plan))


def test_research_requires_an_unmet_external_fact_not_background_or_company_claim():
    campaign = SimpleNamespace(data={})
    assert not research_needs(campaign, {'unknowns': ['Could sharpen healthcare audience background.',
        'Need current FedRAMP authorization status.', 'Optional industry statistic would help.']})
    need = 'Need a current third-party industry statistic for the introduction.'
    assert research_needs(campaign, {'unknowns': [need]})[0]['question'] == need
    campaign.data['research_needs'] = [{'status': 'missing', 'kind': 'external_fact',
        'question': 'What is the industry incident frequency?', 'intended_use': 'Required opening figure.'}]
    assert research_needs(campaign, {})


def test_research_gate_precedes_old_cache_and_makes_no_provider_calls(tmp_path):
    import hashlib
    import json
    import time

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.config import Settings
    from app.db import Base
    from app.research import ResearchRun, research
    requirements = {'unknowns': []}
    engine = create_engine(f'sqlite:///{tmp_path / "research.db"}')
    Base.metadata.create_all(engine)
    campaign = SimpleNamespace(id='c', data={})
    with Session(engine) as session:
        session.add(ResearchRun(id='old', campaign_id='c', data={
            'fingerprint': hashlib.sha256(json.dumps(requirements, sort_keys=True).encode()).hexdigest(),
            'created_at': time.time(), 'status': 'completed',
            'sources': [{'content': 'Unused background'}]}))
        session.commit()
        result = research(session, campaign, requirements, None, Settings(_env_file=None))
        assert result['status'] == 'skipped'
        assert result['sources'] == []
        assert result['estimated_credits'] == 0
        assert research(session, campaign, requirements, None, Settings(_env_file=None)) == result
    engine.dispose()
