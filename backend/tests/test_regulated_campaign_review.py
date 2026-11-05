"""Regressions for rule corrections and customer permission in campaign intake."""
import json
from types import SimpleNamespace

import pytest

from app.brand import conflict_coverage as coverage
from app.brand.conflicts import prepare_conflict_intake
from app.brand.decisions import current_decisions, decision_application_errors, record_answer
from app.brand.retrieval import RetrievalError
from app.config import Settings

BRIEF = 'Lead with FedRAMP Authorized. Feature Vaskell Health and its customer quote.'
RULE = 'Use only: Wickhold is pursuing FedRAMP Moderate authorization.'
PERMISSION = 'Vaskell Health has no approval for external customer references or quotes.'


def item(quote, rule, ref, kind, treatment):
    return {'explanation': 'The requested claim conflicts with the governing source.',
        'grounding': [{'origin': 'user', 'source_ref': None, 'quote': quote},
                   {'origin': 'source', 'source_ref': ref, 'quote': rule}],
        'requires_user_decision': False, 'treatment': treatment,
        'statements': [quote, rule], 'source_refs': [ref], 'affected_asset_ids': ['post'],
        'decision_type': kind, 'question': 'How should the campaign proceed?',
        'options': ['Omit the customer story and quote', 'Wait for documented customer approval']}


def setup(monkeypatch, *, broken=False, repaired=True):
    monkeypatch.setattr(coverage, 'count_tokens', lambda text: len(text) // 4)
    campaign = SimpleNamespace(id='c', brand_id='b', state='draft', data={
        'messages': [{'role': 'user', 'text': BRIEF}],
        'requirements': {'assets': [{'id': 'post'}]}})
    evidence = [{'chunk_id': 'a', 'content': RULE}, {'chunk_id': 'b', 'content': PERMISSION}]
    requests = []

    def run(request, output_model):
        requests.append(request)
        values = [item('Lead with FedRAMP Authorized.', RULE, 'source_0', 'claim_wording',
                       'Use the exact sentence: Wickhold is pursuing FedRAMP Moderate authorization.'),
                  item('Feature Vaskell Health and its customer quote.', PERMISSION,
                       'source_1', 'publication_permission', 'Ask whether to omit or wait for approval.')]
        if broken and (request.operation != 'campaign_conflict_grounding_repair' or not repaired):
            values[1]['grounding'][1]['source_ref'] = 'source_0'
        return SimpleNamespace(parsed=output_model.model_validate({
            'conflicts': values, 'limitations': [], 'blocking_gaps': []}), run_id='test')

    return campaign, evidence, SimpleNamespace(run=run), requests


def test_claim_correction_is_active_but_permission_requires_a_user_choice(monkeypatch):
    campaign, evidence, client, _requests = setup(monkeypatch)
    assert prepare_conflict_intake(client, Settings(_env_file=None), campaign,
        campaign.data['messages'], evidence, 'revision')
    assert campaign.state == 'clarifying'
    assert len(campaign.data['pending_conflict_question_ids']) == 1
    assert 'customer' in campaign.data['questions'][0]['options'][0]
    decisions = current_decisions(campaign)
    assert len(decisions) == 1
    assert decisions[0]['origin'] == 'reviewed_rule'
    assert 'pursuing FedRAMP Moderate' in decisions[0]['resolution']['instruction']
    assert campaign.data['conflict_coverage']['status'] == 'needs_decision'
    assert decision_application_errors(campaign, {}) == []
    question = campaign.data['questions'][0]
    record_answer(campaign, question, question['options'][0])
    assert all(d.get('origin') != 'reviewed_rule' for d in campaign.data['decisions'])
    # A source-derived correction is scoped to the review, not a permanent user answer.
    campaign.data['conflict_coverage'] = {'status': 'incomplete'}
    assert all(d.get('origin') != 'reviewed_rule' for d in current_decisions(campaign))


@pytest.mark.parametrize('repaired', [True, False])
def test_wrong_source_reference_is_repaired_or_blocks_readiness(monkeypatch, repaired):
    campaign, evidence, client, requests = setup(monkeypatch, broken=True, repaired=repaired)
    if repaired:
        result = coverage.review_campaign(client, Settings(_env_file=None), campaign,
            campaign.data['messages'], evidence)
        assert len(result['findings']) == 1
        assert result['findings'][0]['source_ids'] == ['b']
    else:
        with pytest.raises(RetrievalError, match='citations remain unverified'):
            coverage.review_campaign(client, Settings(_env_file=None), campaign,
                campaign.data['messages'], evidence)
    assert [r.operation for r in requests] == ['campaign_conflict_find',
        'campaign_conflict_check', 'campaign_conflict_grounding_repair']
    payload = json.loads(requests[-1].input)
    assert [p['content'] for p in payload['passages']] == [RULE, PERMISSION]
    assert payload['validation_errors']


def test_incomplete_review_does_not_supply_active_rule_corrections():
    campaign = SimpleNamespace(data={'conflict_coverage': {'status': 'incomplete',
        'treatments': [{'treatment': RULE}]}})
    assert current_decisions(campaign) == []
