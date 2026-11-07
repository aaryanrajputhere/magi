"""Lossless evidence deduplication for asset prompts."""
import re
from copy import deepcopy


def planning_review(review):
    """Project audit records without touching their stored form or evidence text."""
    allowed = {'status', 'limitations', 'blocking_gaps', 'treatments', 'findings',
               'conflicts', 'id', 'affected_asset_ids', 'decision_type', 'explanation',
               'grounding', 'source_ids', 'source_refs', 'source_quotes', 'statements',
               'requires_user_decision', 'treatment', 'question', 'options', 'answer',
               'resolution', 'origin', 'source_ref', 'quote', 'text', 'content',
               'chunk_id', 'relative_path', 'heading_path', 'start_line', 'end_line',
               'decision', 'approved_wording', 'reason', 'kind', 'message'}

    def project(value):
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items() if key in allowed}
        if isinstance(value, list):
            return [project(item) for item in value]
        return deepcopy(value)

    return project(review)


def planning_research(research):
    if not isinstance(research, dict):
        return deepcopy(research)
    result = {key: deepcopy(research[key]) for key in ('status', 'reason') if key in research}
    bodies = {}
    sources = []
    for source in research.get('sources', []):
        item = {key: deepcopy(source[key]) for key in
                ('id', 'url', 'title', 'truncated', 'retrieved_at') if key in source}
        if 'content' in source or source.get('content_ref') in research.get('contents', {}):
            body = (source['content'] if 'content' in source else
                    research['contents'][source['content_ref']])
            if body not in bodies:
                bodies[body] = 'research-' + str(len(bodies))
            item['content_ref'] = bodies[body]
        sources.append(item)
    result['sources'] = sources
    result['contents'] = {ref: body for body, ref in bodies.items()}
    return result


def stable_source_ref(chunk_id):
    """Stable transport label; citations still use the original immutable chunk ID."""
    import hashlib
    return 'r' + hashlib.sha256(chunk_id.encode()).hexdigest()[:12]


def prompt_decisions(decisions, asset_id=None):
    """Execution needs instructions, not the evidence hashes used to invalidate them."""
    return [{k: deepcopy(v) for k, v in decision.items()
             if k in {'id', 'origin', 'question', 'answer', 'resolution', 'affected_asset_ids'}}
            for decision in decisions
            if asset_id is None or not decision.get('affected_asset_ids')
            or asset_id in decision['affected_asset_ids']]


def writing_package(package, task='writing'):
    """Select intact original chunks; never substitute generated guideline text."""
    result = lean_package(package)
    pack = package.get('rule_pack') or {}
    channel = pack.get('format') or package.get('requirement', {}).get('format')
    from app.llm.context_collections import assemble_collections
    assembled = assemble_collections(result['evidence'], channel, task=task)
    result.update(assembled)
    result['rule_pack'] = {'format': channel, 'limits': deepcopy(pack.get('limits', []))}
    result.pop('channel', None)
    # Reports can duplicate source text; facts remain in the selected original chunks.
    if result.get('topic', {}).get('report'):
        result['topic']['report'].pop('supported', None)
    return result


def original_passages(evidence, channel):
    """Exclude explicitly unrelated channel/visual sections, retaining uncertain scope."""
    channel_names = {'linkedin': 'linkedin', 'blog': 'blog', 'email': 'email',
                     'x': 'x', 'landing_page': 'landing'}
    selected = []
    for passage in evidence:
        path = passage.get('relative_path', '').lower()
        headings = passage.get('heading_path') or []
        scope = ' '.join(headings).lower()
        other = any(name != channel and re.search(r'\b' + re.escape(label) + r'\b', scope)
                    for name, label in channel_names.items())
        own = bool(channel and re.search(r'\b' + re.escape(channel_names.get(channel, channel)) + r'\b', scope))
        # Only prune clearly channel-scoped chunks. Unknown applicability stays.
        if other and not own and ('/channels/' in path or '/templates/' in path):
            continue
        selected.append(deepcopy(passage))
    return sorted(selected, key=lambda e: e['chunk_id'])


def lean_package(package):
    """Keep passages once; replace verbatim report copies with source references."""
    result = deepcopy(package)
    evidence = list({e['chunk_id']: e for e in [
        *package.get('evidence', []), *(package.get('rule_pack') or {}).get('evidence', [])
    ]}.values())
    contents = {e['content']: e['chunk_id'] for e in evidence}
    if result.get('rule_pack'):
        result['rule_pack'].pop('evidence', None)
        result['rule_pack'].pop('writing_evidence', None)
    for name in ('topic', 'channel'):
        track = result.get(name, {})
        track.pop('evidence', None)
        report = track.get('report', {})
        for entry in report.get('supported', []):
            if entry.get('text') in contents:
                entry['source_ids'] = list(dict.fromkeys([*entry.get('source_ids', []), contents[entry.pop('text')]]))
    result['evidence'] = evidence
    result.pop('image_catalog', None)
    return result


def image_guidance(package):
    """Visual, voice, and channel rules plus campaign intent; exclude product corpus."""
    if not package or 'evidence' not in package:
        return package
    result = lean_package(package)
    result['evidence'] = [e for e in result['evidence'] if not any(
        part in e.get('relative_path', '') for part in ('/products/', '/case-studies/', '/research/'))
        or re.search(r'\b(image|images|imagery|screenshot|screenshots|photo|photograph|logo|hero|illustration|visual)\b',
                     e.get('content', ''), re.IGNORECASE)]
    retained = {e['chunk_id'] for e in result['evidence']}
    for name in ('topic', 'channel'):
        report = result.get(name, {}).get('report', {})
        report['supported'] = [entry for entry in report.get('supported', [])
            if entry.get('text') or set(entry.get('source_ids', [])) & retained]
    result.pop('rule_pack', None)
    return result


def lean_plan_context(context):
    """Share identical rule packs and passages across planned assets."""
    import hashlib
    import json
    result = deepcopy(context)
    result.pop('source_catalog', None)
    if isinstance(result.get('resolved_review'), dict):
        result['resolved_review'].pop('source_snapshot', None)
    evidence = {e['chunk_id']: deepcopy(e) for e in context.get('evidence', [])}
    packs = {}
    for asset_id, package in context.get('asset_packages', {}).items():
        lean = writing_package(package, task='planning')
        passages = lean.pop('evidence')
        local_ids = {e['ref']: e['chunk_id'] for e in passages}
        lean['guideline_collections'] = {k: [local_ids[ref] for ref in refs]
            for k, refs in lean.get('guideline_collections', {}).items()}
        evidence.update({e['chunk_id']: e for e in passages})
        lean['evidence_ids'] = [e['chunk_id'] for e in passages]
        pack = lean.pop('rule_pack', None)
        if pack:
            key = hashlib.sha256(json.dumps(pack, sort_keys=True).encode()).hexdigest()[:16]
            packs[key] = pack
            lean['rule_pack_ref'] = key
        result['asset_packages'][asset_id] = lean
    result['evidence'] = list(evidence.values())
    result['rule_packs'] = packs
    compact_source_groups(result, result.get('asset_packages', {}))
    # Campaign-wide fields are identical across asset packages. Keep one copy,
    # while retaining differing asset-specific values in their own packages.
    packages = list(result.get('asset_packages', {}).values())
    shared = {}
    for field in ('campaign_topic', 'campaign_context', 'campaign_constraints'):
        if packages and field in packages[0] and all(
                p.get(field) == packages[0][field] for p in packages):
            shared[field] = packages[0][field]
            for package in packages:
                package.pop(field, None)
    if shared:
        result['shared_asset_context'] = shared
    return result


def shared_rule_context(packs, *, stable_refs=False):
    """Keep all original rules once, with channel membership by reference."""
    evidence, channels = {}, {}
    for channel, pack in packs.items():
        passages = original_passages(pack.get('evidence', []), channel)
        for passage in passages:
            key = passage['chunk_id']
            if key in evidence and evidence[key] != passage:
                raise ValueError('Conflicting rule evidence for chunk ' + key)
            evidence[key] = deepcopy(passage)
        channels[channel] = {k: deepcopy(v) for k, v in pack.items()
                             if k in {'format', 'limits'}}
        channels[channel]['evidence_ids'] = list(dict.fromkeys(
            passage['chunk_id'] for passage in passages))
    result = {'channels': channels, 'evidence': list(evidence.values())}
    compact_source_groups(result, channels)
    if stable_refs:
        if len({stable_source_ref(e['chunk_id']) for e in result['evidence']}) != len(result['evidence']):
            raise ValueError('Stable source reference collision')
        aliases = {e['ref']: stable_source_ref(e['chunk_id']) for e in result['evidence']}
        for e in result['evidence']:
            e['ref'] = stable_source_ref(e['chunk_id'])
        result['source_groups'] = {k: [aliases[r] for r in refs] for k, refs in result['source_groups'].items()}
    return result


def compact_source_groups(payload, members):
    """Short transport references; original citation IDs and source text stay intact."""
    aliases = {e['chunk_id']: f's{i}' for i, e in enumerate(payload['evidence'])}
    payload['evidence'] = [
        {'ref': aliases[e['chunk_id']], **{k: deepcopy(v) for k, v in e.items()
            if k in ('chunk_id', 'content', 'relative_path', 'heading_path')}}
        for e in payload['evidence']]
    groups = {}
    for member in members.values():
        if member.get('guideline_collections'):
            member['guideline_collections'] = {k: [aliases[i] for i in ids]
                for k, ids in member['guideline_collections'].items()}
        ids = tuple(dict.fromkeys(member.pop('evidence_ids', [])))
        if ids not in groups:
            groups[ids] = f'g{len(groups)}'
        member['evidence_group'] = groups[ids]
    payload['source_groups'] = {name: [aliases[i] for i in ids] for ids, name in groups.items()}


def draft_prompt(package, task, shared=None):
    """Necessary originals in stable brand/channel/fact messages, with durable refs.

    Never expand the selected evidence to manufacture a shared prefix. Every
    source occurs once; changing asset metadata and limits follow the originals.
    """
    import json

    from app.llm.context_collections import classify

    packet = writing_package(package)
    groups = {'brand_rules': [], 'channel_rules': [], 'asset_sources': []}
    aliases = {}
    for passage in packet.pop('evidence', []):
        category = classify(passage)
        name = ('channel_rules' if category == 'channel' else
                'brand_rules' if category in ('voice', 'writing', 'general') else 'asset_sources')
        if shared is not None:
            channel = packet.get('rule_pack', {}).get('format')
            name = ('brand_rules' if passage['chunk_id'] in shared['brand'] else
                    'channel_rules' if passage['chunk_id'] in shared['channels'].get(channel, [])
                    else 'asset_sources')
        aliases[passage.get('ref', passage['chunk_id'])] = passage['chunk_id']
        groups[name].append({**{k: deepcopy(v) for k, v in passage.items()
            if k in {'chunk_id', 'content', 'relative_path', 'heading_path', 'dependency_ids', 'start_line', 'end_line'}},
            'ref': passage['chunk_id']})
    packet['guideline_collections'] = {k: [aliases[ref] for ref in refs]
        for k, refs in packet.get('guideline_collections', {}).items()}
    return [*({'role': 'user', 'content': json.dumps(
                {name: {'evidence': sorted(passages, key=lambda p: p['chunk_id'])}}, sort_keys=True)}
              for name, passages in groups.items()),
            {'role': 'user', 'content': json.dumps({**task, 'package': packet}, sort_keys=True)}]


def planning_prompt(context, task):
    """One original dictionary and short transport refs, without audit snapshots."""
    from app.llm.prompt_cache import cached_prompt
    from app.brand.conflict_coverage import review_conversation
    context = deepcopy(context)
    if 'resolved_review' in context:
        context['resolved_review'] = planning_review(context['resolved_review'])
    if 'web_research' in context:
        context['web_research'] = planning_research(context['web_research'])
    sources = sorted(context.pop('evidence', []), key=lambda p: p['chunk_id'])
    if len({stable_source_ref(p['chunk_id']) for p in sources}) != len(sources):
        raise ValueError('Stable source reference collision')
    aliases = {p['ref']: stable_source_ref(p['chunk_id']) for p in sources}
    for i, passage in enumerate(sources):
        passage['ref'] = stable_source_ref(passage['chunk_id'])
    context['source_groups'] = {key: [aliases.get(ref, ref) for ref in refs]
        for key, refs in context.get('source_groups', {}).items()}
    for asset_id, package in context.get('asset_packages', {}).items():
        # source_groups already supplies the complete selected membership.
        package.pop('guideline_collections', None)
        if package.get('requirement') and context.get('requirements'):
            package.pop('requirement')
            package['requirement_ref'] = asset_id
    if context.get('requirements'):
        context['requirements'] = {k: v for k, v in context['requirements'].items()
                                   if k not in {'run_id', 'extraction_run_id'}}
        for asset in context['requirements'].get('assets', []):
            asset['provenance'] = [{k: v for k, v in item.items() if k in {'field', 'origin'}}
                                   for item in asset.get('provenance', [])]
    task = deepcopy(task)
    if 'conversation' in task:
        task['conversation'] = review_conversation(task['conversation'])
    return cached_prompt({'original_sources': sources, 'brand_context': context, **task},
                         ('original_sources',))


def shared_draft_groups(packages):
    """Intersect already-selected originals; never add a source for cache reuse."""
    from app.llm.context_collections import classify
    prepared = [writing_package(p) for p in packages.values()]
    brand_sets = [{e['chunk_id'] for e in p['evidence']
                   if classify(e) in ('voice', 'writing', 'general')} for p in prepared]
    channels = {}
    for packet in prepared:
        channel = packet.get('rule_pack', {}).get('format')
        channels.setdefault(channel, []).append({e['chunk_id'] for e in packet['evidence']
                                                if classify(e) == 'channel'})
    return {'brand': sorted(set.intersection(*brand_sets)) if brand_sets else [],
            'channels': {channel: sorted(set.intersection(*groups)) for channel, groups in channels.items()
                         if channel is not None}}


def semantic_projection(value):
    """Keep change-detection and ranking bookkeeping outside semantic prompts."""
    omitted = {'source_snapshot', 'resolution_snapshot', 'score', 'score_type',
               'keyword_rank', 'keyword_score', 'semantic_rank', 'semantic_score', 'rank'}
    if isinstance(value, dict):
        return {k: semantic_projection(v) for k, v in value.items() if k not in omitted}
    if isinstance(value, list):
        return [semantic_projection(v) for v in value]
    return value


def compact_requirement_context(requirements):
    """Lossless transport refs for repeated asset fields and provenance quotations."""
    import json
    counts = {}
    fields = {'audience', 'timing', 'sender_or_byline', 'constraints', 'quote'}
    def count(value):
        if isinstance(value, dict):
            for k, v in value.items():
                if k in fields and v is not None:
                    key = json.dumps(v, sort_keys=True, ensure_ascii=False)
                    if len(key) >= 60:
                        counts[key] = counts.get(key, 0) + 1
                count(v)
        elif isinstance(value, list):
            for v in value:
                count(v)
    count(requirements)
    aliases = {key: 'v' + str(i) for i, key in enumerate(sorted(k for k, n in counts.items() if n > 1))}
    def project(value):
        if isinstance(value, dict):
            result = {}
            for k, v in value.items():
                key = json.dumps(v, sort_keys=True, ensure_ascii=False)
                result[k] = {'value_ref': aliases[key]} if k in fields and key in aliases else project(v)
            return result
        if isinstance(value, list):
            return [project(v) for v in value]
        return deepcopy(value)
    return {'values': {alias: json.loads(key) for key, alias in aliases.items()},
            'requirements': project(semantic_projection(requirements))}
