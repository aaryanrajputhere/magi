"""Bind generated plans to completed evidence review for approval."""
import json
from typing import Literal

from pydantic import Field, create_model

from app.brand.conflict_coverage import (
    PlanVerification, digest, require_ids, review_conversation, review_decisions,
)
from app.brand.decisions import DECISION_GUIDANCE, current_decisions
from app.llm.client import ModelRequest
from app.llm.context import RunContext
from app.brand.retrieval import RetrievalError


def plan_signature(plan):
    return digest({k: plan.get(k) for k in ('goal', 'assumptions', 'constraints', 'questions', 'assets')})


def verify_plan(client, settings, campaign, messages, plan):
    """Check the outline and repair once, then display it without a second verification."""
    from app.api.campaigns import bind_plan_requirements, requirement_bound_plan_schema
    from app.brand.campaign_requirements import require_deliverables
    report = campaign.data.get('conflict_coverage', {})
    if report.get('status') != 'ready' or not report.get('coverage_challenged'):
        raise RetrievalError('Complete conflict coverage before proposing a plan for approval')
    requirements = plan.get('requirements')
    if requirements is not None:
        plan = bind_plan_requirements(plan, requirements)
        require_deliverables(plan, requirements)
    expected = [asset['id'] for asset in plan['assets']]
    verification_schema = create_model('AssetPlanVerification', __base__=PlanVerification,
        reviewed_asset_ids=(list[Literal[tuple(expected)]] if expected else list[str],
                            Field(..., **({} if expected else {'max_length': 0}))))
    lean = {k: plan.get(k) for k in ('goal', 'assumptions', 'constraints', 'questions', 'assets')}
    packs = {p['rule_pack']['format']: p['rule_pack']
             for p in plan.get('asset_packages', {}).values() if p.get('rule_pack')}
    result = client.run(ModelRequest(operation='campaign_plan_verification',
        model=settings.magi_retrieval_planner_model,
        instructions=DECISION_GUIDANCE +
        'Verify this candidate against the original brief and resolved conflicts for EVERY asset. '
        'Apply resolved treatments. Return each asset ID exactly once. Check exact required wording, '
        'imagery, live versus scheduled posting, dates, audiences and draft-only instructions. '
        'Do not re-open resolved issues or invent extra requirements. Return concrete actionable '
        'defects only; when every asset complies return defects: []. Check naming, claims and '
        'channel alignment against governing_rules. Do not demand full draft copy in an outline.',
        input=json.dumps({'conversation': review_conversation(messages),
            'review': {k: v for k, v in report.items() if k not in ['source_snapshot', 'findings']},
            'current_decisions': review_decisions(campaign), 'candidate': lean,
            'governing_rules': packs}),
        max_output_tokens=6000,
        context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id)),
        output_model=verification_schema)
    require_ids(result.parsed.reviewed_asset_ids, expected, 'plan assets')
    history = [{'run_id': result.run_id, **result.parsed.model_dump()}]
    plan = dict(plan)
    if result.parsed.defects:
        repaired = client.run(ModelRequest(operation='campaign_plan_coverage_repair',
            model=settings.magi_retrieval_planner_model,
            instructions=DECISION_GUIDANCE +
            'Repair only the concrete candidate defects. Preserve all asset IDs, formats, audiences, '
            'dates and unrelated fields. Apply reviewed decisions and exact wording. Return an outline, '
            'never full drafts. Do not resolve new conflicts or invent exceptions.',
            input=json.dumps({'candidate': lean, 'governing_rules': packs,
                'defects': result.parsed.defects, 'review': report, 'requirements': requirements,
                'current_decisions': current_decisions(campaign)}),
            max_output_tokens=12000,
            context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id)),
            output_model=requirement_bound_plan_schema(requirements))
        plan = {**plan, **repaired.parsed.model_dump()}
        if requirements is not None:
            plan = bind_plan_requirements(plan, requirements)
            require_deliverables(plan, requirements)
    plan.pop('recovery', None)
    plan['conflict_review_key'] = report['snapshot_key']
    plan['conflict_verification'] = {
        'signature': plan_signature(plan), 'history': history, 'method': 'check_and_repair'}
    return plan


def approval_error(campaign):
    plans = campaign.data.get('plans', [])
    if not plans:
        return None
    if plans[-1].get('recovery', {}).get('status') == 'review_required':
        return 'Finish evidence review and plan verification before approving this recovered plan'
    if not plans[-1].get('conflict_review_key'):
        if campaign.data.get('conflict_coverage'):
            return 'Finish the requested evidence review and plan verification before approval'
        return None  # Preserve legacy snapshots until an explicit review is requested.
    plan = plans[-1]
    report = campaign.data.get('conflict_coverage', {})
    if (report.get('status') != 'ready' or not report.get('coverage_challenged')
            or report.get('snapshot_key') != plan['conflict_review_key']
            or plan.get('conflict_verification', {}).get('signature') != plan_signature(plan)):
        return 'The current plan needs conflict review and verification before approval'
    return None
