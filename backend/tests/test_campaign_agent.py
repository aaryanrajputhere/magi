import json
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import campaign_agent
from app.api import campaigns
from app.config import Settings
from app.db import create_database_engine, initialize_database
from app.models.agent import AgentTurn, CampaignLease
from app.brand.campaign_requirements import requirement_revision


def test_agent_proposes_then_finishes_without_repeating_action(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'agent.db'}")
    initialize_database(engine)
    requests = []
    actions = []

    class Client:
        def __init__(self, *args):
            pass

        def run(self, request):
            requests.append(request)
            assert request.tool_choice == "required"
            name = "propose_plan" if len(requests) == 1 else "finish_turn"
            args = ({"instruction": "Five posts", "addition": False}
                    if name == "propose_plan" else {"text": "Plan ready for approval."})
            return SimpleNamespace(response={"output": [{
                "type": "function_call", "name": name,
                "arguments": json.dumps(args), "call_id": str(len(requests)),
            }]})

        def close(self):
            pass

    def process(identifier, body, session, settings, **kwargs):
        actions.append(body.text)
        c = campaigns.load(session, identifier)
        c.state = "awaiting_approval"
        c.version += 1
        session.commit()
        return campaigns.snapshot(c)

    monkeypatch.setattr(campaign_agent, "TrackedOpenAI", Client)
    monkeypatch.setattr(campaigns, "process_message", process)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id="c", brand_id="b", data={"messages": [], "requirements": {"assets": [], "issues": []}, "retrieved_context": {"requirements_revision": requirement_revision([{"role": "user", "text": "Five posts"}], "campaign")}, "requirements_revision": requirement_revision([{"role": "user", "text": "Five posts"}], "campaign")}))
            session.commit()
            body = campaigns.Message(text="Five posts", version=0, request_id="test")
            result = campaign_agent.run_turn("c", body, session, Settings(_env_file=None))
            assert result["state"] == "awaiting_approval"
            assert session.get(AgentTurn, "c:test").status == "completed"
            assert session.scalar(select(CampaignLease)) is None
            assert len(actions) == 1
            assert len(requests) == 1
            assert campaign_agent.run_turn("c", body, session, Settings(_env_file=None)) == result
            assert len(actions) == 1
    finally:
        engine.dispose()


def test_clarification_then_approval_does_not_replan(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'clarification.db'}")
    initialize_database(engine)
    actions = []

    def process(identifier, body, session, settings, **kwargs):
        actions.append((body.text, kwargs['forced_route'].route))
        c = campaigns.load(session, identifier)
        c.data = {**c.data, 'plans': [{'questions': [], 'assets': [{'id': 'post'}]}]}
        c.state = 'awaiting_approval'
        c.version += 1
        session.commit()
        return campaigns.snapshot(c)

    def no_model(*args, **kwargs):
        return SimpleNamespace(response={'output': [{'type': 'function_call', 'name': 'finish_turn',
            'arguments': json.dumps({'text': 'Please clarify the request.'}), 'call_id': 'finish'}]})

    monkeypatch.setattr(campaigns, 'process_message', process)
    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', no_model)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='clarifying',
                data={'messages': [], 'plans': [{'questions': ['Proceed without statistics?'], 'assets': []}]}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='confirm', version=0),
                                             session, Settings(_env_file=None))
            assert result['state'] == 'clarifying'
            c = campaigns.load(session, 'c')
            process('c', campaigns.Message(text='confirm', version=c.version), session, Settings(_env_file=None), forced_route=campaigns.MessageRoute(route='campaign', target_asset_id=None, reply=''))
            result = campaigns.snapshot(c)
            result = campaign_agent.run_turn('c', campaigns.Message(text='approved', version=result['version']),
                                             session, Settings(_env_file=None))
            assert actions == [('confirm', 'campaign')]
            assert 'Approve & generate' in result['messages'][-1]['text']
            assert 'generation' not in result
            assert session.scalar(select(CampaignLease)) is None
    finally:
        engine.dispose()


def test_addition_without_drafts_uses_normal_plan(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'addition.db'}")
    initialize_database(engine)
    routes = []

    def run(self, request):
        return SimpleNamespace(response={'output': [{'type': 'function_call',
            'name': 'propose_plan', 'arguments': json.dumps({'instruction': 'Five assets', 'addition': True}),
            'call_id': 'plan'}]})

    def process(identifier, body, session, settings, **kwargs):
        routes.append(kwargs['forced_route'].route)
        c = campaigns.load(session, identifier)
        c.state = 'awaiting_approval'
        session.commit()
        return campaigns.snapshot(c)

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    monkeypatch.setattr(campaigns, 'process_message', process)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': [], 'plans': [], 'requirements': {'assets': [], 'issues': []}, 'retrieved_context': {'requirements_revision': requirement_revision([{'role': 'user', 'text': 'Five assets'}], 'campaign')}, 'requirements_revision': requirement_revision([{'role': 'user', 'text': 'Five assets'}], 'campaign')}))
            session.commit()
            campaign_agent.run_turn('c', campaigns.Message(text='Five assets', version=0),
                                    session, Settings(_env_file=None))
            assert routes == ['campaign']
    finally:
        engine.dispose()


def test_requirement_revision_tracks_user_intent_and_mode():
    from app.brand.campaign_requirements import requirement_revision
    messages = [{'role': 'user', 'text': 'Three launch posts'}]
    initial = requirement_revision(messages, 'campaign')
    messages.append({'role': 'assistant', 'text': 'What launch date?'})
    assert requirement_revision(messages, 'campaign') == initial
    assert requirement_revision(messages, 'extend') != initial
    messages.append({'role': 'user', 'text': 'November 10'})
    assert requirement_revision(messages, 'campaign') != initial


def test_structured_question_is_saved_and_answered(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'question.db'}")
    initialize_database(engine)
    calls = iter([
        ('ask_question', {'text': 'Which audience?', 'kind': 'choices', 'options': ['Buyers', 'Developers']}),
        ('extract_requirements', {'addition': False}),
        ('ask_question', {'text': 'Which product is launching?', 'kind': 'text', 'options': []}),
    ])

    def run(self, request, **kwargs):
        if request.operation == 'campaign_decision_match':
            return SimpleNamespace(parsed=kwargs['output_model'].model_validate({'matches': [{
                'finding_index': 0, 'decision_id': None, 'disposition': 'new', 'reason': 'Different subject',
                'changed_source_ids': [], 'changed_user_instruction': None}]}))
        name, arguments = next(calls)
        return SimpleNamespace(response={'output': [{'type': 'function_call', 'name': name,
            'arguments': json.dumps(arguments), 'call_id': name}]})

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    from app.brand import campaign_requirements
    monkeypatch.setattr(campaign_requirements, 'extract_requirements', lambda *a: {
        'assets': [], 'unknowns': [], 'issues': [{'question': 'Which product is launching?'}]})
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': []}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='Create a launch', version=0), session, Settings(_env_file=None))
            question = result['pending_question']
            assert question['options'] == ['Buyers', 'Developers']
            assert result['messages'][-1]['question']['id'] == question['id']
            result = campaign_agent.run_turn('c', campaigns.Message(text='Developers', question_id=question['id'], version=result['version']), session, Settings(_env_file=None))
            assert result['pending_question']['text'] == 'Which product is launching?'
            assert result['questions'][0]['answer'] == 'Developers'
            assert 'generation' not in result
    finally:
        engine.dispose()


def test_question_schema_rejects_invalid_choices():
    import pytest
    from pydantic import ValidationError
    for kind, options in [('choices', []), ('choices', ['Same', 'Same']), ('text', ['Unexpected'])]:
        with pytest.raises(ValidationError):
            campaign_agent.Question(text='Question?', kind=kind, options=options)
    assert campaign_agent.Question(text='Launch date?', kind='text', options=[]).kind == 'text'


def test_early_planning_returns_recoverable_feedback(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'blocked.db'}")
    initialize_database(engine)
    requests = []

    def run(self, request):
        requests.append(request)
        name = 'propose_plan' if len(requests) == 1 else 'ask_question'
        args = {'instruction': 'Launch', 'addition': False} if name == 'propose_plan' else {
            'text': 'How many posts?', 'kind': 'text', 'options': []}
        if len(requests) == 2:
            feedback = json.loads(request.input[-1]['output'])
            assert feedback['status'] == 'blocked'
            assert 'extract_requirements' in feedback['available_actions']
        return SimpleNamespace(response={'output': [{'type': 'function_call', 'name': name,
            'arguments': json.dumps(args), 'call_id': name}]})

    def no_planning(*args, **kwargs):
        raise AssertionError('Planning must not start without current requirements')

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    monkeypatch.setattr(campaigns, 'process_message', no_planning)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': []}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='Launch', version=0), session, Settings(_env_file=None))
            assert result['pending_question']['text'] == 'How many posts?'
    finally:
        engine.dispose()


def test_retrieve_context_saves_artifact_without_plan(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'context.db'}")
    initialize_database(engine)
    monkeypatch.setattr(campaigns, 'prepare_conflict_intake', lambda *a, **kw: False)
    monkeypatch.setattr(campaigns, 'plan_retrieval', lambda *a, **kw: {'id': 'search', 'plan': {}})
    monkeypatch.setattr(campaigns, 'execute_retrieval', lambda *a, **kw: {'id': 'retrieval'})
    monkeypatch.setattr(campaigns, 'retrieval_checkpoint', lambda *a, **kw: {
        'id': 'context', 'evidence': [{'chunk_id': 'source'}], 'tracks': {'topic': {'evidence': [{'chunk_id': 'source'}]}, 'linkedin': {'evidence': []}}})
    try:
        with Session(engine) as session:
            campaign = campaigns.Campaign(id='c', brand_id='b', data={
                'messages': [{'role': 'user', 'text': 'Launch'}],
                'requirements': {'topic': 'Launch', 'assets': []}})
            session.add(campaign)
            session.commit()
            output = campaigns.retrieve_campaign_context(session, campaign, Settings(_env_file=None), 'campaign')
            assert output['artifact_id'] == 'context'
            assert output['empty_tracks'] == ['linkedin']
            assert campaign.data['retrieved_context']['requirements_revision'] == requirement_revision(campaign.data['messages'], 'campaign')
            assert not campaign.data.get('plans')
            assert not campaign.data.get('generation')
    finally:
        engine.dispose()


def test_targeted_plan_revision_preserves_other_assets(tmp_path):
    from copy import deepcopy
    engine = create_database_engine(f"sqlite:///{tmp_path}/revision.db")
    initialize_database(engine)
    assets = [dict(id=str(i), format='linkedin', audience='Leads', purpose='Launch', timing=None,
                   angle='Product', hook='New release', cta='Read more') for i in range(1, 12)]
    requirements = {'topic': 'Launch', 'assets': [dict(a) for a in assets], 'constraints': []}
    original = {'assets': assets, 'requirements': requirements, 'questions': [], 'goal': 'Launch'}
    with Session(engine) as session:
        c = campaigns.Campaign(id='c', brand_id='b', state='approved', data={
            'plans': [deepcopy(original)], 'messages': [{'role': 'user', 'text': 'Remove post 11'}]})
        session.add(c)
        session.commit()
        result = campaigns.revise_campaign_plan(session, c, ['11'], [{'asset_id': '2', 'changes': {'hook': 'Updated hook'}}])
        assert result['asset_count'] == 10
        assert c.state == 'awaiting_approval'
        assert c.data['plans'][0] == original
        assert c.data['plans'][1]['assets'][0] == assets[0]
        assert c.data['plans'][1]['assets'][1]['hook'] == 'Updated hook'
        assert len(c.data['requirements']['assets']) == 10
        before = deepcopy(c.data)
        assert campaigns.revise_campaign_plan(session, c, ['unknown'], [])['status'] == 'blocked'
        assert c.data == before
        c.data = {**c.data, 'generation': {'drafts': [{'asset_id': '1'}]}}
        assert campaigns.revise_campaign_plan(session, c, ['1'], [])['status'] == 'blocked'
    engine.dispose()


def test_reviewed_plan_removal_reuses_review_without_workflow(tmp_path, monkeypatch):
    from copy import deepcopy
    from app.brand.plan_verification import approval_error, plan_signature

    def unexpected(*args, **kwargs):
        raise AssertionError('Removal must not restart research or verification')

    monkeypatch.setattr(campaigns, 'prepare_conflict_intake', unexpected)
    monkeypatch.setattr(campaigns, 'load_artifact', unexpected)
    monkeypatch.setattr('app.brand.plan_verification.verify_plan', unexpected)
    engine = create_database_engine(f"sqlite:///{tmp_path}/removal.db")
    initialize_database(engine)
    assets = [dict(id=f'post-{i}', format='linkedin', audience='Leads', purpose='Launch',
                   timing=None, angle='Product', hook='New release', cta='Read more')
              for i in range(1, 6)]
    requirements = {'topic': 'Launch', 'assets': deepcopy(assets), 'constraints': []}
    original = {'assets': assets, 'requirements': requirements, 'questions': [],
                'goal': 'Launch', 'conflict_review_key': 'review',
                'asset_packages': {a['id']: {'requirement': deepcopy(a), 'evidence': ['source']}
                                   for a in assets}}
    original['conflict_verification'] = {'signature': plan_signature(original), 'history': []}
    coverage = {'status': 'ready', 'coverage_challenged': True, 'snapshot_key': 'review'}
    with Session(engine) as session:
        c = campaigns.Campaign(id='c', brand_id='b', state='approved', data={
            'plans': [deepcopy(original)], 'conflict_coverage': coverage,
            'retrieved_context': {'artifact_id': 'existing', 'requirements_revision': 'old'},
            'messages': [{'role': 'user', 'text': 'remove post 4 from the plan'}]})
        session.add(c)
        session.commit()
        result = campaigns.revise_campaign_plan(session, c, ['post-4'], [])
        assert result['status'] == 'saved'
        assert result['asset_count'] == 4
        assert c.state == 'awaiting_approval'
        assert c.data['plans'][0] == original
        revised = c.data['plans'][-1]
        assert revised['assets'] == [a for a in assets if a['id'] != 'post-4']
        assert revised['asset_packages'] == {k: v for k, v in original['asset_packages'].items()
                                             if k != 'post-4'}
        assert c.data['conflict_coverage'] == coverage
        assert c.data['retrieved_context']['artifact_id'] == 'existing'
        assert approval_error(c) is None
        # A stale verification must not become valid just because an asset is removed.
        c.data = {**c.data, 'plans': [dict(revised, goal='Changed goal')]}
        before = deepcopy(c.data)
        assert campaigns.revise_campaign_plan(session, c, ['post-1'], [])['status'] == 'blocked'
        assert c.data == before
    engine.dispose()


def test_video_choice_is_rejected_before_question_is_saved(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'unsupported.db'}")
    initialize_database(engine)
    requests = []

    def run(self, request, **kwargs):
        requests.append(request)
        options = ['Videos', 'Emails'] if len(requests) == 1 else ['Technical buyers', 'Executives']
        return SimpleNamespace(response={'output': [{
            'type': 'function_call', 'name': 'ask_question', 'call_id': str(len(requests)),
            'arguments': json.dumps({'text': 'Which audience?' if len(requests) > 1 else 'Which format?',
                                     'kind': 'choices', 'options': options})}]})

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': []}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(
                text='Plan a Q1 awareness campaign. Any mix of channels is fine.', version=0),
                session, Settings(_env_file=None))
            assert len(requests) == 2
            assert result['pending_question']['options'] == ['Technical buyers', 'Executives']
            assert len(campaigns.load(session, 'c').data['questions']) == 1
            assert 'Unsupported media' in requests[-1].input[-1]['output']
    finally:
        engine.dispose()


def test_question_with_seven_choices_retries_without_stopping(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'question_retry.db'}")
    initialize_database(engine)
    calls = []

    def run(self, request, **kwargs):
        calls.append(request)
        if len(calls) == 2:
            feedback = request.input[-1]
            assert feedback['type'] == 'function_call_output'
            assert json.loads(feedback['output'])['status'] == 'invalid_arguments'
        options = [f'Choice {i}' for i in range(7)] if len(calls) == 1 else ['Buyers', 'Executives']
        return SimpleNamespace(response={'output': [{
            'type': 'function_call', 'name': 'ask_question', 'call_id': str(len(calls)),
            'arguments': json.dumps({'text': 'Which audience?', 'kind': 'choices', 'options': options})}]})

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    try:
        with Session(engine) as session:
            requirements = {'assets': [], 'issues': []}
            session.add(campaigns.Campaign(id='c', brand_id='b', data={
                'messages': [], 'requirements': requirements}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(
                text='Plan a campaign', version=0), session, Settings(_env_file=None))
            assert len(calls) == 2
            assert result['pending_question']['options'] == ['Buyers', 'Executives']
            saved = campaigns.load(session, 'c').data
            assert len(saved['questions']) == 1
            assert saved['requirements'] == requirements
            assert session.scalar(select(CampaignLease)) is None
    finally:
        engine.dispose()


def test_answer_forces_extraction_retrieval_then_plan(tmp_path, monkeypatch):
    from app.brand import campaign_requirements
    engine = create_database_engine(f"sqlite:///{tmp_path / 'resume.db'}")
    initialize_database(engine)
    offered = []
    actions = []

    def run(self, request, **kwargs):
        names = [t['name'] for t in request.tools]
        offered.append(set(names))
        name = 'propose_plan' if 'propose_plan' in names else names[0]
        args = {'addition': False}
        if name == 'propose_plan':
            args['instruction'] = 'Plan using the clarified wording'
        return SimpleNamespace(response={'output': [{'type': 'function_call', 'name': name,
            'arguments': json.dumps(args), 'call_id': str(len(offered))}]})

    def extract(*args):
        actions.append('extract')
        assert args[-1]['conversation'][-1]['text'] == 'Use pursuing authorization'
        return {'assets': [], 'issues': [], 'unknowns': []}

    def retrieve(session, campaign, settings, mode, client=None):
        actions.append('retrieve')
        campaign.data = {**campaign.data, 'retrieved_context': {
            'requirements_revision': campaign.data['requirements_revision']}}
        campaign.version += 1
        session.commit()
        return {'version': campaign.version}

    def process(identifier, body, session, settings, **kwargs):
        actions.append('plan')
        campaign = campaigns.load(session, identifier)
        campaign.data = {**campaign.data, 'plans': [{'questions': [], 'assets': [{'id': 'post'}]}]}
        campaign.state = 'awaiting_approval'
        campaign.version += 1
        session.commit()
        return campaigns.snapshot(campaign)

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    monkeypatch.setattr(campaign_requirements, 'extract_requirements', extract)
    monkeypatch.setattr(campaigns, 'retrieve_campaign_context', retrieve)
    monkeypatch.setattr(campaigns, 'process_message', process)
    try:
        with Session(engine) as session:
            question = {'id': 'q', 'text': 'Which wording?', 'kind': 'choices',
                        'options': ['Authorized', 'Pursuing'], 'answer': None}
            session.add(campaigns.Campaign(id='c', brand_id='b', state='clarifying', data={
                'messages': [], 'plans': [], 'pending_question': question, 'questions': [question]}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='Use pursuing authorization',
                question_id='q', version=0), session, Settings(_env_file=None))
            assert actions == ['extract', 'retrieve', 'plan']
            assert offered == [{'propose_plan', 'ask_question'}]  # prerequisites dispatch without a model call
            assert result['state'] == 'awaiting_approval'
            assert result['pending_question'] is None
            assert result['questions'][0]['answer'] == 'Use pursuing authorization'
    finally:
        engine.dispose()


def test_blocked_revision_cannot_finish_with_promise(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'blocked_finish.db'}")
    initialize_database(engine)
    calls = []

    def run(self, request, **kwargs):
        calls.append(request)
        if len(calls) == 1:
            name, args = 'revise_plan', {'remove_asset_ids': ['missing'], 'patches': []}
        elif len(calls) == 2:
            name, args = 'finish_turn', {'text': 'Proceeding with changes now.'}
        else:
            assert json.loads(request.input[-1]['output'])['status'] == 'blocked'
            name, args = 'ask_question', {'text': 'Which campaign should I edit?', 'kind': 'text', 'options': []}
        return SimpleNamespace(response={'output': [{'type': 'function_call', 'name': name,
            'arguments': json.dumps(args), 'call_id': str(len(calls))}]})

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': [], 'plans': []}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='Edit the plan', version=0),
                                             session, Settings(_env_file=None))
            assert result['state'] == 'clarifying'
            assert len(calls) == 3
            assert all(m['text'] != 'Proceeding with changes now.' for m in result['messages'])
            assert [item['tool'] for item in result['agent_last_turn']['completed']] == ['ask_question']
            turn = session.get(AgentTurn, result['agent_last_turn']['id'])
            assert turn.data['executions'] == []
    finally:
        engine.dispose()


def test_conflict_choices_reuse_retrieval_but_new_instructions_invalidate_it():
    messages = [{'role': 'user', 'text': 'Campaign brief'}]
    revision = requirement_revision(messages, 'campaign')
    messages.extend([
        {'role': 'assistant', 'question': {'id': 'q', 'conflict_id': 'issue',
                                         'options': ['Use source wording', 'Keep request']}},
        {'role': 'user', 'text': 'Use source wording'}])
    assert requirement_revision(messages, 'campaign') == revision
    messages.append({'role': 'user', 'text': 'Add a new product launch topic'})
    assert requirement_revision(messages, 'campaign') != revision


def test_preference_answers_still_invalidate_requirements():
    messages = [{'role': 'user', 'text': 'Campaign brief'}]
    revision = requirement_revision(messages, 'campaign')
    messages.extend([
        {'role': 'assistant', 'question': {'id': 'q', 'options': ['Email', 'Blog']}},
        {'role': 'user', 'text': 'Email'}])
    assert requirement_revision(messages, 'campaign') != revision


def test_saved_conflict_choice_skips_extraction_and_retrieval(tmp_path, monkeypatch):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'resume.db'}")
    initialize_database(engine)
    question = {'id': 'q', 'conflict_id': 'issue', 'options': ['Follow source', 'Keep request']}
    messages = [{'role': 'user', 'text': 'Campaign brief'},
                {'role': 'assistant', 'question': question}]
    revision = requirement_revision(messages, 'campaign')
    calls = []

    class Client:
        def __init__(self, *args):
            pass

        def run(self, request):
            calls.append(request.operation)
            names = {t['name'] for t in request.tools}
            assert names == {'propose_plan', 'ask_question'}
            return SimpleNamespace(response={'output': [{'type': 'function_call',
                'name': 'propose_plan', 'arguments': json.dumps({'instruction': 'Apply the saved answer',
                'addition': False}), 'call_id': 'resume'}]})

        def close(self):
            pass

    def process(identifier, body, session, settings, **kwargs):
        campaign = campaigns.load(session, identifier)
        assert campaign.data['retrieved_context']['artifact_id'] == 'saved-evidence'
        assert campaign.data['decisions'][-1]['answer'] == 'Follow source'
        assert campaign.data['pending_candidate']['goal'] == 'Original candidate'
        campaign.state = 'awaiting_approval'
        campaign.version += 1
        session.commit()
        return campaigns.snapshot(campaign)

    monkeypatch.setattr(campaign_agent, 'TrackedOpenAI', Client)
    monkeypatch.setattr(campaigns, 'process_message', process)
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', state='clarifying', data={
                'messages': messages, 'plans': [], 'questions': [question], 'pending_question': question,
                'pending_candidate': {'goal': 'Original candidate'},
                'requirements': {'assets': [], 'issues': []}, 'requirements_revision': revision,
                'retrieved_context': {'artifact_id': 'saved-evidence', 'requirements_revision': revision}}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='Follow source',
                question_id='q', version=0), session, Settings(_env_file=None))
            assert result['state'] == 'awaiting_approval'
            assert calls == ['campaign_agent_step']
    finally:
        engine.dispose()


def test_retrieval_collects_conflicts_and_stops_agent_before_planning(tmp_path, monkeypatch):
    from app.brand import conflicts
    engine = create_database_engine(f"sqlite:///{tmp_path / 'intake.db'}")
    initialize_database(engine)
    operations = []
    def review(*args, **kwargs):
        operations.append('campaign_conflict_comparisons')
        return [dict(id=topic, status='unresolved', explanation=f'{topic} conflicts with source',
            statements=[f'{topic} requested', f'{topic} restriction'], source_ids=['s'],
            affected_asset_ids=[], question=f'Resolve {topic}?',
            options=[f'Follow {topic} source', f'Keep {topic} request'])
            for topic in ('status', 'clearance')]
    monkeypatch.setattr(conflicts, 'review_campaign', review)
    monkeypatch.setattr(conflicts, 'validate_detected_conflicts', lambda *args: (args[-2], []))
    monkeypatch.setattr(conflicts, 'group_duplicate_conflicts', lambda *args: args[-1])
    monkeypatch.setattr(campaigns, 'plan_retrieval', lambda *a, **k: {'id': 'search', 'plan': {}})
    monkeypatch.setattr(campaigns, 'execute_retrieval', lambda *a, **k: {'id': 'retrieval'})
    monkeypatch.setattr(campaigns, 'retrieval_checkpoint', lambda *a, **k: {
        'id': 'context', 'evidence': [{'chunk_id': 's', 'content': 'Source restrictions'}],
        'tracks': {'topic': {'evidence': [{'chunk_id': 's'}]}}})
    class Client:
        def __init__(self, *args):
            pass
        def close(self):
            pass
        def run(self, request, **kwargs):
            operations.append(request.operation)
            if request.operation == 'campaign_agent_step':
                return SimpleNamespace(response={'output': [{'type': 'function_call', 'name': 'retrieve_context',
                    'arguments': json.dumps({'addition': False}), 'call_id': 'retrieve'}]})
            raise AssertionError(request.operation)
    monkeypatch.setattr(campaign_agent, 'TrackedOpenAI', Client)
    try:
        with Session(engine) as session:
            messages = [{'role': 'user', 'text': 'Brief'}]
            session.add(campaigns.Campaign(id='c', brand_id='b', data={
                'messages': messages, 'plans': [], 'requirements': {'topic': 'Brief', 'assets': [], 'issues': []},
                'requirements_revision': requirement_revision([*messages, {'role': 'user', 'text': 'Brief'}], 'campaign')}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='Brief', version=0), session, Settings(_env_file=None))
            assert result['state'] == 'clarifying'
            assert len(result['conflicts']) == 2
            assert not result.get('pending_question')
            report = '\n'.join(m['text'] for m in result['messages'] if m.get('question'))
            assert 'Resolve status?' in report
            assert 'Resolve clearance?' in report
            assert result['plans'] == []
            assert operations == ['campaign_agent_step', 'campaign_conflict_comparisons']
    finally:
        engine.dispose()
