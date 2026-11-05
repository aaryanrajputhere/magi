import copy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from app.brand import conflict_resolver as resolver
from app.brand import conflicts


@pytest.fixture
def context():
    quote = 'Required wording: "Example Service is pursuing authorization." Never claim authorized.'
    hits = [{'chunk_id': 'legal-chunk', 'document_id': 'legal-doc', 'content': quote,
                 'start_line': 10, 'end_line': 12},
            {'chunk_id': 'old-chunk', 'document_id': 'old-doc', 'content': 'We are authorized.',
                 'start_line': 1, 'end_line': 3}]
    citation = {'document_id': 'governance', 'quotation': 'Legal governs claims.',
                    'start_line': 1, 'end_line': 1}
    authority = {'id': 'authority-v1', 'map': {'groups': [
        {'id': 'legal', 'label': 'Legal', 'priority': 1, 'document_ids': ['legal-doc'],
             'topics': ['claims', 'certification'], 'channels': ['*'], 'governing': True,
             'evidence': [citation]},
        {'id': 'sales', 'label': 'Sales', 'priority': 4, 'document_ids': ['old-doc'],
             'topics': ['*'], 'channels': ['*'], 'governing': False, 'evidence': [citation]},
    ], 'supersessions': [], 'notes': []}}
    assessment = {'disposition': 'approved_wording',
                      'approved_wording': 'Example Service is pursuing authorization.',
                      'supporting_chunk_id': 'legal-chunk', 'wording_start_line': 10, 'wording_end_line': 10, 'rule_chunk_id': 'legal-chunk', 'rule_start_line': 10, 'rule_end_line': 10,
                      'topic': 'certification', 'channels': ['email', 'linkedin'],
                      'coverage_complete': True, 'scope_clear': True, 'evidence_consistent': True,
                      'reason': 'The governing policy prescribes this wording.'}
    campaign = SimpleNamespace(id='campaign', brand_id='brand', data={'messages': []})
    settings = SimpleNamespace(magi_retrieval_planner_model='test-model')
    finding = {'id': 'issue', 'question': 'How should authorization be described?',
                   'status': 'unresolved', 'decision_type': 'claim_wording', 'source_ids': ['legal-chunk', 'old-chunk']}
    return SimpleNamespace(hits=hits, authority=authority, assessment=assessment,
                           campaign=campaign, settings=settings, finding=finding)


class Client:
    def __init__(self, assessment):
        self.assessment = assessment
        self.requests = []

    def run(self, request, *, output_model):
        self.requests.append(request)
        return SimpleNamespace(parsed=output_model.model_validate(self.assessment))


def run_wording(ctx):
    return resolver.resolve_approved_wording(
        Client(ctx.assessment), ctx.settings, ctx.campaign, ctx.finding, ctx.hits,
        ctx.authority, {'status': 'unresolved'})


def test_prescribed_wording_resolves_with_exact_chunk_citation(context):
    result = run_wording(context)
    assert result['status'] == 'resolved'
    assert result['resolution'] == context.assessment['approved_wording']
    assert result['resolution_origin'] == 'confirmed_authority'
    assert result['approved_wording_citation']['document_id'] == 'legal-doc'


@pytest.mark.parametrize('change', [
    {'disposition': 'needs_user'},
    {'coverage_complete': False},
    {'scope_clear': False},
    {'evidence_consistent': False},
    {'wording_end_line': 99},
    {'approved_wording': 'We are fully authorized.'},
    {'channels': []},
    {'channels': ['*']},
    {'topic': 'pricing'},
    {'supporting_chunk_id': 'old-chunk', 'wording_start_line': 1, 'wording_end_line': 1,
     'approved_wording': 'We are authorized.'},
])
def test_uncertain_or_ungrounded_wording_stays_unresolved(context, change):
    context.assessment.update(change)
    assert run_wording(context)['status'] == 'unresolved'


def test_unknown_authority_scope_does_not_infer_precedence(context):
    context.authority['map']['groups'][0]['channels'] = None
    client = Client(context.assessment)
    result = {'status': 'unresolved'}
    assert resolver.resolve_approved_wording(client, context.settings, context.campaign,
        context.finding, context.hits, context.authority, result) is None
    assert client.requests == []
    assert 'explicit authority channel scope' in result['approved_wording_reason']


def test_channel_restriction_and_higher_authority_block_resolution(context):
    context.authority['map']['groups'][0]['channels'] = ['email']
    assert run_wording(context)['status'] == 'unresolved'
    context.authority['map']['groups'][0]['channels'] = ['*']
    context.authority['map']['groups'][1]['priority'] = 0
    assert run_wording(context)['status'] == 'unresolved'


def test_document_id_cannot_be_used_as_chunk_citation(context):
    context.assessment['supporting_chunk_id'] = 'legal-doc'
    with pytest.raises(ValueError):
        run_wording(context)


def test_superseded_wording_source_stays_unresolved(context):
    context.authority['map']['supersessions'] = [{
        'newer_id': 'new-policy', 'older_id': 'legal-doc', 'topics': ['certification'],
        'channels': ['*'], 'evidence': context.authority['map']['groups'][0]['evidence'],
    }]
    result = run_wording(context)
    assert result['status'] == 'unresolved'
    assert 'superseded' in result['resolution_reason']


def test_invalid_model_output_preserves_question(context, monkeypatch):
    engine = create_engine('sqlite://')
    hits = [SimpleNamespace(model_dump=lambda h=h: h) for h in context.hits]
    monkeypatch.setattr(resolver, 'search_chunks', lambda *a, **kw: SimpleNamespace(results=hits))
    context.assessment['supporting_chunk_id'] = 'invented-id'
    try:
        result = resolver.resolve_question(engine, Client(context.assessment), context.settings,
                                           context.campaign, context.finding, context.authority)
        assert result['status'] == 'unresolved'
        assert result['question'] == context.finding['question']
        assert result['resolution_error_type'] == 'ValidationError'
    finally:
        engine.dispose()


def test_two_wording_conflicts_resolve_and_permission_question_remains(context, monkeypatch):
    """Exercise actual intake and resolver, including strict model schema parsing."""
    engine = create_engine('sqlite://')
    findings = [dict(context.finding, id=key, question=key, explanation=key,
                     options=['Use approved wording', 'Omit'])
                for key in ('authorization wording', 'attestation wording', 'customer permission')]
    # An extra original citation should not prevent resolving a fully covered wording question.
    for finding in findings:
        finding['source_ids'].append('extra-original-citation')
    hits = [SimpleNamespace(model_dump=lambda h=h: h) for h in context.hits]
    monkeypatch.setattr(resolver, 'search_chunks', lambda *a, **kw: SimpleNamespace(results=hits))
    monkeypatch.setattr(resolver, 'active_authority', lambda *a: context.authority)
    monkeypatch.setattr(conflicts, 'review_campaign',
                        lambda *a, **kw: {'findings': findings, 'report': {}})

    class IntakeClient(Client):
        def run(self, request, *, output_model):
            question = json.loads(request.input)['conflict']['question']
            self.assessment = copy.deepcopy(context.assessment)
            if question == 'attestation wording':
                self.assessment.update(approved_wording='Example Service holds an attestation.',
                    wording_start_line=11, wording_end_line=11)
            elif question == 'customer permission':
                self.assessment.update(disposition='needs_user',
                    reason='Choose whether to omit the story or wait for permission.')
            return super().run(request, output_model=output_model)

    context.hits[0]['content'] += '\nRequired wording: "Example Service holds an attestation."'
    try:
        assert conflicts.prepare_conflict_intake(IntakeClient(context.assessment), context.settings,
            context.campaign, [], context.hits, 'revision', engine=engine)
        saved = context.campaign.data
        assert [f['status'] for f in saved['conflicts']] == ['resolved', 'resolved', 'unresolved']
        assert len(saved['pending_conflict_question_ids']) == 1
        assert saved['questions'][0]['conflict_id'] == 'customer permission'
    finally:
        engine.dispose()


def test_separate_wording_and_rule_lines_are_copied_verbatim(context):
    context.hits[0]['content'] = 'Required wording is in the next line.\r\nExample Service is pursuing authorization.\r\n'
    context.assessment.update(wording_start_line=11, wording_end_line=11)
    result = run_wording(context)
    assert result['status'] == 'resolved'
    assert result['approved_wording_citation']['exact_quote'] == 'Example Service is pursuing authorization.\r\n'
    assert result['approval_rule_citation']['exact_quote'] == 'Required wording is in the next line.\r\n'


@pytest.mark.parametrize('decision_type', ['publication_permission', 'campaign_choice'])
def test_user_decisions_bypass_all_model_calls(context, decision_type):
    context.finding['decision_type'] = decision_type
    result = resolver.resolve_question(None, None, None, context.campaign, context.finding, None)
    assert result['status'] == 'unresolved'
    assert result['resolution_reason'] == 'This decision requires user input'


def test_invalid_citation_is_repaired_once_with_same_evidence(context):
    class RepairClient(Client):
        def run(self, request, *, output_model):
            payload = json.loads(request.input)
            assessment = copy.deepcopy(self.assessment)
            if not self.requests:
                assessment['wording_end_line'] = 999
                assert payload['citation_repair'] is None
            else:
                assert payload['citation_repair']['previous_assessment']['wording_end_line'] == 999
                assert payload['passages'][0]['numbered_lines'][0]['line'] == 10
            self.requests.append(request)
            return SimpleNamespace(parsed=output_model.model_validate(assessment))

    client = RepairClient(context.assessment)
    result = resolver.resolve_approved_wording(client, context.settings, context.campaign,
        context.finding, context.hits, context.authority, {'status': 'unresolved'})
    assert result['status'] == 'resolved'
    assert len(client.requests) == 2


def test_persistently_invalid_citation_stops_after_one_retry(context):
    context.assessment['wording_end_line'] = 999
    client = Client(context.assessment)
    result = resolver.resolve_approved_wording(client, context.settings, context.campaign,
        context.finding, context.hits, context.authority, {'status': 'unresolved'})
    assert result['status'] == 'unresolved'
    assert len(client.requests) == 2


def test_resolution_keeps_original_evidence_when_search_misses_it(context, monkeypatch):
    engine = create_engine('sqlite://')
    context.campaign.data['evidence_ledger'] = {h['chunk_id']: h for h in context.hits}
    monkeypatch.setattr(resolver, 'search_chunks', lambda *a, **kw: SimpleNamespace(results=[]))
    client = Client(context.assessment)
    try:
        result = resolver.resolve_question(engine, client, context.settings,
            context.campaign, context.finding, context.authority)
        assert result['status'] == 'resolved'
        assert set(result['resolution_source_ids']) == {'legal-chunk', 'old-chunk'}
        assert {p['chunk_id'] for p in json.loads(client.requests[0].input)['passages']} == {
            'legal-chunk', 'old-chunk'}
    finally:
        engine.dispose()
