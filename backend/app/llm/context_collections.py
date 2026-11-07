"""Task-specific collections of verbatim source chunks, without stored-text mutation."""
import re
from copy import deepcopy

CATEGORIES = ('product', 'writing', 'voice', 'channel', 'visual', 'general')
CHANNELS = {'linkedin': r'linkedin', 'blog': r'blog', 'email': r'email',
            'landing_page': r'landing[ _-]page', 'x': r'x|twitter'}


def classify(passage):
    path = passage.get('relative_path', '').lower()
    headings = ' '.join(passage.get('heading_path') or []).lower()
    scope = path + ' ' + headings
    if '/visual' in path or re.search(r'\b(imagery|image selection|logo usage|screenshots)\b', headings):
        return 'visual'
    if '/voice/' in path or 'voice-and-tone' in path:
        return 'voice'
    if '/products/' in path or '/research/' in path or '/case-studies/' in path or '/legal/' in path:
        return 'product'
    if '/channels/' in path or '/templates/' in path:
        return 'channel'
    if '/style/' in path or re.search(r'\b(writing|grammar|punctuation|terminology)\b', scope):
        return 'writing'
    return 'general'


def applies(passage, channel):
    path = passage.get('relative_path', '').lower()
    headings = ' '.join(passage.get('heading_path') or []).lower()
    if '/channels/' not in path and '/templates/' not in path and '/by-content-type/' not in path:
        return True  # Uncertain scope is retained, never guessed away.
    # Explicit section scope takes precedence over a mixed document's filename.
    scope = headings if any(re.search(r'\b(?:' + p + r')\b', headings) for p in CHANNELS.values()) else path.rsplit('/', 1)[-1]
    matches = {name for name, pattern in CHANNELS.items()
               if re.search(r'\b(?:' + pattern + r')\b', scope)}
    return not matches or channel in matches


def assemble_collections(evidence, channel, task='writing'):
    originals = {p['chunk_id']: p for p in evidence}
    retained = {key for key, p in originals.items() if applies(p, channel)
                and (task != 'image' or classify(p) in ('visual', 'general', 'channel')
                     or '/legal/' in p.get('relative_path', '').lower())
                and (task in ('image', 'planning') or classify(p) != 'visual')}
    # Explicit dependencies override normal task/channel filtering.
    while True:
        dependencies = {key for identifier in retained
                        for key in originals[identifier].get('dependency_ids', []) if key in originals}
        if dependencies <= retained:
            break
        retained.update(dependencies)
    passages, collections = [], {name: [] for name in CATEGORIES}
    for i, key in enumerate(sorted(retained)):
        p = deepcopy(originals[key])
        p['ref'] = f's{i}'
        passages.append(p)
        collections[classify(p)].append(p['ref'])
    return {'evidence': passages, 'guideline_collections': {k: v for k, v in collections.items() if v}}
