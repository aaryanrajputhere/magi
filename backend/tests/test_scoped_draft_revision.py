from review_helpers import review_payload
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.api import campaigns as api
from app.config import Settings
from app.db import create_database_engine, initialize_database


@pytest.mark.parametrize('escalate,undo_shortening,clarify', [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
def test_revision_scope_and_final_shortening(tmp_path, monkeypatch, escalate, undo_shortening, clarify):
    from app.brand import draft_validation
    from app.models import Brand
    engine = create_database_engine(f'sqlite:///{tmp_path}/revision.db')
    initialize_database(engine)
    source = {'chunk_id': 'rule', 'content': 'Use approved wording.', 'relative_path': 'brand/style/rules.md'}
    package = {'evidence': [source], 'rule_pack': {'version': 'v1', 'format': 'linkedin', 'limits': [], 'evidence': [source]}}
    asset = {'id': 'a', 'format': 'linkedin', 'purpose': 'Awareness'}
    draft = {'asset_id': 'a', 'format': 'linkedin', 'plan_index': 0, 'title': 'Title', 'body': 'One two three four five six.',
             'source_ids': ['rule'], 'image': {'image_id': 'keep'}, 'publication_details': {'destination_url': 'https://example.com'}}
    plan = {'review_id': 'review', 'assets': [asset]}
    monkeypatch.setattr(api, 'load_asset_package', lambda *a: deepcopy(package))
    monkeypatch.setattr(api, 'memory_context', lambda *a: [])
    monkeypatch.setattr(api, 'image_catalog', lambda *a: (None, [{'id': 'keep'}]))
    conflicts = []
    monkeypatch.setattr(api, 'review_conflicts', lambda *a: conflicts.append(True) or [])
    class Client:
        def __init__(self, *a): pass
        def close(self): pass
        def run(self, request, output_model):
            if request.operation == 'edit_saved_draft':
                # Exercise the deeper evidence-backed branch for substantive edits.
                return SimpleNamespace(run_id='scope', parsed=output_model.model_validate({
                    'title': 'Title', 'body': 'One two.', 'subject': None, 'preheader': None,
                    'sender_name': None, 'byline': None, 'requires_conflict_review': True}))
            assert request.operation == 'revise_campaign_asset'
            return SimpleNamespace(run_id='revision', parsed=output_model.model_validate({
                'title': 'Title', 'body': 'One two.', 'source_ids': ['rule'], 'subject': None,
                'preheader': None, 'sender_name': None, 'byline': None, 'requires_conflict_review': escalate,
                'clarification': {'text': 'How should we handle this claim?', 'reason': 'Approval is missing.',
                    'kind': 'choices', 'options': ['Omit it', 'Provide approved wording']} if clarify else None}))
    monkeypatch.setattr(api, 'TrackedOpenAI', Client)
    def validate(client, settings, campaign, asset, candidate, packet):
        assert not clarify, "Clarifications must never validate or save candidate prose"
        assert packet['validation_conversation'] == [{'role': 'user', 'text': 'Make the first post shorter'}]
        assert candidate['image'] == draft['image']
        assert candidate['publication_details'] == draft['publication_details']
        return {**candidate, 'body': draft['body'] if undo_shortening else candidate['body'],
                'status': 'completed', 'validation': {'status': 'passed', 'findings': []}}
    monkeypatch.setattr(draft_validation, 'validate_and_repair', validate)
    with Session(engine) as session:
        session.add(Brand(id='b', name='Brand', source_directory='/tmp'))
        session.commit()
        other = {**deepcopy(draft), 'asset_id': 'other'}
        session.add(api.Campaign(id='c', brand_id='b', state='needs_review', version=1, data={
            'messages': [{'role': 'user', 'text': 'Historical brief'}], 'plans': [plan],
            'generation': {'plan_index': 0, 'drafts': [draft, other]}}))
        session.commit()
        try:
            api.process_message('c', api.Message(text='Make the first post shorter', version=1), session,
                Settings(_env_file=None), forced_route=api.MessageRoute(route='revise', target_asset_id='a', reply=''))
        except Exception:
            if not undo_shortening: raise
        session.expire_all()
        saved = api.load(session, 'c').data['generation']['drafts']
        assert saved[0]['body'] == (draft['body'] if undo_shortening or clarify else 'One two.')
        if clarify:
            assert api.load(session, 'c').state == 'clarifying'
            assert api.load(session, 'c').data['pending_question']['options'] == ['Omit it', 'Provide approved wording']
        assert saved[0]['image'] == draft['image']
        assert saved[1] == other
        assert bool(conflicts) == escalate
    engine.dispose()


def test_revision_reuses_task_routes_and_preserves_source_dependencies():
    from app.brand.draft_revision import revision_packages, validation_context
    sources = [{'chunk_id': 'writing', 'content': 'Approved claim', 'dependency_ids': ['qualifier']},
               {'chunk_id': 'qualifier', 'content': 'Required qualification'},
               {'chunk_id': 'validation', 'content': 'Channel rule'},
               {'chunk_id': 'unrelated', 'content': 'Other asset rules'}]
    package = {'evidence': sources, 'rule_pack': {'format': 'linkedin', 'limits': [], 'evidence': sources}}
    campaign = SimpleNamespace(data={'asset_context_routing': {'run_id': 'saved', 'assets': {'a': {
        'writing': ['writing'], 'validation': ['validation'], 'image': [], 'production_dependencies': []}}},
        'decisions': [{'id': 'd', 'origin': 'user', 'answer': 'Use approved wording', 'affected_asset_ids': ['a']}]})
    original = deepcopy(package)
    writing, validation = revision_packages(campaign, {'asset_packages': {'a': package}}, 'a', package)
    assert {p['chunk_id'] for p in writing['evidence']} == {'writing', 'qualifier'}
    assert {p['chunk_id'] for p in validation['evidence']} == {'writing', 'qualifier', 'validation'}
    assert package == original
    assert validation_context(campaign, 'a', {'id': 'a'}, 'Shorten')['current_decisions'][0]['answer'] == 'Use approved wording'


def test_validation_payload_omits_history_and_preserves_original_rules():
    from app.brand.draft_validation import validation_draft, validation_pack
    draft = {'body': 'Copy', 'image': {'image_id': 'keep'}, 'validation': {'history': ['large']},
             'dependency_records': ['old'], 'publication_details': {'destination_url': 'https://example.com'}}
    assert 'validation' not in validation_draft(draft)
    assert 'dependency_records' not in validation_draft(draft)
    assert validation_draft(draft)['image'] == draft['image']
    source = {'chunk_id': 's', 'content': 'Required original wording, including its exception.',
              'heading_path': ['Rules'], 'embedding': [1]*100, 'retrieval_diagnostics': 'large'}
    packet = validation_pack({'format': 'linkedin', 'limits': [], 'evidence': [source]})
    assert packet['evidence'][0]['content'] == source['content']
    assert 'embedding' not in packet['evidence'][0]


def test_editorial_validation_stops_after_one_repair(tmp_path, monkeypatch):
    from app.brand import draft_validation as module
    from app.brand.draft_patches import Patches
    campaign = SimpleNamespace(id='c', brand_id='b', data={'messages': []})
    draft = {'title': 'Title', 'body': 'One two three.', 'source_ids': []}
    packet = {'revision_instruction': 'Shorten', 'rule_pack': {'format': 'linkedin', 'version': 'v', 'limits': [], 'evidence': []}}
    checks = []
    def check(*args):
        checks.append(True)
        return ['Conflicting placement rules need review'], 'check'
    monkeypatch.setattr(module, 'check', check)
    class Client:
        def run(self, request, output_model):
            assert request.operation == 'campaign_asset_repair'
            return SimpleNamespace(run_id='repair', parsed=Patches.model_validate({
                'content_hash': module.content_hash(draft), 'patches': [
                    {'field': 'body', 'start': 0, 'end': 3, 'original': 'One', 'replacement': 'A'}]}))
    result = module.validate_and_repair(Client(), Settings(_env_file=None), campaign, {'id': 'a'}, draft, packet)
    assert len(checks) == 2
    assert len(result['validation']['history']) == 2
    assert result['validation']['status'] == 'failed'


def test_resolved_model_dependency_does_not_survive_revalidation(monkeypatch):
    from app.brand import draft_validation as module
    from app.brand.draft_patches import Patches
    campaign = SimpleNamespace(id='c', brand_id='b', data={'messages': []})
    draft = {'title': '', 'body': 'One two three.', 'source_ids': []}
    packet = {'revision_instruction': 'Shorten', 'rule_pack': {'format': 'linkedin', 'version': 'v', 'limits': [], 'evidence': []}}
    calls = []
    def check(client, settings, campaign, asset, copy, package):
        calls.append(True)
        if len(calls) == 1:
            package['dependency_records'].append({'id': 'stale', 'kind': 'missing_evidence',
                'message': 'Unsupported sentence', 'status': 'unresolved'})
            return ['Remove unsupported sentence'], 'first'
        return [], 'second'
    monkeypatch.setattr(module, 'check', check)
    class Client:
        def run(self, request, output_model):
            return SimpleNamespace(run_id='repair', parsed=Patches.model_validate({
                'content_hash': module.content_hash(draft), 'patches': [
                    {'field': 'body', 'start': 0, 'end': 3, 'original': 'One', 'replacement': 'A'}]}))
    result = module.validate_and_repair(Client(), Settings(_env_file=None), campaign, {'id': 'a'}, draft, packet)
    assert not any(r['id'] == 'stale' for r in result['dependency_records'])
    assert result['validation']['status'] == 'passed'


def test_shortening_budget_respects_channel_minimum():
    from app.brand.draft_revision import shortening_budget
    from app.brand.retrieval import RetrievalError
    package = {'rule_pack': {'limits': [{'field': 'body', 'unit': 'words', 'minimum': 900, 'maximum': 1400}]}}
    budget = shortening_budget({'body': 'word ' * 1100}, package)
    assert budget['minimum_body_words'] == 900
    assert 900 < budget['target_body_words'] < 1100
    with pytest.raises(RetrievalError, match='cannot be shortened'):
        shortening_budget({'body': 'word ' * 900}, package)


def test_revision_source_selection_keeps_citations_limits_and_dependencies():
    from app.brand.draft_revision import focus_revision_context
    sources = [{'chunk_id': 'mandatory', 'content': 'Required exception', 'dependency_ids': ['definition']},
               {'chunk_id': 'definition', 'content': 'Definition'},
               {'chunk_id': 'cited', 'content': 'Verified fact'},
               {'chunk_id': 'limit', 'content': 'Length rule'},
               {'chunk_id': 'irrelevant', 'content': 'Optional style essay. ' * 1200}]
    packet = {'evidence': sources, 'rule_pack': {'evidence': sources, 'limits': [{'chunk_id': 'limit'}]}}
    class Client:
        def run(self, request, output_model):
            import json
            from app.llm.client import strict_output_schema
            strict_output_schema(output_model)
            payload = review_payload(request)
            ref = next(p['id'] for p in payload['sources'] if p['content'] == 'Required exception')
            return SimpleNamespace(run_id='selection', parsed=output_model.model_validate({'source_ids': [ref]}))
    campaign = SimpleNamespace(id='c', brand_id='b', data={})
    writing, validation = focus_revision_context(Client(), Settings(_env_file=None), campaign, {'id': 'a'},
        {'body': 'Copy', 'source_ids': ['cited']}, 'Shorten', packet, packet)
    assert {p['chunk_id'] for p in writing['evidence']} == {'mandatory', 'definition', 'cited', 'limit'}
    assert writing['evidence'] == validation['evidence']
    assert len(packet['evidence']) == 5


def test_near_minimum_blog_shortening_stops_before_model_calls():
    from app.brand.draft_revision import shortening_budget
    from app.brand.retrieval import RetrievalError
    packet = {'rule_pack': {'limits': [{'field': 'body', 'unit': 'words', 'minimum': 900}]}}
    with pytest.raises(RetrievalError, match='only 2 words can be removed'):
        shortening_budget({'body': 'word ' * 902}, packet)
