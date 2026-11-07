from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api.campaigns import Plan, PlanOutline
from app.brand.clarifications import PlanningQuestion, pending_plan_question, question_text
from app.brand.decisions import current_decisions, record_answer
from app.llm.client import strict_output_schema


def clarification():
    return {'text': 'How should we handle the customer example?',
            'reason': 'A customer quote is present; campaign publication clearance is not established.',
            'kind': 'choices', 'options': ['Continue without the customer example',
                'Plan with a placeholder pending clearance', 'Provide approved wording and clearance']}


def test_plan_provider_schema_preserves_question_choices_and_reason():
    outline = {'goal': 'Awareness', 'questions': [clarification()],
               'assumptions': [], 'constraints': [], 'assets': []}
    parsed = PlanOutline.model_validate(outline).model_dump()
    pending = pending_plan_question(parsed['questions'][0])
    assert pending['options'] == clarification()['options']
    assert pending['reason'] == clarification()['reason']
    assert pending['kind'] == 'choices'
    assert pending['answer'] is None
    assert question_text(parsed['questions'][0]) == clarification()['text']
    strict_output_schema(PlanOutline)
    with pytest.raises(ValidationError):
        PlanOutline.model_validate({**outline, 'questions': [clarification(), clarification()]})


@pytest.mark.parametrize('options', [['One'], ['One', 'One'], ['One', ' '], list('abcde')])
def test_choice_options_must_be_actionable_and_distinct(options):
    with pytest.raises(ValidationError):
        PlanningQuestion.model_validate({**clarification(), 'options': options})


def test_legacy_saved_plans_and_text_questions_remain_readable():
    pending = pending_plan_question('What date should we use?')
    assert pending['kind'] == 'text'
    assert pending['options'] == []
    assert question_text('Legacy question') == 'Legacy question'
    plan = Plan.model_validate({'reply': 'Need a date', 'reply_paragraphs': [],
        'questions': ['Legacy question'], 'goal': 'Awareness',
        'assumptions': [], 'constraints': [], 'assets': []})
    assert plan.questions == ['Legacy question']


@pytest.mark.parametrize('answer', [
    'Continue without the customer example',
    'Plan with a placeholder pending clearance\nAdditional details: Keep the other five assets moving.',
    'Use only the approved product example instead.',
])
def test_answer_is_saved_and_available_to_continued_planning(answer):
    pending = pending_plan_question(clarification())
    campaign = SimpleNamespace(data={'pending_question': pending, 'questions': [pending],
        'messages': [{'role': 'user', 'text': 'Create the campaign.'}]})
    record_answer(campaign, pending, answer)
    assert campaign.data['pending_question'] is None
    assert campaign.data['questions'][0]['answer'] == answer
    decision, = current_decisions(campaign)
    assert decision['answer'] == answer
    assert decision['question'] == clarification()['text']


def test_agent_publishes_structured_planning_question(tmp_path, monkeypatch):
    import json

    from sqlalchemy.orm import Session

    from app import campaign_agent
    from app.api import campaigns
    from app.brand.campaign_requirements import requirement_revision
    from app.config import Settings
    from app.db import create_database_engine, initialize_database

    engine = create_database_engine(f"sqlite:///{tmp_path / 'planning.db'}")
    initialize_database(engine)

    def run(self, request):
        return SimpleNamespace(response={'output': [{'type': 'function_call',
            'name': 'propose_plan', 'arguments': json.dumps({'instruction': 'Plan', 'addition': False}),
            'call_id': 'plan'}]})

    def process(identifier, body, session, settings, **kwargs):
        campaign = campaigns.load(session, identifier)
        campaign.state = 'clarifying'
        campaign.data = {**campaign.data, 'plans': [{'questions': [clarification()], 'assets': []}]}
        session.commit()
        return campaigns.snapshot(campaign)

    monkeypatch.setattr(campaign_agent.TrackedOpenAI, 'run', run)
    monkeypatch.setattr(campaigns, 'process_message', process)
    revision = requirement_revision([{'role': 'user', 'text': 'Plan'}], 'campaign')
    try:
        with Session(engine) as session:
            session.add(campaigns.Campaign(id='c', brand_id='b', data={'messages': [],
                'requirements': {'assets': [], 'issues': []}, 'requirements_revision': revision,
                'retrieved_context': {'requirements_revision': revision}}))
            session.commit()
            result = campaign_agent.run_turn('c', campaigns.Message(text='Plan', version=0),
                                             session, Settings(_env_file=None))
            question = result['pending_question']
            assert question['kind'] == 'choices'
            assert question['options'] == clarification()['options']
            assert question['reason'] == clarification()['reason']
            assert result['messages'][-1]['question'] == question
    finally:
        engine.dispose()
