"""Deterministic plan checks; original evidence and requirements remain authoritative."""
import re


def proposed(requirement, field):
    return any(p.get('field') == field and p.get('origin') == 'proposed'
               for p in requirement.get('provenance', []))


def mechanical_findings(plan, messages=()):
    from app.brand.cta_limits import explicit_limits
    findings = []
    assets = {a['id']: a for a in plan['assets']}
    if any(re.search(r'\[s\d+(?:[, \]\d]|$)', item) for item in plan.get('constraints', [])):
        findings.append({'asset_id': None, 'field': 'constraints',
            'explanation': 'Legacy stage-local citation aliases require rechecking against original source IDs. Do not guess their meaning from this packet.',
            'kind': 'content', 'rule_refs': [], 'severity': 'minor'})
    def add(asset, field, message, severity='minor', refs=None):
        findings.append({'asset_id': asset['id'], 'field': field, 'explanation': message,
                             'kind': 'content', 'rule_refs': refs or [], 'severity': severity})
    for asset in assets.values():
        pack = plan.get('asset_packages', {}).get(asset['id'], {}).get('rule_pack', {})
        for limit in explicit_limits(pack.get('evidence', [])):
            label = asset.get('cta', '')
            count = len(label) if limit['unit'] == 'characters' else len(label.split())
            if count > limit['maximum']:
                add(asset, 'cta', f"CTA label exceeds {limit['maximum']} {limit['unit']}; keep sequencing and destinations in their own fields.", refs=[limit['chunk_id']])
        destination = asset.get('cta_destination')
        kind = asset.get('cta_destination_kind')
        legacy_form = bool(destination and re.fullmatch(r'(?:an? |the )?on[- ]page (?:demo |request |demo request )?form[.]?', destination, re.IGNORECASE))
        form = kind == 'on_page_form' or (kind is None and legacy_form)
        if form and (asset.get('format') != 'landing_page' or (destination and re.match(r'^https?://', destination))):
            add(asset, 'cta_destination_kind', 'An on-page form destination belongs to its landing page and requires no URL.', 'material')
        if kind == 'asset' and destination not in assets:
            add(asset, 'cta_destination', 'Asset destination must name an existing asset.', 'material')
        if kind == 'url' and not (destination and re.match(r'^https?://', destination)):
            add(asset, 'cta_destination', 'URL destination requires an evidence-supported URL.', 'material')
        dependencies = asset.get('publish_after_asset_ids', [])
        if destination and not form and destination not in assets and not re.match(r'^https?://', destination):
            add(asset, 'cta_destination', 'Destination must name an existing asset ID or an evidence-supported URL.', 'material')
        if destination in assets and destination not in dependencies:
            add(asset, 'publish_after_asset_ids', 'Publish after the CTA destination asset is available.')
        if any(dep not in assets or dep == asset['id'] for dep in dependencies):
            add(asset, 'publish_after_asset_ids', 'Publication dependency names an unknown asset or itself.', 'material')
        if destination and re.match(r'^https?://', destination):
            originals = '\n'.join(e.get('content', '') for p in plan.get('asset_packages', {}).values()
                                  for e in [*p.get('evidence', []), *p.get('rule_pack', {}).get('evidence', [])])
            originals += '\n' + '\n'.join(m.get('text', '') for m in messages if m.get('role') == 'user')
            if destination not in originals:
                add(asset, 'cta_destination', 'Destination URL lacks supplied supporting evidence.', 'material')
    def cyclic(start, node, visited):
        for dep in assets.get(node, {}).get('publish_after_asset_ids', []):
            if dep == start:
                return True
            if dep not in visited and cyclic(start, dep, visited | {dep}):
                return True
        return False
    for asset in assets.values():
        if cyclic(asset['id'], asset['id'], {asset['id']}):
            add(asset, 'publish_after_asset_ids', 'Publication dependencies contain a cycle.', 'material')
    return findings


def merge_findings(findings):
    """One correction per field, preserving every distinct instruction and reference."""
    merged = {}
    for finding in findings:
        d = finding.model_dump() if hasattr(finding, 'model_dump') else dict(finding)
        key = d['asset_id'], d['field']
        if key not in merged:
            merged[key] = d
            continue
        old = merged[key]
        if d['explanation'] not in old['explanation']:
            old['explanation'] += '\n' + d['explanation']
        old['rule_refs'] = sorted(set(old['rule_refs']) | set(d['rule_refs']))
        if d.get('severity') == 'material':
            old['severity'] = 'material'
            if d.get('brief_quote'):
                old['brief_quote'] = d['brief_quote']
        if d['kind'] == 'content':
            old['kind'] = 'content'
    return list(merged.values())
