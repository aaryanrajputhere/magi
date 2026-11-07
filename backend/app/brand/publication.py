"""Publication blockers are separate from text compliance."""
import hashlib
from copy import deepcopy


def dependency(kind, message, source=None):
    return {'id': hashlib.sha256((kind + ':' + message).encode()).hexdigest()[:16],
            'kind': kind, 'message': message, 'status': 'unresolved', 'evidence': None,
            'source_ref': source}


def legacy_dependency(message):
    text = message.lower()
    kind = ('image_review' if ('image' in text or 'hero' in text) and any(
        word in text for word in ('rights', 'permission', 'restriction', 'metadata', 'placement')) else
            'image_attachment' if 'image' in text or 'hero' in text else
            'destination_url' if 'url' in text or 'destination' in text else
            'author' if 'byline' in text or 'author' in text else
            'publication_date' if 'date' in text or 'publication' in text else 'legacy')
    result = dependency(kind, message)
    result['legacy'] = True
    return result


def reconcile(draft, image_record=None):
    result = deepcopy(draft)
    records = result.get('dependency_records')
    if records is None:
        records = [legacy_dependency(m) for m in result.get('production_dependencies', [])]
    metadata = result.get('publication_details', {})
    for item in records:
        item['status'], item['evidence'] = 'unresolved', None
        kind = item['kind']
        if kind == 'image_review' and result.get('image') and image_record:
            item.setdefault('original_message', item['message'])
            item['message'] = 'Confirm the selected image rights and placement against its current library record.'
        evidence = None
        if kind == 'image_attachment' and result.get('image') and image_record:
            # Attachment does not imply rights/placement approval.
            evidence = {'image_id': image_record['id']}
        elif kind == 'author' and (result.get('byline') or metadata.get('byline')):
            evidence = {'byline': result.get('byline') or metadata['byline']}
        elif kind == 'destination_url' and metadata.get('destination_url') and metadata['destination_url'] in result.get('body', ''):
            evidence = {'destination_url': metadata['destination_url']}
        elif kind == 'publication_date' and metadata.get('publication_date'):
            evidence = {'publication_date': metadata['publication_date']}
        if evidence:
            item['status'], item['evidence'] = 'resolved', evidence
    unique = {}
    for item in records:
        key = (item['kind'], '') if item['kind'] in {
            'destination_url', 'author', 'publication_date', 'image_attachment', 'image_review'} else (
                item['kind'], item.get('source_ref') or item['message'])
        if key not in unique:
            unique[key] = item
        elif item.get('source_ref'):
            unique[key].setdefault('additional_source_refs', []).append(item['source_ref'])
    result['dependency_records'] = list(unique.values())
    result['production_dependencies'] = [i['message'] for i in unique.values() if i['status'] != 'resolved']
    validation = result.get('validation', {})
    text_passed = validation.get('status') == 'passed'
    result['publication_readiness'] = 'ready' if text_passed and not result['production_dependencies'] else 'blocked'
    result['status'] = 'completed' if result['publication_readiness'] == 'ready' else 'needs_review'
    return result
