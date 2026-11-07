import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.brand.draft_validation import Review, validate_and_repair
from app.llm.client import strict_output_schema


def fixtures():
    source = {'chunk_id': 'cta', 'document_id': 'doc', 'content': 'A CTA has at most four words.'}
    package = {'evidence': [source], 'rule_pack': {'format': 'linkedin', 'limits': [],
                                                'version': 'v1', 'evidence': [source]}}
    draft = {'title': 'Containment', 'subject': None, 'preheader': None, 'sender_name': None,
             'byline': None, 'body': 'Read the full original report here', 'source_ids': ['cta']}
    return package, draft


def test_validation_schema_is_accepted_before_provider_request():
    schema = strict_output_schema(Review)
    assert schema
    assert 'scope' in Review.model_json_schema()['$defs']['Finding']['required']


def test_generation_result_validates_repairs_and_rechecks():
    package, draft = fixtures()
    original = deepcopy(draft)
    calls = []
    def run(request, output_model):
        strict_output_schema(output_model)
        calls.append(request.operation)
        if request.operation == 'campaign_asset_repair':
            payload = json.loads(request.input[1]['content'])
            assert payload['grounded_findings'][0]['rule_chunk_id'] == 'cta'
            value = {'content_hash': payload['draft_content_hash'], 'patches': [{'field': 'body', 'start': 0, 'end': len(draft['body']),
                                   'original': draft['body'], 'replacement': 'Read the report'}]}
        else:
            value = {'findings': [{
                'scope': 'text', 'draft_quote': draft['body'], 'rule_chunk_id': 'cta',
                'source_span': 'cta:0', 'dependency_kind': None,
                'rule_quote': 'A CTA has at most four words.', 'correction': 'Use Read the report.'
            }] if len(calls) == 1 else []}
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id=str(len(calls)))
    result = validate_and_repair(SimpleNamespace(run=run),
        SimpleNamespace(magi_conflict_review_model='test', magi_writer_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={'messages': []}),
        {'id': 'a', 'format': 'linkedin', 'purpose': 'Distinct approved purpose'}, draft, package)
    assert calls == ['campaign_asset_validation', 'campaign_asset_repair', 'campaign_asset_validation']
    assert result['status'] == 'completed'
    assert result['body'] == 'Read the report'
    assert draft == original


def test_validation_failure_remains_specific_and_unverified(caplog):
    package, draft = fixtures()
    def run(*args, **kwargs):
        raise ValueError('Structured output contract is invalid')
    result = validate_and_repair(SimpleNamespace(run=run), SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={'messages': []}), {'id': 'a'}, draft, package)
    assert result['status'] == 'needs_review'
    assert result['validation']['status'] == 'failed'
    assert any('Structured output contract is invalid' in finding for finding in result['validation']['findings'])
    assert 'Asset validation failed' in caplog.text


def test_model_cannot_fail_passing_numeric_limit_or_regenerate_source_quote():
    from app.brand.draft_validation import check
    package, draft = fixtures()
    draft['body'] = 'word ' * 935
    source = {'chunk_id': 'length', 'content': 'Blog posts run 900 to 1,400 words.'}
    package['rule_pack'].update(evidence=[source], limits=[{
        'field': 'body', 'unit': 'words', 'minimum': 900, 'maximum': 1400,
        'chunk_id': 'length', 'quote': source['content']}])
    draft['source_ids'] = ['length']
    def run(request, output_model):
        return SimpleNamespace(run_id='test', parsed=output_model.model_validate({'findings': [{
            'scope': 'text', 'dependency_kind': None, 'source_span': 'length:0',
            'draft_quote': '', 'rule_chunk_id': 'length', 'rule_quote': 'Not an original quotation',
            'correction': 'Expand to at least 900 words.'}]}))
    errors, _ = check(SimpleNamespace(run=run), SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={'messages': []}), {'id': 'a'}, draft, package)
    assert errors == []


def test_wrong_source_span_is_rechecked_once_without_repair():
    from app.brand.draft_validation import check
    package, draft = fixtures()
    draft['body'] = 'Read more'
    calls = []
    def run(request, output_model):
        calls.append(request.operation)
        # A valid span belonging to another chunk must not ground this rule ID.
        value = {'findings': [{'scope': 'text', 'dependency_kind': None, 'source_span': 'cta:0',
            'draft_quote': '', 'rule_chunk_id': 'wrong-source', 'rule_quote': 'Untrusted quotation',
            'correction': 'Change copy'}]} if len(calls) == 1 else {'findings': []}
        return SimpleNamespace(run_id='test', parsed=output_model.model_validate(value))
    errors, _ = check(SimpleNamespace(run=run), SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={'messages': []}), {'id': 'a'}, draft, package)
    assert errors == []
    assert calls == ['campaign_asset_validation', 'campaign_asset_validation']


def test_cta_maximum_is_taken_from_original_source_not_hardcoded():
    from app.brand.draft_validation import mechanical
    pack = {'limits': [], 'evidence': [{'chunk_id': 'cta', 'content':
        'A CTA, the call to action, is at most four words, and it starts with a verb.'}]}
    assert 'cta: 5 words; maximum 4' in mechanical(
        {'body': 'Visit the containment campaign page', 'source_ids': []}, pack, [])
    pack['evidence'][0]['content'] = 'A CTA is at most six words.'
    assert mechanical({'body': 'Visit the containment campaign page', 'source_ids': []}, pack, []) == []


def test_validation_boundary_and_schema_stay_stable_across_source_packets():
    from app.brand.draft_validation import check
    requests, schemas = [], []
    def run(request, output_model):
        requests.append(request)
        schemas.append(strict_output_schema(output_model))
        return SimpleNamespace(run_id='test', parsed=output_model.model_validate({'findings': []}))
    package, draft = fixtures()
    campaign = SimpleNamespace(id='c', brand_id='b', data={'messages': []})
    settings = SimpleNamespace(magi_conflict_review_model='gpt-6-luna')
    check(SimpleNamespace(run=run), settings, campaign, {'id': 'a'}, draft, package)
    changed = {**draft, 'body': 'Read more'}
    check(SimpleNamespace(run=run), settings, campaign, {'id': 'a'}, changed, package)
    assert requests[0].cache_prefix_messages == requests[1].cache_prefix_messages == 1
    assert requests[0].input[0] == requests[1].input[0]
    assert requests[0].input[1] != requests[1].input[1]
    other = deepcopy(package)
    other['rule_pack']['evidence'] = [{'chunk_id': 'other', 'content': 'Different original.'}]
    check(SimpleNamespace(run=run), settings, campaign, {'id': 'b'},
          {**changed, 'source_ids': ['other']}, other)
    assert schemas[0] == schemas[1] == schemas[2]
    assert requests[0].input[0] != requests[2].input[0]


def test_unknown_span_remains_rejected_with_fixed_schema():
    from app.brand.draft_validation import check
    package, draft = fixtures()
    def run(request, output_model):
        return SimpleNamespace(run_id='test', parsed=output_model.model_validate({'findings': [{
            'scope': 'text', 'dependency_kind': None, 'source_span': 'invented:0',
            'draft_quote': '', 'rule_chunk_id': 'cta', 'rule_quote': 'Invented',
            'correction': 'Change copy'}]}))
    errors, _ = check(SimpleNamespace(run=run), SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={'messages': []}), {'id': 'a'}, draft, package)
    assert any('ungrounded findings' in error for error in errors)


def test_compact_paragraph_aliases_preserve_exact_grounding():
    from app.brand.draft_validation import check
    package, draft = fixtures()
    package['rule_pack']['evidence'][0]['content'] = 'First paragraph.\n\n\n\nLast exact paragraph.'
    def run(request, output_model):
        prefix = json.loads(request.input[0]['content'])
        assert prefix['source_spans'] == {'cta': ['s0', 's1']}
        return SimpleNamespace(run_id='test', parsed=output_model.model_validate({'findings': [{
            'scope': 'text', 'dependency_kind': None, 'source_span': 's1',
            'draft_quote': '', 'rule_chunk_id': 'cta', 'rule_quote': 'Untrusted generated quote',
            'correction': 'Apply last paragraph'}]}))
    errors, _ = check(SimpleNamespace(run=run), SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={'messages': []}), {'id':'a'}, draft, package)
    assert errors == ['Apply last paragraph']
    assert package['structured_findings'][0]['rule_quote'] == 'Last exact paragraph.'
    assert package['structured_findings'][0]['source_span'] == 'cta:2'


def test_exhausted_repair_preserves_draft_findings_and_run(monkeypatch):
    from app.brand import draft_validation as module
    from app.llm.client import ModelRunError
    package, draft = fixtures()
    original = deepcopy(draft)
    monkeypatch.setattr(module, 'check', lambda *args: (['Fix supported qualifier'], 'review'))
    def run(request, output_model):
        assert request.max_output_tokens == 8000
        raise ModelRunError('repair-run', 'incomplete')
    result = module.validate_and_repair(SimpleNamespace(run=run),
        SimpleNamespace(magi_writer_model='test'), SimpleNamespace(id='c',brand_id='b',data={'messages':[]}),
        {'id':'a'},draft,package)
    assert result['body'] == original['body'] and draft == original
    assert result['status'] == 'needs_review'
    assert result['validation']['status'] == 'failed'
    assert result['validation']['history'][0]['repair'] == {'run_id':'repair-run','status':'incomplete'}
    assert 'Fix supported qualifier' in result['validation']['findings']


def test_long_repairs_have_headroom_and_length_targets_stay_within_limits(monkeypatch):
    from app.brand import draft_validation as module
    from app.llm.client import ModelRunError
    package, draft = fixtures()
    draft['body'] = 'supported ' * 692
    package['rule_pack']['limits']=[{'field':'body','unit':'words','minimum':900,'maximum':1400}]
    assert module.length_targets(package['rule_pack']) == [
        {'field':'body','unit':'words','minimum':900,'maximum':1400,'target':1150}]
    monkeypatch.setattr(module,'check',lambda *args:(['body: 692 words; minimum 900'],'review'))
    def run(request,output_model):
        assert request.max_output_tokens == 12000
        assert json.loads(request.input[-1]['content'])['length_targets'][0]['target'] == 1150
        assert 'Set start and end to null' in request.instructions
        raise ModelRunError('run','incomplete')
    result=module.validate_and_repair(SimpleNamespace(run=run),SimpleNamespace(magi_writer_model='test'),
        SimpleNamespace(id='c',brand_id='b',data={'messages':[]}),{'id':'a'},draft,package)
    assert result['status']=='needs_review'
    assert result['body']==draft['body']


def test_section_budgets_never_become_whole_asset_length_targets():
    from app.brand.draft_validation import length_targets
    pack = {'limits': [{'field': 'page', 'unit': 'words', 'minimum': 20, 'maximum': 40,
                       'quote': '| Section | Words |\n| Hero | 20–40 |'}]}
    assert length_targets(pack) == []


def test_asset_id_cannot_become_a_publishable_cta_url():
    from app.brand.draft_validation import check
    package,draft=fixtures()
    draft['body']='[Read more](landing-01)'
    def run(request,output_model):
        return SimpleNamespace(run_id='review',parsed=output_model.model_validate({'findings':[]}))
    errors,_=check(SimpleNamespace(run=run),SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c',brand_id='b',data={'messages':[]}),
        {'id':'a','cta_destination_kind':'asset','cta_destination':'landing-01'},draft,package)
    assert any('asset-ID link' in e for e in errors)
    assert package['dependency_records'][0]['kind']=='destination_url'


@pytest.mark.parametrize('recover',[True,False])
def test_ambiguous_patch_gets_one_bounded_correction_without_weakening_guards(monkeypatch,recover):
    from app.brand import draft_validation as module
    package,draft=fixtures()
    draft['body']='Repeated. Keep qualification. Repeated.'
    calls=[]
    def check(*args):
        return (['Correct the first occurrence'],'review') if not calls else ([], 'recheck')
    monkeypatch.setattr(module,'check',check)
    def run(request,output_model):
        payload=json.loads(request.input[-1]['content'])
        calls.append(request)
        if len(calls)==2:
            assert payload['patch_feedback']['anchors'][0]['exact_occurrences']==2
            assert calls[0].input[0]==request.input[0]
        anchor='Repeated. Keep qualification.' if len(calls)==2 and recover else 'Repeated'
        replacement='New. Keep qualification.' if len(calls)==2 and recover else 'New'
        return SimpleNamespace(run_id='repair-'+str(len(calls)),parsed=output_model.model_validate({
            'content_hash':module.content_hash(draft),'patches':[{'field':'body','start':None,'end':None,
            'original':anchor,'replacement':replacement}]}))
    result=module.validate_and_repair(SimpleNamespace(run=run),SimpleNamespace(magi_writer_model='test'),
        SimpleNamespace(id='c',brand_id='b',data={'messages':[]}),{'id':'a'},draft,package)
    assert len(calls)==2
    assert len(result['validation']['history'][0]['repair_attempts'])==2
    if recover:
        assert result['body']=='New. Keep qualification. Repeated.'
        assert result['validation']['status']=='passed'
    else:
        assert result['body']==draft['body']
        assert result['status']=='needs_review'


def test_plan_metadata_violation_blocks_publication_without_rewriting_correct_copy():
    from app.brand.draft_validation import check
    package,draft=fixtures()
    draft['body']='Read the report'
    def run(request,output_model):
        return SimpleNamespace(run_id='review',parsed=output_model.model_validate({'findings':[{
            'scope':'text','dependency_kind':None,'source_span':'s0','rule_chunk_id':'cta',
            'rule_quote':'A CTA has at most four words.','draft_quote':'Read The Report',
            'correction':'Use sentence case in the approved CTA.'}]}))
    errors,_=check(SimpleNamespace(run=run),SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c',brand_id='b',data={'messages':[]}),
        {'id':'a','cta':'Read The Report'},draft,package)
    assert errors==[]
    assert package['structured_findings']==[]
    assert package['dependency_records'][0]['message'].startswith('Approved plan metadata requires correction:')
    assert draft['body']=='Read the report'
