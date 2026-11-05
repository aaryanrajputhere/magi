"""Lossless evidence deduplication for asset prompts."""
import re
from copy import deepcopy


def lean_package(package):
    """Keep passages once; replace verbatim report copies with source references."""
    result = deepcopy(package)
    evidence = list({e['chunk_id']: e for e in [
        *package.get('evidence', []), *(package.get('rule_pack') or {}).get('evidence', [])
    ]}.values())
    contents = {e['content']: e['chunk_id'] for e in evidence}
    if result.get('rule_pack'):
        result['rule_pack'].pop('evidence', None)
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
    evidence, packs = {}, {}
    for asset_id, package in context.get('asset_packages', {}).items():
        lean = lean_package(package)
        passages = lean.pop('evidence')
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
    return result
