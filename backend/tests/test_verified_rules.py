import copy
import json
from types import SimpleNamespace

import pytest

from app.brand import draft_validation as validation
from app.brand.retrieval import RetrievalError
from app.brand.rule_pack import Assignment, Limit, validate_assignments, validate_limits
from app.llm.context import RunContext


def pack(field='body', unit='characters', minimum=None, maximum=240):
    return {'version': 'original_rules_v1', 'limits': [{'field': field, 'unit': unit,
        'minimum': minimum, 'maximum': maximum}], 'evidence': [{'chunk_id': 'rule',
        'content': 'Use current naming. https://example.test/resource'}]}


@pytest.mark.parametrize('field,unit,n,lo,hi', [('body', 'characters', 309, None, 240),
    ('body', 'characters', 651, None, 240), ('body', 'words', 357, 900, 1400),
    ('subject', 'characters', 53, None, 48)])
def test_actual_campaign_limit_failures(field, unit, n, lo, hi):
    text = 'x' * n if unit == 'characters' else ' '.join(['word'] * n)
    draft = {'body': '', 'source_ids': ['rule'], field: text}
    assert validation.mechanical(draft, pack(field, unit, lo, hi), [])


def test_channel_assignment_rejects_linkedin_for_x():
    doc = SimpleNamespace(title='LinkedIn', raw_text='LinkedIn posts have short paragraphs.')
    with pytest.raises(RetrievalError, match='channel scope'):
        validate_assignments([Assignment(role='x', document_id='d', scope_quote=doc.raw_text)], {'d': doc}, ['x'])
    with pytest.raises(RetrievalError, match='Missing governing'):
        validate_assignments([], {}, ['claims'])


@pytest.mark.parametrize('failure', ['wrong_document', 'paraphrase', 'missing_role', 'wrong_channel'])
def test_document_selection_repairs_and_revalidates(failure):
    import json

    from app.brand.rule_pack import Selection, repair_selection
    documents = {
        'blog': SimpleNamespace(title='Blog', raw_text='Every blog post has one hero.'),
        'web': SimpleNamespace(title='Landing page', raw_text='Hero image: photo of engineers.'),
        'x': SimpleNamespace(title='X', raw_text='X posts use short sentences.'),
    }
    role = 'x' if failure == 'wrong_channel' else 'imagery'
    valid = Assignment(role=role, document_id='x' if role == 'x' else 'blog',
                       scope_quote=documents['x' if role == 'x' else 'blog'].raw_text)
    bad = {
        'wrong_document': [valid.model_copy(update={'document_id': 'web'})],
        'paraphrase': [valid.model_copy(update={'scope_quote': 'Blog posts need a hero.'})],
        'missing_role': [],
        'wrong_channel': [Assignment(role='x', document_id='blog', scope_quote=documents['blog'].raw_text)],
    }[failure]
    calls = []
    class Client:
        def run(self, request, output_model):
            calls.append(request)
            payload = json.loads(request.input)
            assert payload['previous'] == Selection(assignments=bad).model_dump()
            if failure == 'wrong_document':
                assert {'document_id': 'blog', 'scope_quote': valid.scope_quote} in payload['exact_quote_matches']
            return SimpleNamespace(parsed=Selection(assignments=[valid]))
    result = repair_selection(Selection(assignments=bad), documents, [role], Client(),
        SimpleNamespace(magi_conflict_review_model='test'), RunContext(), [], {})
    assert result.assignments == [valid]
    assert len(calls) == 1
    assert calls[0].operation == 'campaign_rule_documents_repair'


def test_document_selection_repair_is_bounded_and_fails_closed():
    from app.brand.rule_pack import Selection, repair_selection
    calls = []
    class Client:
        def run(self, request, output_model):
            calls.append(request)
            return SimpleNamespace(parsed=Selection(assignments=[]))
    with pytest.raises(RetrievalError, match='Missing governing evidence'):
        repair_selection(Selection(assignments=[]), {}, ['claims'], Client(),
            SimpleNamespace(magi_conflict_review_model='test'), RunContext(), [], {})
    assert len(calls) == 2


def test_valid_document_selection_needs_no_repair():
    from app.brand.rule_pack import Selection, repair_selection
    selection = Selection(assignments=[Assignment(role='claims', document_id='claims',
                                                 scope_quote='Approved claims')])
    assert repair_selection(selection, {'claims': SimpleNamespace(raw_text='Approved claims')},
                            ['claims'], None, None, None, [], {}) is selection


def test_rule_packs_continue_after_citation_repair_and_cache_verified_result(tmp_path):
    from sqlalchemy.orm import Session

    from app.brand import rule_pack
    from app.brand.authority import document_manifest
    from app.brand.chunking import build_chunks
    from app.db import create_database_engine, initialize_database
    from app.models import AuthorityVersion, Brand, Document

    engine = create_database_engine(f'sqlite:///{tmp_path / "rules.db"}')
    initialize_database(engine)
    with Session(engine) as session:
        session.add(Brand(id='b', name='Brand', source_directory='/tmp'))
        session.commit()
        session.add(Document(id='d', brand_id='b', relative_path='blog.md', title='Blog rules',
                             content_hash='hash', raw_text='Blog posts use approved brand imagery and voice.'))
        session.commit()
        _, manifest = document_manifest(session, 'b')
        session.add(AuthorityVersion(id='a', brand_id='b', version=1, status='confirmed',
            payload={'groups': [], 'supersessions': []}, source_hashes={'d': 'hash'},
            manifest_hash=manifest))
        session.commit()
    build_chunks(engine, 'b')
    assignments = [Assignment(role=role, document_id='d',
                              scope_quote='Blog posts use approved brand imagery and voice.')
                   for role in ['brand', 'claims', 'voice', 'imagery', 'blog']]
    calls = []
    class Client:
        def run(self, request, output_model):
            calls.append(request.operation)
            if request.operation == 'campaign_rule_limits':
                return SimpleNamespace(parsed=rule_pack.Limits(limits=[]))
            selected = [a.model_copy(deep=True) for a in assignments]
            if request.operation == 'campaign_rule_documents':
                selected[-1].document_id = 'wrong'
            return SimpleNamespace(parsed=rule_pack.Selection(assignments=selected))
    settings = SimpleNamespace(magi_conflict_review_model='test')
    packs = rule_pack.build_rule_packs(engine, 'b', ['blog'], Client(), settings, 'c')
    assert packs['blog']['coverage'] == 'complete'
    assert {a['document_id'] for a in packs['blog']['assignments']} == {'d'}
    assert packs['blog']['evidence']
    assert calls == ['campaign_rule_documents', 'campaign_rule_documents_repair', 'campaign_rule_limits']
    assert rule_pack.build_rule_packs(engine, 'b', ['blog'], None, settings, 'c') == packs


def test_limits_require_verbatim_numbers_and_units():
    evidence = [{'chunk_id': 's', 'content': 'At most 240 characters.'}]
    limit = Limit(field='body', unit='characters', minimum=None, maximum=240,
                  chunk_id='s', quote='At most 240 characters.')
    validate_limits([limit], evidence)
    limit.maximum = 280
    with pytest.raises(RetrievalError):
        validate_limits([limit], evidence)


def test_urls_control_characters_and_content_receipt():
    draft = {'body': 'Bad\x00 [go]() https://invented.test', 'source_ids': []}
    assert len(validation.mechanical(draft, pack(), [])) == 3
    old = validation.content_hash(draft)
    draft['body'] = 'Changed'
    assert validation.content_hash(draft) != old


@pytest.mark.parametrize('repaired_ok', [True, False])
def test_repair_rechecked_and_failed_copy_is_not_complete(monkeypatch, repaired_ok):
    calls = []
    def check(*args):
        calls.append(args[4]['body'])
        return ([] if repaired_ok and len(calls) == 2 else ['Use current naming']), 'review'
    monkeypatch.setattr(validation, 'check', check)
    class Client:
        def run(self, request, output_model):
            payload = {k: v for message in request.input for k, v in json.loads(message['content']).items()}
            assert payload['brand_preferences'] == [{'id': 'm', 'text': 'Short paragraphs', 'revision': 2}]
            return SimpleNamespace(parsed=SimpleNamespace(model_dump=lambda: {'body': 'Corrected'}))
    campaign = SimpleNamespace(id='c', brand_id='b', data={'messages': []})
    draft = {'body': 'Old', 'source_ids': ['rule']}
    original = copy.deepcopy(draft)
    result = validation.validate_and_repair(Client(), SimpleNamespace(magi_retrieval_planner_model='test'),
        campaign, {'id': 'a'}, draft, {'rule_pack': pack(), 'evidence': pack()['evidence'],
            'brand_preferences': [{'id': 'm', 'text': 'Short paragraphs', 'revision': 2}]})
    assert result['status'] == ('completed' if repaired_ok else 'needs_review')
    assert calls == ['Old', 'Corrected']
    assert draft == original


def test_reviewer_failure_never_passes(monkeypatch):
    def fail(*args):
        raise RuntimeError('offline')
    monkeypatch.setattr(validation, 'check', fail)
    result = validation.validate_and_repair(None, None, SimpleNamespace(data={}), {},
        {'body': 'text'}, {'rule_pack': pack()})
    assert result['status'] == 'needs_review'


def test_all_new_provider_schemas_are_strict():
    from app.brand.campaign_requirements import CampaignRequirements
    from app.brand.draft_validation import Review
    from app.brand.rule_pack import Limits, Selection
    from app.llm.client import strict_output_schema
    for model in [CampaignRequirements, Review, Limits, Selection]:
        strict_output_schema(model)


def test_limit_quote_cannot_join_two_source_passages():
    evidence = [{'chunk_id': 'first', 'content': 'At most 240 characters.'},
                {'chunk_id': 'second', 'content': 'Threads have four posts at most.'}]
    limit = Limit(field='body', unit='characters', minimum=None, maximum=240,
        chunk_id='second', quote='At most 240 characters. Threads have four posts at most.')
    with pytest.raises(RetrievalError, match='exact source quotation'):
        validate_limits([limit], evidence)
    limit.chunk_id, limit.quote = 'first', 'At most 240 characters.'
    validate_limits([limit], evidence)


def test_mechanical_failure_repairs_even_when_reviewer_is_unavailable(monkeypatch):
    calls = []
    def fail(*args):
        raise RuntimeError('offline')
    monkeypatch.setattr(validation, 'check', fail)
    class Client:
        def run(self, request, output_model):
            calls.append(request)
            return SimpleNamespace(parsed=SimpleNamespace(model_dump=lambda: {'body': 'Short'}))
    result = validation.validate_and_repair(Client(), SimpleNamespace(magi_retrieval_planner_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={}), {'id': 'a'},
        {'body': 'x' * 309, 'source_ids': []}, {'rule_pack': pack(), 'evidence': pack()['evidence']})
    assert len(calls) == 1
    assert result['body'] == 'Short'
    assert result['status'] == 'needs_review'
    assert '309 characters' in result['validation']['history'][0]['findings'][0]
    assert all('309 characters' not in f for f in result['validation']['findings'])


def test_bad_finding_does_not_discard_valid_findings_and_image_record_is_supplied():
    import json
    image = {'id': 'photo', 'usage_rights': 'owned', 'restrictions': ''}
    class Client:
        def run(self, request, output_model):
            assert json.loads(request.input[-1]['content'])['image_record'] == image
            return SimpleNamespace(run_id='review', parsed=validation.Review(findings=[
                validation.Finding(draft_quote='Old', rule_chunk_id='rule',
                    rule_quote='Use current naming.', correction='Correct the name'),
                validation.Finding(draft_quote='Invented', rule_chunk_id='rule',
                    rule_quote='Invented rule', correction='Invalid correction')]))
    errors, _ = validation.check(Client(), SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c', brand_id='b', data={}), {'id': 'a'},
        {'body': 'Old', 'source_ids': ['rule']}, {'rule_pack': pack(), 'image_record': image})
    assert 'Correct the name' in errors
    assert 'Invalid correction' not in errors
    assert any(f.startswith('Review incomplete:') for f in errors)


def test_section_budgets_do_not_limit_entire_page_in_cached_packs():
    quote = '| Section | Budget (words) | Notes |\n| Hero | 40 | Short |'
    limit = Limit(field='page', unit='words', minimum=None, maximum=40,
                  chunk_id='rule', quote=quote)
    with pytest.raises(RetrievalError, match='Section budget'):
        validate_limits([limit], [{'chunk_id': 'rule', 'content': quote}])
    cached = pack('page', 'words', None, 40)
    cached['limits'][0]['quote'] = quote
    assert validation.mechanical({'title': 'Headline', 'body': 'word ' * 100}, cached, []) == []
    total = quote + '\n| **Total** | **480** | Slack |'
    limit.quote, limit.maximum = total, 480
    validate_limits([limit], [{'chunk_id': 'rule', 'content': total}])
