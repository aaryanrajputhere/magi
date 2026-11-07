"""Campaign-wide conflict review, separate from evidence compaction."""
import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, create_model

from app.brand.conflict_coverage import VERSION, current_pool, review_campaign
from app.brand.decisions import DECISION_GUIDANCE, current_decisions, evidence_snapshot
from app.llm.client import ModelRequest
from app.llm.context import RunContext
from app.llm.prompt_payloads import prompt_decisions, semantic_projection


class DecisionMatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    finding_index: int
    decision_id: str | None
    disposition: Literal['new', 'resolved', 'reopen']
    reason: str
    changed_source_ids: list[str]
    changed_user_instruction: str | None


class DecisionMatches(BaseModel):
    model_config = ConfigDict(extra='forbid')
    matches: list[DecisionMatch]


def reconcile_decisions(client, settings, campaign, findings, evidence, messages):
    """Compare issues semantically with answered questions, independent of generated IDs."""
    decisions = [d for d in current_decisions(campaign) if d.get('origin') != 'reviewed_rule']
    if not decisions or not findings:
        return findings
    match = create_model('ReferencedDecisionMatch', __base__=DecisionMatch,
        decision_id=(Literal[tuple(d['id'] for d in decisions)] | None, ...))
    schema = create_model('ReferencedDecisionMatches', __base__=DecisionMatches,
                          matches=(list[match], ...))
    snapshot = evidence_snapshot(evidence)
    changes = {d['id']: {
        'changed_source_ids': [i for i, digest in snapshot.items()
                               if d.get('source_snapshot') and i in d['source_snapshot'] and d['source_snapshot'][i] != digest],
        'later_user_messages': [m.get('text', '')
            for m in messages[d.get('message_count', len(messages)):]
            if m.get('role') == 'user' and m.get('text', '').strip() != d['answer'].strip()]}
        for d in decisions}
    result = client.run(ModelRequest(operation='campaign_decision_match',
        model=settings.magi_retrieval_planner_model,
        instructions=DECISION_GUIDANCE +
        'Match every finding to the existing decision about the SAME subject and scope, even '
        'when the wording, source IDs or asset IDs differ. Return one match per finding index. '
        'An explicit decision about claim wording resolves subsequent findings on the same wording '
        'and scope. Do not label it new. Customer publication permission is a separate subject. '
        'If the candidate fails to apply a clear decision, mark resolved here: the audit will '
        'repair the candidate; do not ask the user again. Reopen only for a genuinely ambiguous '
        'answer, relevant changed evidence or a later user instruction changing that decision. '
        'For changed evidence cite changed_source_ids; for later intent quote changed_user_instruction '
        'exactly. A historical source disagreement is not new evidence. New means no matching decision.',
        input=json.dumps({'findings': semantic_projection(findings), 'decisions': semantic_projection(decisions),
                          'changes_since_decision': changes}), max_output_tokens=4000,
        context=RunContext(brand_id=campaign.brand_id, campaign_id=campaign.id)), output_model=schema)
    matches = {m.finding_index: m for m in result.parsed.matches}
    if set(matches) != set(range(len(findings))) or len(result.parsed.matches) != len(findings):
        raise ValueError('Decision matching did not cover every conflict')
    by_id = {d['id']: d for d in decisions}
    unresolved = []
    for index, finding in enumerate(findings):
        item = matches[index]
        decision = by_id.get(item.decision_id)
        if decision and item.disposition == 'resolved':
            continue
        if decision and item.disposition == 'reopen':
            old = decision.get('source_snapshot', {})
            changed = any(i in snapshot and i in old and snapshot[i] != old[i]
                          for i in item.changed_source_ids)
            later = messages[decision.get('message_count', len(messages)):]
            new_intent = bool(item.changed_user_instruction and any(
                m.get('role') == 'user' and item.changed_user_instruction in m.get('text', '')
                and m.get('text', '').strip() != decision['answer'].strip() for m in later))
            if decision['status'] == 'selected' and not changed and not new_intent:
                continue
            finding = {**finding, 'reopened_decision_id': decision['id'],
                       'explanation': finding['explanation'] + '\nReopened: ' + item.reason}
        unresolved.append(finding)
    return unresolved


def review_conflicts(client, settings, campaign, messages, evidence, candidate=None, *, include_history=True):
    """Review a revision without replacing the campaign planning receipt."""
    result = review_campaign(client, settings, campaign, messages, evidence, candidate=candidate)
    return reconcile_decisions(client, settings, campaign, result['findings'], evidence, messages)


def publish_conflict(campaign, findings):
    history = {f['id']: f for f in campaign.data.get('conflicts', [])}
    for finding in findings:
        history[finding['id']] = finding
    campaign.data = {**campaign.data, 'conflicts': list(history.values())}
    if campaign.data.get('pending_question'):
        return
    finding = next((f for f in history.values() if f['status'] == 'unresolved'), None)
    if finding is None:
        return
    question = {'id': str(uuid4()), 'conflict_id': finding['id'],
                'issue_id': finding.get('issue_id') or finding['id'],
                'text': finding['explanation'] + '\n\n' + finding['question'],
                'kind': 'choices', 'options': finding['options'], 'answer': None,
                'source_snapshot': finding.get('source_snapshot', {}),
                'affected_asset_ids': finding.get('affected_asset_ids', []),
                'message_count': len(campaign.data.get('messages', []))}
    if finding.get('unverified_source_ids'):
        question['text'] += '\n\nSome source references could not be verified. Please review this conflict before deciding.'
    campaign.data.pop('planning_error', None)
    campaign.data = {**campaign.data, 'pending_question': question,
        'questions': [*campaign.data.get('questions', []), question],
        'messages': [*campaign.data.get('messages', []), {'role': 'assistant', 'text': question['text'], 'question': question}]}
    campaign.state = 'clarifying'


def has_conflicts(campaign):
    return any(c['status'] == 'unresolved' for c in campaign.data.get('conflicts', []))


def conflict_intake_key(revision, evidence):
    return hashlib.sha256(json.dumps([VERSION, revision,
                                     evidence_snapshot(evidence)], sort_keys=True).encode()).hexdigest()


def rebind_answered_intake(campaign, revision):
    intake = campaign.data.get('conflict_intake')
    if not intake or campaign.data.get('pending_conflict_question_ids'):
        return
    answered = {f['id'] for f in campaign.data.get('conflicts', []) if f.get('status') == 'answered'}
    if not set(intake.get('issue_ids', [])) <= answered:
        return
    evidence = list(campaign.data.get('evidence_ledger', {}).values())
    key = conflict_intake_key(revision, evidence)
    if intake.get('authority_key'):
        key += ':' + intake['authority_key']
    campaign.data = {**campaign.data, 'conflict_intake': {**intake,
        'requirements_revision': revision, 'revision': key}}


def prepare_conflict_intake(client, settings, campaign, messages, evidence, revision, *, query_groups=None, engine=None, checkpoint=None):
    """Discover a complete question queue once per brief/evidence revision."""
    from app.brand.evidence_ledger import campaign_evidence
    evidence = current_pool(campaign, evidence, query_groups)
    campaign_evidence(campaign, evidence)
    from app.brand.conflict_resolver import active_authority, resolve_question
    authority = active_authority(engine, campaign.brand_id) if engine is not None else None
    key = conflict_intake_key(revision, evidence)
    if engine is not None:
        key += ':' + (authority['id'] if authority else 'no-authority')
    intake = campaign.data.get('conflict_intake', {})
    from app.brand.context import fingerprint
    from app.brand.context_scope import intent, reviewed_choices_only, select_scope
    corpus = fingerprint(engine, campaign.brand_id) if engine is not None else None
    semantic = {'corpus_fingerprint': corpus, 'user_messages': intent(messages), 'source_snapshot': evidence_snapshot(evidence),
        'requirements': {k: v for k, v in (campaign.data.get('requirements') or {}).items()
                         if k not in {'run_id', 'extraction_run_id'}},
        'decisions': prompt_decisions([d for d in current_decisions(campaign)
                                      if d.get('origin') != 'reviewed_rule']),
        'model': settings.magi_conflict_review_model, 'version': VERSION,
        'authority_key': (authority['id'] if authority else 'no-authority')}
    receipt_key = hashlib.sha256(json.dumps(semantic, sort_keys=True).encode()).hexdigest()
    prior_report = campaign.data.get('conflict_coverage', {})
    if prior_report.get('status') not in {'ready', 'needs_decision'}:
        prior_report = intake.get('completed_review', prior_report)
    prior = intake.get('review_receipt')
    reusable = bool(prior_report.get('coverage_challenged')
                    and prior_report.get('status') in {'ready', 'needs_decision'})
    choices_settle_review = bool(prior and reusable and reviewed_choices_only(campaign, prior, messages)
        and all(prior.get(field) == semantic[field] for field in
                ('source_snapshot', 'requirements', 'model', 'version', 'authority_key', 'corpus_fingerprint')))
    same = reusable and (intake.get('receipt_key') == receipt_key or choices_settle_review)
    if same:
        campaign.data = {**campaign.data, 'conflict_intake': {**intake, 'revision': key,
                         'requirements_revision': revision, 'receipt_key': receipt_key,
                         'review_receipt': semantic}, 'conflict_coverage': prior_report}
    if not same and (intake.get('revision') != key or (prior and intake.get('receipt_key') != receipt_key)):
        scoped_ids = None
        if (prior and reusable and prior.get('model') == semantic['model']
                and prior.get('version') == VERSION
                and prior.get('authority_key') == semantic['authority_key']
                and prior.get('corpus_fingerprint') == corpus):
            changed_existing = any(semantic['source_snapshot'].get(k) != v
                                   for k, v in prior['source_snapshot'].items())
            if not changed_existing:
                scoped_ids, full = select_scope(client, settings, campaign, messages, evidence,
                    operation='scope_campaign_changes', prior=prior)
                if full:
                    scoped_ids = None

        campaign.data = {**campaign.data, 'conflict_coverage': {'status': 'incomplete'},
            'conflict_intake': {**intake, 'completed_review': prior_report}}
        if checkpoint is not None:
            checkpoint()
        result = review_campaign(client, settings, campaign, messages, evidence,
                                  query_groups=query_groups, engine=engine,
                                  **({'source_scope_ids': scoped_ids, 'previous_review': prior_report}
                                     if scoped_ids is not None else {}))
        if scoped_ids is not None:
            untouched = [f for f in prior_report.get('findings', [])
                         if not set(f.get('source_ids', [])) & scoped_ids]
            result['findings'] = [*untouched, *result['findings']]
            report = result['report']
            report.update({'scope': 'delta', 'parent_snapshot_key': prior_report.get('snapshot_key'),
                'reviewed_source_ids': sorted(scoped_ids),
                'source_snapshot': semantic['source_snapshot'], 'source_count': len(evidence),
                'findings': result['findings'],
                'treatments': [*prior_report.get('treatments', []), *report.get('treatments', [])],
                'limitations': list(dict.fromkeys([*prior_report.get('limitations', []),
                                                   *report.get('limitations', [])]))})
        campaign.data = {**campaign.data, 'conflict_coverage': result['report']}
        findings = result['findings']
        query_reviews = [{'query_id': 'campaign_wide', 'conflicts': findings}]
        findings = reconcile_decisions(client, settings, campaign, findings, evidence, messages)
        rejected = []
        if engine is not None:
            if settings.magi_parallel_conflict_resolution and len(findings) > 1:
                from copy import deepcopy
                from types import SimpleNamespace

                from app.llm.parallel import run_jobs
                frozen = SimpleNamespace(id=campaign.id, brand_id=campaign.brand_id, data=deepcopy(campaign.data))
                outcomes = run_jobs(client, [lambda child, f=f: resolve_question(
                    engine, child, settings, frozen, f, authority) for f in map(deepcopy, findings)])
                resolved = []
                for value, error in outcomes:
                    if error is not None:
                        raise error
                    resolved.append(value)
                findings = resolved
            else:
                findings = [resolve_question(engine, client, settings, campaign, f, authority) for f in findings]

        history = {f['id']: f for f in campaign.data.get('conflicts', [])}
        history.update({f['id']: f for f in findings})
        campaign.data = {**campaign.data, 'conflicts': list(history.values())}
        campaign.data = {**campaign.data, 'conflict_intake': {
            'revision': key, 'requirements_revision': revision,
            'receipt_key': receipt_key, 'review_receipt': semantic,
            'completed_review': result['report'],
            'authority_key': (authority['id'] if authority else 'no-authority') if engine is not None else None,
            'issue_ids': [finding['id'] for finding in findings], 'query_reviews': query_reviews,
            'rejected_findings': rejected,
            'stage': 'campaign_coverage'}}
        questions = []
        for finding in findings:
            if finding['status'] == 'resolved':
                continue
            questions.append({'id': str(uuid4()), 'conflict_id': finding['id'],
                'issue_id': finding.get('issue_id', finding['id']),
                'text': finding['explanation'] + '\n\n' + finding['question'],
                'kind': 'choices', 'options': finding['options'], 'answer': None,
                'source_snapshot': finding.get('source_snapshot', {}),
                'affected_asset_ids': finding.get('affected_asset_ids', []),
                'message_count': len(campaign.data.get('messages', []))})
        campaign.data = {**campaign.data,
            'questions': [*campaign.data.get('questions', []), *questions],
            'pending_conflict_question_ids': [q['id'] for q in questions],
            'messages': [*campaign.data.get('messages', []),
                {'role': 'assistant', 'text': 'Retrieval conflict review complete.' if findings else
                 'No unresolved conflicts in reviewed evidence. Direct review and independent check completed.'},
                *[{'role': 'assistant', 'text': q['text'], 'question': q} for q in questions]]}
        campaign.state = 'clarifying' if questions else 'draft'

    pending = bool(campaign.data.get('pending_conflict_question_ids'))
    campaign.data = {**campaign.data, 'conflict_coverage': {
        **campaign.data.get('conflict_coverage', {}),
        'status': 'needs_decision' if pending else 'ready'}}
    return pending
