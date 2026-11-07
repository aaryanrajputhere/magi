"""Structured planning clarifications, with support for saved legacy questions."""
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanningQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=1200)
    kind: Literal['choices', 'text']
    options: list[str] = Field(max_length=4)

    @model_validator(mode='after')
    def validate_options(self):
        options = [value.strip() for value in self.options]
        if self.kind == 'choices' and (len(options) < 2 or any(not v for v in options)
                                      or len(set(options)) != len(options)):
            raise ValueError('Choice questions need 2–4 distinct, nonempty options')
        if self.kind == 'text' and options:
            raise ValueError('Text questions cannot have options')
        self.options = options
        return self


def question_text(question):
    return question if isinstance(question, str) else question['text']


def pending_plan_question(question):
    value = ({'text': question, 'kind': 'text', 'options': []} if isinstance(question, str)
             else PlanningQuestion.model_validate(question).model_dump())
    return {'id': str(uuid4()), **value, 'answer': None}


PLANNING_QUESTION_GUIDANCE = (
    'Before asking a planning question, inspect the supplied original evidence and current decisions. '
    'Reuse an exact quote when present; do not ask the user to repeat it. Never infer publication '
    'clearance from a quote alone. Ask only one essential decision at a time. Return structured '
    'questions with text, reason, kind and options. The reason must distinguish what the supplied '
    'evidence establishes from what is missing; do not claim to have searched material not supplied. '
    'Prefer kind=choices with 2–4 actionable, mutually distinct routes. For missing customer quote '
    'or clearance, offer continuing without the optional customer example, planning with a clearly '
    'marked placeholder pending clearance, or providing approved wording and clearance details, '
    'only when each route fits explicit user requirements. Do not offer an unverified replacement '
    'claim or bypass mandatory requirements. Use kind=text and options=[] only when a concrete '
    'value is indispensable and no valid alternatives exist. A placeholder is an unresolved '
    'production dependency, never approved audience-facing copy. Apply the user’s selected route '
    'and accompanying details on continuation; do not repeat an answered question unless the '
    'answer is ambiguous or relevant evidence or user intent changes. '
)
