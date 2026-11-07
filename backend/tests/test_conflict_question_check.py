"""Only grounded, incompatible requirements should become user questions."""

from types import SimpleNamespace

import pytest
from review_helpers import review_payload

from app.brand import conflict_coverage as coverage
from app.config import Settings
from app.llm.client import strict_output_schema


def finding(user_quote, rule_quote, *, requires_user_decision=True):
    return {'explanation': 'Review finding', 'statements': [user_quote, rule_quote],
        'grounding': [{'origin': 'user', 'source_ref': None, 'quote': user_quote},
                   {'origin': 'source', 'source_ref': 'source_0', 'quote': rule_quote}],
        'requires_user_decision': requires_user_decision,
        'treatment': 'Use supported descriptive wording and make a reasonable channel recommendation.',
        'source_refs': ['source_0'], 'affected_asset_ids': [], 'decision_type': 'campaign_choice',
        'question': 'Which requirement should take precedence?', 'options': ['Follow rule', 'Request exception']}


@pytest.mark.parametrize('brief,rule,proposed,expected', [
    ('Containment is what we want to be known for.', 'Unsupported market leadership claims are banned.',
     finding('Position us as the market leader.', 'Unsupported market leadership claims are banned.'), 0),
    ('Mix of channels is fine, whatever makes sense.', 'Each landing page needs a defined audience.',
     finding('Build a central landing-page hub.', 'Each landing page needs a defined audience.'), 0),
    ('Containment is what we want to be known for.', 'Unsupported market leadership claims are banned.',
     finding('Containment is what we want to be known for.',
             'Unsupported market leadership claims are banned.', requires_user_decision=False), 0),
    ('Use the phrase limits what a compromised agent can do.',
     'Say limits what a compromised agent can do.',
     finding('Use the phrase limits what a compromised agent can do.',
             'Say limits what a compromised agent can do.', requires_user_decision=False), 0),
    ('Use a working room on the product page.', 'Product pages must use product UI.',
     finding('Use a working room on the product page.', 'Product pages must use product UI.'), 1),
])
def test_question_check_before_findings_are_published(monkeypatch, brief, rule, proposed, expected):
    monkeypatch.setattr(coverage, 'count_tokens', lambda text: len(text) // 4)
    requests = []

    def run(request, *, output_model):
        requests.append(request)
        assert 'Does this require a user decision' in request.instructions
        assert review_payload(request)['conversation'][0]['text'] == brief
        strict_output_schema(output_model)
        return SimpleNamespace(parsed=output_model.model_validate({
            'conflicts': [proposed], 'limitations': [], 'blocking_gaps': []}), run_id='test')

    result = coverage.review_campaign(SimpleNamespace(run=run), Settings(_env_file=None),
        SimpleNamespace(id='c', brand_id='b', data={}), [{'role': 'user', 'text': brief}],
        [{'chunk_id': 's', 'content': rule}])
    assert len(result['findings']) == expected
    assert len(requests) == 2
    if not expected:
        assert result['report']['limitations']


def test_assistant_proposal_cannot_ground_a_brief_conflict():
    item = coverage.DirectFinding.model_validate(finding('Build a landing page.', 'Use one audience.'))
    error = coverage.question_grounding_error(item, [
        {'role': 'user', 'text': 'Suggest channels.'},
        {'role': 'assistant', 'text': 'Build a landing page.'}],
        {'source_0': {'content': 'Use one audience.'}})
    assert 'absent from user messages' in error


def test_source_conflicts_remain_supported_without_inventing_user_intent():
    value = finding('Use old wording.', 'Use new wording.')
    value.update(source_refs=['source_0', 'source_1'], grounding=[
        {'origin': 'source', 'source_ref': 'source_0', 'quote': 'Use old wording.'},
        {'origin': 'source', 'source_ref': 'source_1', 'quote': 'Use new wording.'}])
    item = coverage.DirectFinding.model_validate(value)
    assert coverage.question_grounding_error(item, [], {
        'source_0': {'content': 'Use old wording.'},
        'source_1': {'content': 'Use new wording.'}}) is None
    item.grounding[1].quote = 'An invented rule.'
    assert 'absent from its cited source' in coverage.question_grounding_error(item, [], {
        'source_0': {'content': 'Use old wording.'},
        'source_1': {'content': 'Use new wording.'}})


def test_retry_replaces_cached_review_with_unverified_citations(monkeypatch):
    from app.brand import context
    from app.brand.retrieval import RetrievalError

    monkeypatch.setattr(coverage, 'count_tokens', lambda text: 1)
    monkeypatch.setattr(context, 'fingerprint', lambda *args: 'corpus')
    brief, rule = 'Use a room.', 'Use product UI.'
    bad = {'conflicts': [finding(brief, 'Invented quotation.')],
           'limitations': [], 'blocking_gaps': []}
    artifacts = [SimpleNamespace(corpus_fingerprint='corpus', payload={
        'stage': stage, 'result': bad}) for stage in
        ('conflict_find', 'conflict_check', 'conflict_grounding_repair')]
    saved = []
    monkeypatch.setattr(context, 'save_artifact', lambda *args: saved.append(args[-1]))

    class Session:
        def __init__(self, engine):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, statement):
            return SimpleNamespace(all=lambda: [('s', rule)])

        def scalars(self, statement):
            return artifacts

    monkeypatch.setattr('sqlalchemy.orm.Session', Session)
    requests = []
    succeeds = False

    def run(request, *, output_model):
        requests.append(request)
        if request.operation == 'campaign_conflict_grounding_repair':
            error = review_payload(request)['validation_errors'][0]
            assert error['finding_index'] == 0
            assert error['grounding'][1]['quote'] == 'Invented quotation.'
        value = {'conflicts': [finding(brief, rule)], 'limitations': [], 'blocking_gaps': []} if succeeds else bad
        return SimpleNamespace(parsed=output_model.model_validate(value), run_id='test')

    args = (SimpleNamespace(run=run), Settings(_env_file=None),
            SimpleNamespace(id='c', brand_id='b', data={}),
            [{'role': 'user', 'text': brief}], [{'chunk_id': 's', 'content': rule}])
    with pytest.raises(RetrievalError, match='citations remain unverified'):
        coverage.review_campaign(*args, engine=object())
    assert not saved
    assert len(requests) == 3
    succeeds = True
    result = coverage.review_campaign(*args, engine=object())
    assert len(result['findings']) == 1
    assert len(requests) == 5
    assert [a['stage'] for a in saved] == ['conflict_find', 'conflict_check']


@pytest.mark.parametrize('quote,source', [
    ('Vaskell Health is not cleared. Do not use the name.',
     '**Vaskell Health is not cleared.** Do not use the name.'),
    ('Healthcare. A good fit on paper.', '4. **Healthcare.** A good fit on paper.'),
])
def test_markdown_citations_restore_original_source(quote, source):
    item = coverage.DirectFinding.model_validate(finding('Use a room.', quote))
    assert coverage.question_grounding_error(item, [{'role': 'user', 'text': 'Use a room.'}],
        {'source_0': {'content': source}}) is None
    assert item.grounding[1].quote in source
    assert '**' in item.grounding[1].quote


@pytest.mark.parametrize('quote', ['Vaskell Health is cleared.', 'Vaskell Health is not cleared. Use the name.',
                                    'Vaskell Health is not cleared. ... Do not use the name.'])
def test_citation_matching_rejects_changed_words_and_omissions(quote):
    assert coverage.source_quote(quote, '**Vaskell Health is not cleared.** Do not use the name.') is None


def test_automatic_treatment_does_not_require_user_choices():
    value = finding('Use a room.', 'Use product UI.', requires_user_decision=False)
    value['options'] = ['Follow the established rule', '  ']
    review = coverage.DirectReview.model_validate({'conflicts': [value], 'limitations': [], 'blocking_gaps': []})
    coverage.validate_review(review, ['source_0'], [])
    review.conflicts[0].requires_user_decision = True
    with pytest.raises(coverage.RetrievalError, match='concrete options'):
        coverage.validate_review(review, ['source_0'], [])
    review.conflicts[0].requires_user_decision = False
    review.conflicts[0].decision_type = 'publication_permission'
    with pytest.raises(coverage.RetrievalError, match='concrete options'):
        coverage.validate_review(review, ['source_0'], [])
