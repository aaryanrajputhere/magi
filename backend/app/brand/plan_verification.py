"""Bind generated plans to completed evidence review for approval."""
from copy import deepcopy
from typing import Literal

from pydantic import Field, ValidationError, create_model, model_validator

from app.brand.conflict_coverage import (
    Closed,
    digest,
    require_ids,
    review_conversation,
    review_decisions,
)
from app.brand.decisions import DECISION_GUIDANCE
from app.brand.retrieval import RetrievalError
from app.llm.client import ModelRequest, ModelRunError
from app.llm.context import RunContext
from app.llm.prompt_cache import cached_prompt, prompt_cache_key
from app.llm.prompt_payloads import planning_review, prompt_decisions, shared_rule_context

PLAN_FIELDS = ('goal', 'assumptions', 'constraints', 'questions')
ASSET_FIELDS = ('purpose', 'angle', 'hook', 'cta', 'audience', 'timing', 'cta_destination', 'cta_destination_kind', 'publish_after_asset_ids')


class PlanDefect(Closed):
    asset_id: str | None = Field(description='Use null ONLY for goal, assumptions, constraints or questions. For purpose, angle, hook, cta, audience, timing, cta_destination, cta_destination_kind or publish_after_asset_ids, use the affected asset ID; never null.')
    field: Literal['goal', 'assumptions', 'constraints', 'questions',
                   'purpose', 'angle', 'hook', 'cta', 'audience', 'timing', 'cta_destination', 'cta_destination_kind', 'publish_after_asset_ids']
    explanation: str = Field(min_length=1)
    severity: Literal['minor', 'material'] = 'material'
    brief_quote: str | None = None
    kind: Literal['incomplete_text', 'content']
    rule_refs: list[str] = Field(description='Relevant chunk IDs; empty for a brief-only defect.')

    @model_validator(mode='after')
    def valid_scope(self):
        if self.field not in (PLAN_FIELDS if self.asset_id is None else ASSET_FIELDS):
            raise ValueError('Defect field does not belong to its campaign or asset scope')
        return self


class PlanCheck(Closed):
    @classmethod
    def model_json_schema(cls, **kwargs):
        schema = super().model_json_schema(**kwargs)
        schema['$defs']['PlanDefect']['required'] = list(schema['$defs']['PlanDefect']['properties'])
        return schema

    reviewed_asset_ids: list[str]
    defects: list[PlanDefect]


class FieldPatch(Closed):
    asset_id: str | None
    field: Literal['goal', 'assumptions', 'constraints', 'questions',
                   'purpose', 'angle', 'hook', 'cta', 'audience', 'timing', 'cta_destination', 'cta_destination_kind', 'publish_after_asset_ids']
    replacement: str | list[str] | None


class PlanPatches(Closed):
    patches: list[FieldPatch] = Field(min_length=1)


def patch_output_schema(defects):
    """Constrain each replacement using the actual outline field contract."""
    from app.api.campaigns import PlannedAsset, PlanOutline
    variants = []
    for index, (asset_id, field) in enumerate(dict.fromkeys(
            (d.asset_id, d.field) for d in defects)):
        fields = PlanOutline.model_fields if asset_id is None else PlannedAsset.model_fields
        info = deepcopy(fields[field])
        info.default = ...
        info.default_factory = None
        variants.append(create_model(f'PlanFieldPatch{index}', __base__=Closed,
            asset_id=(type(None) if asset_id is None else Literal[asset_id], ...),
            field=(Literal[field], ...), replacement=(info.annotation, info)))
    variant = variants[0]
    for item in variants[1:]:
        variant = variant | item
    return create_model('BoundedPlanPatches', __base__=Closed,
                        patches=(list[variant], Field(min_length=1, max_length=len(variants))))


def plan_signature(plan):
    return digest({k: plan.get(k) for k in (*PLAN_FIELDS, 'assets')})


def apply_patches(plan, defects, patches):
    """Only verifier-authorized fields can change; never mutate the saved candidate."""
    allowed = {(d.asset_id, d.field) for d in defects}
    actual = [(p.asset_id, p.field) for p in patches]
    if len(actual) != len(set(actual)) or set(actual) != allowed:
        raise RetrievalError('Plan repair must patch each defective field exactly once')
    result = deepcopy(plan)
    assets = {a['id']: a for a in result['assets']}
    for patch in patches:
        fields = PLAN_FIELDS if patch.asset_id is None else ASSET_FIELDS
        if patch.field not in fields or (patch.asset_id is not None and patch.asset_id not in assets):
            raise RetrievalError('Plan repair targets an invalid field or asset')
        is_list = patch.field in ('assumptions', 'constraints', 'questions', 'publish_after_asset_ids')
        if isinstance(patch.replacement, list) != is_list:
            raise RetrievalError('Plan repair replacement has the wrong field type')
        target = result if patch.asset_id is None else assets[patch.asset_id]
        target[patch.field] = deepcopy(patch.replacement)
    return result


def repair_rule_context(packs, refs):
    """Keep cited chunks and complete source documents for their exceptions."""
    if not refs:
        return packs  # Brief-only semantic defects have no safe source anchor.
    all_passages = [e for pack in packs.values() for e in pack.get('evidence', [])]
    documents = {e['document_id'] for e in all_passages
                 if e['chunk_id'] in refs and e.get('document_id')}
    retained = set(refs)
    retained.update(e['chunk_id'] for e in all_passages if e.get('document_id') in documents)
    # Explicit cross-source dependencies, where available, remain in the packet.
    while True:
        dependencies = {r for e in all_passages if e['chunk_id'] in retained
                        for r in e.get('dependency_ids', [])}
        if dependencies <= retained:
            break
        retained.update(dependencies)
    return {channel: {**pack, 'evidence': [e for e in pack.get('evidence', [])
                                         if e['chunk_id'] in retained]}
            for channel, pack in packs.items()}



def global_rules(packs, protected=()):
    """Keep global authority and factual context, including cross-channel dependencies."""
    from app.brand.evidence_scope import global_source_ids
    ids = global_source_ids([p for pack in packs.values() for p in pack.get('evidence', [])], protected)
    return {channel: {**pack, 'evidence': [p for p in pack.get('evidence', []) if p['chunk_id'] in ids]}
            for channel, pack in packs.items()}


def _verify_plan(client, settings, campaign, messages, plan, *, initial_defects=None):
    """Verify every asset, patch only defects, then independently check the changes."""
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
    packs = {}
    for package in plan.get('asset_packages', {}).values():
        pack = package.get('rule_pack')
        if not pack:
            continue
        channel = pack['format']
        current = packs.setdefault(channel, {**pack, 'evidence': []})
        originals = {p['chunk_id']: p for p in current['evidence']}
        originals.update({p['chunk_id']: p for p in pack.get('evidence', [])})
        current['evidence'] = list(originals.values())
    from app.brand.evidence_scope import select_stage
    protected_ids = {ref for finding in campaign.data.get('conflicts', []) for ref in finding.get('source_ids', [])}
    protected_ids.update(ref for issue in (initial_defects or []) for ref in issue.get('rule_refs', []))
    scope_ids, scope_audit = select_stage([p for pack in packs.values() for p in pack.get('evidence', [])],
                                        {a['format'] for a in plan['assets']}, protected=protected_ids)
    packs = {channel: {**pack, 'evidence': [p for p in pack.get('evidence', []) if p['chunk_id'] in scope_ids]}
             for channel, pack in packs.items()}
    campaign._verification_attempts.append({'stage': 'initial_scope', 'status': 'completed', **scope_audit})
    review = planning_review({k: v for k, v in report.items() if k not in ('source_snapshot', 'findings')})
    decisions = prompt_decisions(review_decisions(campaign))
    lean = {k: deepcopy(plan.get(k)) for k in (*PLAN_FIELDS, 'assets')}
    history = []

    def check(candidate, rules, asset_ids, prior_defects=None, *, scope="all", build_only=False, prepared_result=None):
        supplied = shared_rule_context(rules, stable_refs=True)
        allow_global = scope != "channel" and (prior_defects is None or any(d.asset_id is None for d in prior_defects))
        payload = {'governing_rules': supplied, 'review': review,
                   'conversation': review_conversation(messages),
                   'current_decisions': decisions, 'candidate': candidate}
        if scope != 'all':
            payload['review_scope'] = scope
            payload['target_asset_ids'] = asset_ids
            payload['dependency_candidate'] = lean
        if prior_defects is not None:
            payload['change_scope'] = campaign._verification_change_scope
            payload['repaired_defects'] = [d.model_dump() for d in prior_defects]
            payload['unchanged_assets'] = [a for a in lean['assets'] if a['id'] not in asset_ids]
        request = ModelRequest(operation='campaign_plan_verification', timeout_seconds=120,
            prompt_cache_key=prompt_cache_key(campaign.brand_id, 'campaign_plan_verification'),
            cache_prefix_messages=1,
            model=settings.magi_retrieval_planner_model,
            instructions=DECISION_GUIDANCE +
            'Verify this candidate against the original brief and resolved conflicts for EVERY supplied asset. '
            'Apply resolved treatments. Return each supplied asset ID exactly once. Check exact required '
            'wording, imagery, live versus scheduled posting, dates, audiences and draft-only instructions. '
            'Check CTA target availability against the campaign sequence in the initial review. '
            'Distinguish recommended skeleton budgets/planning targets from absolute publication limits; '
            'preserve both when applicable rather than treating them as contradictory rules. '
            'Do not reopen resolved issues or invent requirements. Check naming, claims and channel '
            'alignment against governing_rules.channels. Each evidence_group references source_groups, '
            'whose short references identify evidence.ref. Cite original evidence.chunk_id in rule_refs. '
            'Return actionable defects only, with the exact editable field and asset_id (null for campaign '
            'fields). IDs and formats cannot change. Proposed audience and timing may be patched coherently; explicit requirements must remain intact. '
            'Use kind incomplete_text only for an unfinished sentence that can be completed from existing '
            'candidate facts; otherwise use content. Cite relevant original rule chunk IDs when applicable. '
            'Do not demand full draft copy in an outline. For repaired_defects, check the changed fields '
            'and their dependencies, including unchanged_assets and any new unsupported claims '
            'introduced by the patch. unchanged_assets supply context, not additional review targets. '
            'A missing passage in this focused request does not establish that a previously supported '
            'constraint is unsupported. Never remove an unchanged rule for missing selected evidence. '
            'For a recheck, flag unchanged fields only when the patch creates a new violation or '
            'changes their meaning; do not restart a general outline review. '
            'Check full product naming on first audience-facing mention in hooks. '
            'Return ALL findings in the initial review, including every CTA dependency and audience inconsistency. '
            'severity=material only for unsupported or contradicted claims, permission violations, unresolved brief conflicts or explicit requirement violations; cite original rule_refs or an exact user brief_quote. Set brief_quote=null otherwise. '
            'Editorial refinements and inconsistencies in proposed choices are minor. Do not report optional advice. '
            'CTA is a short label; cta_destination_kind is asset, url or on_page_form. A landing page may use its own demo form without a URL. publish_after_asset_ids records sequencing. Cite immutable chunk IDs, never stage-local s-number aliases. '
            'Return defects: [] when compliant.',
            input=cached_prompt(payload, ('governing_rules',)), max_output_tokens=6000,
            context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id))
        if scope != 'all':
            request = request.model_copy(update={'instructions': request.instructions +
                (' Review campaign-wide brief, decisions and cross-asset dependencies ONLY. Return only asset_id=null findings.'
                 if scope == 'global' else
                 ' Review only target_asset_ids. Return no campaign-level findings. dependency_candidate is read-only cross-asset context.'),
                'operation': 'campaign_plan_verification_' + scope})
        if build_only:
            return request
        campaign._verification_stage = 'confirmation' if prior_defects is not None else 'initial_review'
        result = prepared_result or client.run(request, output_model=PlanCheck)
        campaign._verification_last_run = result.run_id
        campaign._verification_attempts.append({'stage': campaign._verification_stage, 'run_id': result.run_id, 'status': 'completed'})
        require_ids(result.parsed.reviewed_asset_ids, asset_ids, 'plan assets')
        known_refs = {e['chunk_id'] for p in rules.values() for e in p.get('evidence', [])}
        packet = shared_rule_context(rules, stable_refs=True)
        aliases = {e['ref']: e['chunk_id'] for e in packet['evidence']}
        for defect in result.parsed.defects:
            defect.rule_refs = [aliases.get(ref, ref) for ref in defect.rule_refs]
        valid_defects = []
        rejected = []
        for defect in result.parsed.defects:
            if (defect.severity == 'material' and 'severity' in defect.model_fields_set
                    and not defect.rule_refs and not (defect.brief_quote and any(
                        defect.brief_quote in m.get('text', '') for m in messages if m.get('role') == 'user'))):
                rejected.append(defect.model_dump())
                continue
            fields = PLAN_FIELDS if defect.asset_id is None else ASSET_FIELDS
            if ((scope == 'global' and defect.asset_id is not None) or (defect.asset_id is None and not allow_global) or defect.field not in fields
                    or (defect.asset_id is not None and defect.asset_id not in asset_ids)
                    or not set(defect.rule_refs) <= known_refs
                    or (defect.kind == 'incomplete_text' and defect.field not in
                        ('goal', 'purpose', 'angle', 'hook', 'cta'))):
                rejected.append(defect.model_dump())
                continue
            valid_defects.append(defect)
        campaign._verification_issues = [d.model_dump() for d in valid_defects]
        if rejected:
            history.append({'run_id': result.run_id, 'defects': campaign._verification_issues, 'rejected_defects': rejected})
            campaign._verification_history = deepcopy(history)
            raise RetrievalError('Plan verifier returned an invalid defect target or reference')
        history.append({'run_id': result.run_id, **result.parsed.model_dump()})
        campaign._verification_history = deepcopy(history)
        campaign._verification_issues = [d.model_dump() for d in result.parsed.defects]
        return result.parsed.defects

    def unresolved(candidate, issues, reason):
        return finish_review(candidate, issues, history, report, reason=reason)

    from app.brand.plan_checks import mechanical_findings, merge_findings
    def parallel_initial():
        from types import SimpleNamespace

        from app.llm.parallel import run_jobs
        targets = [(lean, global_rules(packs, protected_ids), expected, 'global')]
        for channel in sorted({a['format'] for a in lean['assets']}):
            assets = [a for a in lean['assets'] if a['format'] == channel]
            targets.append(({**lean, 'assets': assets},
                            {channel: packs[channel]} if channel in packs else {},
                            [a['id'] for a in assets], 'channel'))
        requests = [check(c, rules, ids, scope=scope, build_only=True)
                    for c, rules, ids, scope in targets]
        receipts = dict(campaign.data.get('verification_checks', {}))
        signatures = [digest({'version': 'parallel_verify_v1', 'request': r.model_dump(mode='json'),
                              'schema': PlanCheck.model_json_schema()}) for r in requests]
        def job(request, signature):
            def run(child):
                if signature in receipts:
                    saved = receipts[signature]
                    return SimpleNamespace(run_id=saved['run_id'], parsed=PlanCheck.model_validate(saved['result']))
                return child.run(request, output_model=PlanCheck)
            return run
        outcomes = run_jobs(client, [job(r, key) for r, key in zip(requests, signatures)])
        gathered, failures = [], []
        for target, signature, (result, error) in zip(targets, signatures, outcomes):
            if error is not None:
                if not isinstance(error, (ModelRunError, RetrievalError, ValidationError)):
                    raise error
                failures.append(str(error))
                campaign._verification_attempts.append({'stage': 'initial_review', 'scope': target[3],
                    'status': 'failed', 'run_id': getattr(error, 'run_id', None), 'error': str(error)})
                continue
            candidate, rules, ids, scope = target
            try:
                valid = check(candidate, rules, ids, scope=scope, prepared_result=result)
                receipts[signature] = {'run_id': result.run_id, 'result': result.parsed.model_dump()}
                gathered.extend(d.model_dump() for d in valid)
            except RetrievalError as exc:
                gathered.extend(campaign._verification_issues)
                failures.append(str(exc))
        campaign.data = {**campaign.data, 'verification_checks': receipts}
        campaign._verification_issues = merge_findings([
            *gathered, *mechanical_findings(plan, messages)])
        if failures:
            raise RetrievalError('Plan verifier parallel checks incomplete: ' + '; '.join(failures))
        return [PlanDefect.model_validate(d) for d in campaign._verification_issues]

    defects = ([PlanDefect.model_validate(d) for d in initial_defects]
               if initial_defects is not None else
               parallel_initial() if settings.magi_parallel_verification else check(lean, packs, expected))
    defects = [PlanDefect.model_validate(d) for d in merge_findings([
        *defects, *mechanical_findings(plan, messages)])]
    campaign._verification_issues = [d.model_dump() for d in defects]
    for repair_round in range(1):
        if not defects:
            break
        affected = {d.asset_id for d in defects if d.asset_id is not None}
        global_change = any(d.asset_id is None for d in defects)
        formats = {a['format'] for a in lean['assets'] if a['id'] in affected}
        refs = {ref for d in defects for ref in d.rule_refs}
        formats.update(k for k, pack in packs.items() if any(
            e['chunk_id'] in refs for e in pack.get('evidence', [])))
        rules = packs if global_change else {k: v for k, v in packs.items() if k in formats}
        candidate = {**lean, 'assets': lean['assets'] if global_change else
                     [a for a in lean['assets'] if a['id'] in affected]}
        # Sentence completion needs existing facts, not another copy of the corpus.
        # Content changes retain all affected channel rules, including exceptions.
        repair_rules = ({} if all(d.kind == 'incomplete_text' for d in defects)
                        else repair_rule_context(rules, refs))
        campaign._verification_stage = 'repair'
        repaired = client.run(ModelRequest(operation='campaign_plan_coverage_repair',
            prompt_cache_key=prompt_cache_key(campaign.brand_id, 'campaign_plan_coverage_repair'),
            cache_prefix_messages=1,
            model=settings.magi_retrieval_planner_model,
            instructions=DECISION_GUIDANCE +
            'Return one field patch per distinct (asset_id, field) in defects. Never return a whole plan. '
            'Preserve IDs and all unrelated fields. Do not introduce optional statistics, claims, URLs '
            'or examples to repair a hook. Prefer a simple non-factual opening using existing intent. '
            'If retaining a statistic, preserve its approved wording, bracket citation and required '
            'survey base together. Repair only the concrete defects; use existing '
            'candidate facts for incomplete_text, without introducing new claims. For content defects '
            'apply original governing rules including exceptions. Return an outline, never full drafts. '
            'Do not resolve new conflicts or invent exceptions. Shared evidence is referenced by '
            'governing_rules.channels.*.evidence_group references source_groups, whose entries identify evidence.ref. Campaign list fields require list replacements; '
            'Follow each field replacement schema, including nullable fields and dependency lists. CTA labels must not contain sequencing instructions or URLs. Repair related audience, purpose, timing and dependency fields coherently. Respect the replacement schema character '
            'limits. Rewrite concisely as complete sentences; never truncate words or sentences.',
            input=cached_prompt({'candidate': candidate, 'governing_rules': shared_rule_context({k: {field: value for field, value in v.items()
                    if field in ('format', 'limits', 'evidence')} for k, v in repair_rules.items()}, stable_refs=True),
                'unchanged_assets': [a for a in lean['assets'] if a['id'] not in affected]
                                    if not global_change else [],
                'defects': [d.model_dump() for d in defects], 'review': review,
                'conversation': review_conversation(messages),
                'requirements': ({**requirements, 'assets': [a for a in requirements['assets']
                    if global_change or a['id'] in affected]} if requirements is not None else None),
                'current_decisions': decisions}, ('governing_rules',)),
            max_output_tokens=4000,
            context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id)),
            output_model=patch_output_schema(defects))
        campaign._verification_last_run = repaired.run_id
        campaign._verification_attempts.append({'stage': 'repair', 'run_id': repaired.run_id, 'status': 'completed'})
        patched = apply_patches(lean, defects, repaired.parsed.patches)
        if requirements is not None and not patched.get('questions'):
            require_deliverables({**plan, **patched}, requirements)
        if patched == lean:
            return unresolved(plan, defects, 'Repair made no changes; the candidate is saved for correction.')
        # Validate the complete outline and contract before any approval receipt exists.
        try:
            validated = requirement_bound_plan_schema(requirements).model_validate(patched).model_dump()
        except ValidationError as exc:
            details = '; '.join('.'.join(map(str, e['loc'])) + ': ' + e['msg']
                                for e in exc.errors(include_input=False))
            raise RetrievalError('Plan repair failed field validation: ' + details) from exc
        for item in validated['assets']:
            original = next(a for a in patched['assets'] if a['id'] == item['id'])
            for key in ('cta_destination', 'cta_destination_kind', 'publish_after_asset_ids'):
                if key not in original and not item.get(key):
                    item.pop(key, None)
        plan = {**plan, **validated}
        campaign._verification_candidate = deepcopy(plan)
        if campaign._verification_checkpoint:
            campaign._verification_checkpoint(plan)
        if plan['questions']:
            # A clarification is a valid workflow outcome, not a malformed plan.
            # Preserve the candidate for inspection but never issue an approval receipt.
            plan['deferred_candidate'] = deepcopy(validated)
            # Keep the candidate visible while clarification remains blocking.
            plan.pop('conflict_verification', None)
            plan.pop('conflict_review_key', None)
            return finish_review(plan, [PlanDefect(asset_id=None, field='questions',
                explanation='Resolve the essential audience or brief decision.', severity='material',
                kind='content', rule_refs=[])], history, report)
        if requirements is not None:
            require_deliverables(plan, requirements)
        changed_constraints = [item for item in validated['constraints'] if item not in lean['constraints']]
        campaign._verification_change_scope = {'changed_constraint_entries': changed_constraints,
            'removed_constraint_entries': [item for item in lean['constraints'] if item not in validated['constraints']]}
        campaign._verification_attempts.append({'stage': 'field_diff', 'status': 'completed',
            'changed_constraint_entries': changed_constraints,
            'removed_constraint_entries': [item for item in lean['constraints'] if item not in validated['constraints']]})
        recheck = {**validated, 'assets': validated['assets'] if global_change else
                   [a for a in validated['assets'] if a['id'] in affected]}
        from app.brand.evidence_scope import focused_rules
        recheck_rules, scope_reason = focused_rules(rules, refs)
        campaign._verification_attempts.append({'stage': 'confirmation_scope', 'status': 'completed',
            'reason': scope_reason, 'retained_source_ids': sorted({p['chunk_id'] for pack in recheck_rules.values() for p in pack.get('evidence', [])})})
        remaining = check(recheck, recheck_rules, [a['id'] for a in recheck['assets']], defects)
        remaining = [PlanDefect.model_validate(d) for d in merge_findings([*remaining, *mechanical_findings(plan, messages)])]
        if remaining:
            return unresolved(plan, remaining, 'Plan still needs correction after bounded repair.')
        break
    return finish_review(plan, [], history, report, attempts=(
        [{'stage': 'repair', 'status': 'skipped'}, {'stage': 'confirmation', 'status': 'skipped'}]
        if len(history) == 1 else []))


def finish_review(plan, issues, history, report, *, status=None, attempts=None, reason=''):
    from app.brand.plan_checks import merge_findings
    plan = deepcopy(plan)
    findings = merge_findings(issues)
    blockers = [d for d in findings if d.get('severity', 'material') == 'material']
    plan.pop('recovery', None)
    plan.pop('conflict_verification', None)
    plan['conflict_review_key'] = report['snapshot_key']
    plan['plan_review'] = {'status': status or ('issues_remaining' if findings else 'passed'),
        'signature': plan_signature(plan), 'findings': findings, 'approval_blockers': blockers,
        'history': deepcopy(history), 'attempts': attempts or [], 'reason': reason}
    if blockers:
        plan['recovery'] = {'status': 'review_required', 'reason': reason or 'Material plan issues remain.',
                            'issues': blockers, 'history': deepcopy(history)}
    if plan['plan_review']['status'] == 'passed':
        plan['conflict_verification'] = {'signature': plan_signature(plan),
            'history': deepcopy(history), 'method': 'collect_patch_confirm'}
    return plan


def verify_plan(client, settings, campaign, messages, plan, *, initial_defects=None, checkpoint=None):
    """Optional review failures never erase a valid candidate or known blockers."""
    from app.api.campaigns import PlanOutline
    from app.brand.campaign_requirements import require_deliverables
    from app.brand.plan_checks import mechanical_findings, merge_findings
    from app.llm.client import ModelRunError
    report = campaign.data.get('conflict_coverage', {})
    if report.get('status') != 'ready' or not report.get('coverage_challenged'):
        raise RetrievalError('Complete conflict coverage before proposing a plan for approval')
    PlanOutline.model_validate({k: plan[k] for k in (*PLAN_FIELDS, 'assets')})
    if plan.get('requirements') is not None:
        require_deliverables(plan, plan['requirements'])
    campaign._verification_candidate = deepcopy(plan)
    campaign._verification_issues = merge_findings([*(initial_defects or []), *mechanical_findings(plan, messages)])
    campaign._verification_history = []
    campaign._verification_stage = 'initial_review'
    campaign._verification_last_run = None
    campaign._verification_attempts = []
    campaign._verification_change_scope = None
    campaign._verification_checkpoint = checkpoint
    if checkpoint:
        checkpoint(plan)
    try:
        reviewed = _verify_plan(client, settings, campaign, messages, plan, initial_defects=initial_defects)
        reviewed['plan_review']['attempts'] = [*campaign._verification_attempts, *reviewed['plan_review']['attempts']]
        return reviewed
    except (ModelRunError, RetrievalError, ValidationError) as exc:
        # Only expected model-output errors are recoverable; requirement/coverage
        # preconditions and persistence errors must not become approval success.
        if isinstance(exc, RetrievalError) and not str(exc).startswith((
                'Plan verifier', 'Plan repair', 'Incomplete conflict review: plan assets', 'Plan does not match saved campaign requirements:', 'Invalid plan assets', 'Unknown plan assets',
                'Missing plan assets', 'Duplicate plan assets')):
            raise
        candidate = campaign._verification_candidate
        if isinstance(exc, RetrievalError) and str(exc).startswith('Plan does not match saved campaign requirements:'):
            for issue in campaign._verification_issues:
                issue['severity'] = 'material'
        issues = merge_findings([*campaign._verification_issues, *mechanical_findings(candidate, messages)])
        return finish_review(candidate, issues, campaign._verification_history, report,
            status='incomplete', reason='Optional plan verification did not complete.',
            attempts=[*campaign._verification_attempts, {'stage': campaign._verification_stage, 'status': 'failed',
                       'run_id': getattr(exc, 'run_id', campaign._verification_last_run), 'error': str(exc)},
                      {'stage': 'remaining_review_steps', 'status': 'skipped'}])
    finally:
        for key in ('_verification_candidate', '_verification_issues', '_verification_history',
                    '_verification_stage', '_verification_change_scope', '_verification_checkpoint', '_verification_last_run', '_verification_attempts'):
            delattr(campaign, key)


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
            or (plan.get('conflict_verification', {}).get('signature') != plan_signature(plan)
                and not (plan.get('plan_review', {}).get('status') in {'passed', 'incomplete', 'issues_remaining'}
                    and plan['plan_review'].get('signature') == plan_signature(plan)
                    and not plan['plan_review'].get('approval_blockers')))):
        return 'The current plan needs conflict review and verification before approval'
    return None
