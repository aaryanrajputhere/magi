"""Return a reviewable outline when model planning or validation cannot finish."""
from copy import deepcopy


def recover_plan(campaign, error, *, addition=False):
    """Build from the saved contract without another fallible model call."""
    from app.api.campaigns import PlannedAsset
    from app.brand.campaign_requirements import require_deliverables

    data = campaign.data
    requirements = data.get('requirements')
    if not requirements or not requirements.get('assets'):
        return None
    candidate = data.get('pending_candidate') or {}
    candidates = {a.get('id'): a for a in candidate.get('assets', [])}
    assets = []
    for requirement in requirements['assets']:
        asset = candidates.get(requirement['id'], {})
        # Unverified hooks and CTAs must not masquerade as approved claims.
        assets.append(PlannedAsset(
            id=requirement['id'], format=requirement['format'],
            audience=requirement['audience'], timing=requirement.get('timing'),
            purpose=(requirement.get('purpose') or 'Develop the requested content')[:350],
            angle=str(asset.get('angle') or requirement.get('purpose') or 'Develop the requested topic')[:160],
            hook='Develop an opening supported by the campaign evidence.',
            cta='Choose a destination supported by the brief.',
        ).model_dump())
    plan = {
        'goal': requirements.get('topic') or 'Campaign plan',
        'assumptions': ['This outline needs evidence review and verification before approval.'],
        'constraints': deepcopy(requirements.get('constraints', [])),
        'questions': [], 'assets': assets, 'requirements': deepcopy(requirements),
        'recovery': {'status': 'review_required', 'reason': str(error)},
        'reply': 'Returned a plan using your saved requirements. Evidence review and verification still need to finish before approval.',
        'citations': {}, 'conflicts': [],
    }
    require_deliverables(plan, requirements)
    if addition and data.get('plans'):
        previous = data['plans'][-1]
        base_index = previous.get('extension', {}).get('base_plan_index', len(data['plans']) - 1)
        base = data['plans'][base_index]
        base_ids = {a['id'] for a in base['assets']}
        additions = [a for a in assets if a['id'] not in base_ids]
        plan['assets'] = [*deepcopy(base['assets']), *additions]
        plan['requirements']['assets'] = [
            *deepcopy(base.get('requirements', {}).get('assets', base['assets'])),
            *[deepcopy(a) for a in requirements['assets'] if a['id'] not in base_ids]]
        plan['extension'] = {'base_plan_index': base_index,
                             'added_asset_ids': [a['id'] for a in additions]}
        require_deliverables(plan, plan['requirements'])
    data = {**data, 'plans': [*data.get('plans', []), plan],
            'messages': [*data.get('messages', []), {'role': 'assistant',
                'text': plan['reply'], 'plan_index': len(data.get('plans', []))}],
            'planning_recovery': plan['recovery']}
    data.pop('planning_error', None)
    data.pop('pending_candidate', None)
    campaign.data = data
    campaign.version += 1
    campaign.state = 'awaiting_approval'
    return plan
