"""Assemble one saved asset's revision context without reopening the campaign."""
from copy import deepcopy

from app.brand.decisions import current_decisions
from app.llm.prompt_payloads import prompt_decisions


def revision_packages(campaign, plan, asset_id, package):
    from app.llm.asset_context_router import routed_package
    from app.llm.context_collections import assemble_collections
    writing, validation = deepcopy(package), deepcopy(package)
    routing = campaign.data.get('asset_context_routing')
    if routing and asset_id in routing.get('assets', {}):
        originals = plan.get('asset_packages') or {asset_id: package}
        writing = routed_package(package, routing, asset_id, 'writing', originals)
        validation = routed_package(package, routing, asset_id, 'validation', originals)
    else:
        # Legacy assets retain all applicable originals, including uncertain scope.
        for value in (writing, validation):
            evidence = list({p['chunk_id']: p for p in [*value.get('evidence', []),
                *value.get('rule_pack', {}).get('evidence', [])]}.values())
            selected = assemble_collections(evidence, package['rule_pack']['format'], task='writing')['evidence']
            value['evidence'] = selected
            value['rule_pack']['evidence'] = selected
    return writing, validation


def validation_context(campaign, asset_id, asset, instruction):
    return {
        'validation_conversation': [{'role': 'user', 'text': instruction}],
        'current_decisions': prompt_decisions(current_decisions(campaign), asset_id),
        'approved_asset': deepcopy(asset),
        'revision_instruction': instruction,
    }


def focus_revision_context(client, settings, campaign, asset, draft, instruction, writing, validation):
    """Select intact originals once, rather than resending a broad pack per edit step."""

    from pydantic import BaseModel, ConfigDict, Field

    from app.brand.retrieval import RetrievalError
    from app.llm.client import ModelRequest, RunContext
    from app.llm.prompt_cache import cached_prompt, prompt_cache_key

    pool = {p['chunk_id']: p for packet in (writing, validation)
            for p in packet['rule_pack']['evidence']}
    # Small packets already cost less than another selection round trip.
    if sum(len(p['content']) for p in pool.values()) < 18000:
        return writing, validation
    aliases = {f'r{i}': key for i, key in enumerate(sorted(pool))}
    class RevisionSources(BaseModel):
        model_config = ConfigDict(extra='forbid')
        source_ids: list[str] = Field(min_length=1)
    result = client.run(ModelRequest(operation='select_revision_context',
        prompt_cache_key=prompt_cache_key(campaign.brand_id, 'select_revision_context'),
        cache_prefix_messages=1,
        model=settings.magi_retrieval_planner_model,
        instructions='Select the smallest complete set of ORIGINAL source passages needed to edit and independently validate this single draft. '
        'Preserve exact claim wording, factual support, limitations, statistic bases and validity dates, global mandatory rules, '
        'applicable channel length/structure, citations, CTA/link rules, permissions and publication dependencies. '
        'For shortening, do not select optional style essays or unrelated products/channels. '
        'Keep definitions, exceptions and competing rules together; uncertain mandatory restrictions must remain. '
        'Only return supplied source IDs. This selects context, never approves claims or rewrites sources.',
        input=cached_prompt({'asset': asset, 'draft': {k: draft.get(k) for k in ('title', 'body', 'source_ids')},
            'instruction': instruction, 'decisions': prompt_decisions(current_decisions(campaign), asset['id']),
            'sources': [{'id': alias, 'content': pool[key]['content'],
                'path': pool[key].get('relative_path'), 'headings': pool[key].get('heading_path')}
                for alias, key in aliases.items()]}, ('sources',)),
        max_output_tokens=3000, context=RunContext(brand_id=campaign.brand_id,
            campaign_id=campaign.id, asset_id=asset['id'])), output_model=RevisionSources)
    if not set(result.parsed.source_ids) <= aliases.keys():
        raise RetrievalError('Revision source selector returned unknown evidence; original retained')
    refs = {aliases[ref] for ref in result.parsed.source_ids}
    refs.update(ref for ref in draft.get('source_ids', []) if ref in pool)
    refs.update(limit['chunk_id'] for packet in (writing, validation)
        for limit in packet['rule_pack'].get('limits', []) if limit.get('chunk_id') in pool)
    while True:
        dependencies = {key for ref in refs for key in pool[ref].get('dependency_ids', [])}
        if not dependencies <= pool.keys():
            raise RetrievalError('Revision source dependency unavailable; original retained')
        if dependencies <= refs:
            break
        refs.update(dependencies)
    packets = []
    for packet in (writing, validation):
        value = deepcopy(packet)
        value['evidence'] = [deepcopy(pool[key]) for key in sorted(refs)]
        value['rule_pack']['evidence'] = value['evidence']
        value['rule_pack'].pop('writing_evidence', None)
        value.pop('topic', None)
        value.pop('channel', None)
        value['revision_context_run_id'] = result.run_id
        packets.append(value)
    return tuple(packets)


def shortening_budget(draft, package):
    """Make an achievable length contract explicit before spending writer tokens."""
    from app.brand.draft_validation import words
    from app.brand.retrieval import RetrievalError
    original = words(draft['body'])
    minima = [limit['minimum'] for limit in package['rule_pack'].get('limits', [])
              if limit['field'] in ('body', 'page') and limit['unit'] == 'words'
              and limit.get('minimum') is not None]
    minimum = max(minima, default=0)
    # Page limits count the title as well as body.
    for limit in package['rule_pack'].get('limits', []):
        if limit['field'] == 'page' and limit['unit'] == 'words' and limit.get('minimum') == minimum:
            minimum = max(0, minimum - words(draft.get('title') or ''))
    if minimum >= original:
        raise RetrievalError(f'This draft has {original} words and its required minimum is {minimum}. '
                             'It cannot be shortened within the current rules; original retained. Choose a shorter format or resolve the minimum-length requirement.')
    if minimum and original - minimum <= max(10, int(original * .05)):
        raise RetrievalError(f'This draft has {original} words and its required minimum is {minimum}; '
                             f'only {original - minimum} words can be removed. Meaningful shortening needs a shorter format '
                             'or a resolved minimum-length exception. No revision model calls were made; original retained.')
    target = max(minimum + min(20, (original - minimum) // 2), int(original * .7))
    return {'original_body_words': original, 'minimum_body_words': minimum,
            'maximum_body_words': original - 1, 'target_body_words': min(target, original - 1),
            'counting': 'Count headings and visible link text; exclude URL destinations and Markdown punctuation.'}


def edit_saved_copy(client, settings, campaign, draft, instruction):
    """One inexpensive editorial call using only existing copy and the user's edit."""
    import json

    from pydantic import BaseModel, ConfigDict

    from app.llm.client import ModelRequest, RunContext

    class Edit(BaseModel):
        model_config = ConfigDict(extra='forbid')
        title: str
        body: str
        subject: str | None
        preheader: str | None
        sender_name: str | None
        byline: str | None
        requires_conflict_review: bool

    return client.run(ModelRequest(operation='edit_saved_draft', model=settings.magi_writer_model,
        instructions='Edit the supplied saved copy according to the user instruction. '
        'This is an editorial operation, not campaign planning. The user may override length and style defaults. '
        'For make it shorter, remove repetition and substantially reduce length (roughly 20–30% unless a target is given), '
        'even below a channel minimum. Do not pad back to a minimum. Preserve meaning, exact factual claims, '
        'qualifications, citations and URLs unless the user explicitly changes them. Preserve unspecified fields. '
        'Never invent facts, approvals, URLs or names. Return requires_conflict_review=false for editorial wording, '
        'tone, structure and length changes that preserve factual meaning. Return true for new or changed factual '
        'claims, permissions, product capabilities, audience or campaign requirements other than editorial length/style. '
        'When true, the system will use the evidence-review workflow before saving. '
        'Return only the edited fields; images, attachments and publication metadata are preserved by the system.',
        input=json.dumps({'instruction': instruction, 'format': draft['format'],
            'draft': {k: draft.get(k) for k in ('title', 'body', 'subject', 'preheader', 'sender_name', 'byline')}}),
        max_output_tokens=6000, context=RunContext(brand_id=campaign.brand_id,
            campaign_id=campaign.id, asset_id=draft['asset_id'])), output_model=Edit)
