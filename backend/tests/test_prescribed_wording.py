from app.brand.prescribed_wording import prescribed_wording, resolves_question


def test_exact_governing_wording_is_automatic_but_permission_is_not():
    evidence = [{'chunk_id': 'source', 'document_id': 'legal',
                 'content': 'In process. The approved sentence is "Example is pursuing authorization." Never imply it is held.'}]
    authority = {'groups': [{'governing': True, 'topics': ['claims'], 'channels': None,
                              'priority': 1, 'document_ids': ['legal']}]}
    rules = prescribed_wording(evidence, authority, {'blog', 'email'})
    assert rules[0]['approved_wording'] == 'Example is pursuing authorization.'
    assert rules[0]['rule_quote'] in evidence[0]['content']
    assert resolves_question({'text': 'Should we use Example is pursuing authorization.?', 'reason': ''}, rules)
    assert not resolves_question({'text': 'Do we have customer permission for Example is pursuing authorization.?'}, rules)
    assert not prescribed_wording(evidence, {}, {'blog'})
    assert not resolves_question({'text': 'Which audience?'}, rules)


def test_saved_wording_question_can_resume_without_fabricating_user_answer():
    from types import SimpleNamespace
    from app.brand.prescribed_wording import resolve_saved_question
    question = {'id': 'q', 'text': 'Use Example is pursuing authorization.?', 'reason': ''}
    campaign = SimpleNamespace(state='clarifying', data={'pending_question': question,
        'questions': [question], 'messages': [{'role': 'user', 'text': 'Original brief'}]})
    assert resolve_saved_question(campaign, [{'approved_wording': 'Example is pursuing authorization.',
                                            'source_id': 'original'}])
    assert campaign.data['pending_question'] is None
    assert 'answer' not in campaign.data['questions'][0]
    assert campaign.data['questions'][0]['source_resolution']['origin'] == 'source_rule'
    assert campaign.data['messages'] == [{'role': 'user', 'text': 'Original brief'}]
