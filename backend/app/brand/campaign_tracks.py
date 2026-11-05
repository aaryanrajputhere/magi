"""Independent topic and channel reviews, joined into asset-specific evidence packages."""
import re
from concurrent.futures import ThreadPoolExecutor

from app.brand.context import load_artifact, save_artifact
from app.brand.retrieval import RetrievalError

CHANNEL_NAMES = {'linkedin': 'LinkedIn', 'x': 'X', 'email': 'Email',
                 'blog': 'Blog', 'landing_page': 'Landing page'}


def channel_queries(requirements):
    queries = []
    for channel in dict.fromkeys(a['format'] for a in requirements['assets']):
        name = CHANNEL_NAMES[channel]
        assets = [a for a in requirements['assets'] if a['format'] == channel]
        # Tokenize full requirements before applying search-query bounds.
        text = ' '.join(
            a['audience'] + ' ' + a['purpose'] + ' ' + ' '.join(a.get('constraints', []))
            for a in assets)
        terms = list(dict.fromkeys(re.findall(r"[^\W_]+", text.lower())))[:12]
        shared = ' '.join(requirements.get('constraints', [])) + ' ' + ' '.join(
            section['content'] for section in requirements.get('topic_details', []))
        needs_voice = bool(re.search(
            r'\b(tone|voice|humou?r|imagery|images?|screenshots?|visuals?|hero)\b',
            text + ' ' + shared, re.IGNORECASE))
        focuses = [
            ('rules', 'content format length limits CTA links sender byline structure restrictions'),
            ('voice', 'voice audience tone imagery eligibility visual restrictions examples'),
        ]
        if not needs_voice:
            focuses = focuses[:1]
        for suffix, focus in focuses:
            query = f'{name} channel guidelines {focus}'
            for term in terms:
                candidate = f'{query} {term}'
                if len(candidate) <= 512 and len(re.findall(r"[^\W_]+", candidate)) <= 32:
                    query = candidate
            queries.append({'id': f'channel__{channel}__{suffix}', 'purpose': 'channel',
                            'query': query,
                            'reason': f'Retrieve {name} {focus} separately for the requested brief.'})
    return queries


def review_tracks(engine, brand_id, retrieved, searches, requirements, settings, campaign_id):
    from app.brand.review import review_campaign
    from app.llm.client import TrackedOpenAI

    groups = retrieved['groups']
    partitions = {'topic': [g for g in groups if not g['query']['id'].startswith('channel__')]}
    for channel in dict.fromkeys(a['format'] for a in requirements['assets']):
        partitions[channel] = [g for g in groups
                               if g['query']['id'].startswith(f'channel__{channel}__')]
    if any(not items for items in partitions.values()):
        raise RetrievalError('Missing topic or channel evidence track', 'incomplete_retrieval', 422)

    def run_track(item):
        name, track_groups = item
        analysis = {**searches['plan']['analysis'],
                    'channels': [] if name == 'topic' else [CHANNEL_NAMES[name]]}
        # Scope authority resolution to this channel, including channel-specific supersessions.
        scoped_plan = save_artifact(engine, brand_id, 'plan', retrieved['corpus_fingerprint'], {
            'brief': searches['brief'], 'requirements': requirements,
            'plan': {'analysis': analysis, 'queries': [g['query'] for g in track_groups]},
            'track': name}, searches['id'])
        scoped_retrieval = save_artifact(engine, brand_id, 'retrieval', retrieved['corpus_fingerprint'],
            {**{k: v for k, v in retrieved.items()
                if k not in {'id', 'parent_id', 'brand_id', 'corpus_fingerprint', 'groups'}},
             'groups': track_groups, 'track': name}, scoped_plan['id'])
        client = TrackedOpenAI(engine, settings)
        try:
            return name, review_campaign(engine, brand_id, scoped_retrieval['id'], client=client,
                                         settings=settings, campaign_id=campaign_id)
        finally:
            client.close()

    # Topic and channel work is independent; each worker owns its database sessions/client.
    with ThreadPoolExecutor(max_workers=3) as pool:
        tracks = dict(pool.map(run_track, partitions.items()))
    authority_ids = {t['authority_id'] for t in tracks.values()}
    if len(authority_ids) != 1:
        raise RetrievalError('Authority changed between evidence tracks', 'authority_not_ready', 409)
    report = {key: [value for track in tracks.values() for value in track['report'].get(key, [])]
              for key in ('supported', 'restricted', 'conflicts', 'unresolved', 'claims')}
    evidence = {p['chunk_id']: p for track in tracks.values() for p in track['evidence']}
    return save_artifact(engine, brand_id, 'campaign_review', retrieved['corpus_fingerprint'], {
        'report': report, 'evidence': list(evidence.values()),
        'authority_id': tracks['topic']['authority_id'],
        'authority_version_id': tracks['topic']['authority_id'],
        'requirements': requirements,
        'topic_review_id': tracks['topic']['id'],
        'channel_review_ids': {name: track['id'] for name, track in tracks.items() if name != 'topic'},
        'tracks': {name: {'review_id': track['id'], 'report': track['report'],
                          'evidence': track['evidence']} for name, track in tracks.items()},
        'coverage_limitations': [f'{name}: {limitation}' for name, track in tracks.items()
                                 for limitation in track.get('coverage_limitations', [])],
        'pipeline_version': 8}, retrieved['id'])


def asset_package(reviewed, requirement):
    tracks = reviewed['tracks']
    topic, channel = tracks['topic'], tracks[requirement['format']]
    evidence = {p['chunk_id']: p for track in (topic, channel) for p in track['evidence']}
    return {'mode': reviewed.get('mode'), 'requirement': requirement,
            'rule_pack': channel.get('rule_pack'),
            'campaign_topic': reviewed['requirements']['topic'],
            'campaign_context': reviewed['requirements'].get('topic_details', []),
            'campaign_constraints': reviewed['requirements']['constraints'],
            'topic': {'review_id': topic['review_id'], 'report': topic['report']},
            'channel': {'review_id': channel['review_id'], 'report': channel['report']},
            'evidence': list(evidence.values())}


def load_asset_package(engine, brand_id, plan, asset_id):
    package = plan.get('asset_packages', {}).get(asset_id)
    if package is None:
        return None
    if not package.get('rule_pack'):
        from app.brand.rule_pack import build_rule_packs
        from app.config import get_settings
        from app.llm.client import TrackedOpenAI
        settings = get_settings()
        client = TrackedOpenAI(engine, settings)
        try:
            pack = build_rule_packs(engine, brand_id, [package['requirement']['format']], client, settings, None)[package['requirement']['format']]
            package = {**package, 'mode': 'verified', 'rule_pack': pack, 'evidence': pack['evidence']}
        finally:
            client.close()
    from app.brand.authority import current_authority
    current_authority(engine, brand_id, package['rule_pack']['authority_version_id'])
    # Validate the persisted source/authority fingerprints again at execution time.
    for track in ('topic', 'channel'):
        load_artifact(engine, brand_id, package[track]['review_id'], 'campaign_review')
    return package
