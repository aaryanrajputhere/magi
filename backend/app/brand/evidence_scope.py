"""Conservative stage applicability; originals and deferred sources stay inspectable."""
import re
from functools import lru_cache

from app.brand.retrieval import RetrievalError
from app.llm.context_collections import applies

VERSION = 'stage_scope_v2'
# Defer only identifiable execution details. Unknown or mixed sections stay.
EXECUTION = re.compile(r'type scale|typeface|typography|clear space|logo sizing|colour values|color values|hex values|minimum size|export formats|resolution|grammar|punctuation|capitalization|hyphenation|lists$|percentages$|money$|tables, charts and captions|bylines and bios|safe_on|variant_of|background$|image library and its metadata|picking an image: the order|one rewrite, start to finish|how the sentences go', re.IGNORECASE)
PLANNING = re.compile(r'claim|certif|permission|consent|embargo|launch|schedule|audience|pricing|support|roadmap|hero|product UI|approval|clearance|customer|exception|supersed|authority|positioning|containment', re.IGNORECASE)


@lru_cache(maxsize=8192)
def execution_only(path, headings, content):
    """Classify immutable sections, keeping uncertain and mixed restrictions."""
    leaf = headings[-1] if headings else ''
    sensitive = re.search(r'\b(?:claims?|certif\w*|permission|consent|embargo|launch|schedule|'
        r'audience|pricing|roadmap|approval|clearance|customer|exception|supersed\w*|authority|'
        r'prohibited|never|must not|do not)\b', content, re.IGNORECASE)
    if sensitive:
        return False
    if EXECUTION.search(' '.join(headings)):
        return True
    production = re.search(r'worked hierarchy|font weights?|line heights?|spacing scale|'
        r'colour palette|color palette|file naming|export settings|sentence mechanics|'
        r'writing numbers|serial comma|quotation marks|dashes and|icon sizing|'
        r'palette|size and crop|transparency and dark mode|spec$|the spec$', leaf, re.IGNORECASE)
    known = any(part in path.lower() for part in ('/style/', '/visuals/', '/voice/', '/guidelines/'))
    if known and production:
        return True
    # Frontmatter without substantive prose is inventory metadata, not a rule.
    return bool(known and re.fullmatch(r'\s*---\n.*?\n---\s*', content, re.DOTALL))


def dependency_closure(pool, retained):
    retained = set(retained)
    while True:
        added = {ref for key in retained for ref in pool[key].get('dependency_ids', [])}
        if not added <= pool.keys():
            raise RetrievalError('Required source dependency unavailable during stage selection')
        if added <= retained:
            return retained
        retained.update(added)



def global_source_ids(evidence, protected=()):
    """Global strategy/checks retain authority, facts and their original dependencies."""
    pool = {p['chunk_id']: p for p in evidence}
    ids = {key for key, p in pool.items() if not any(part in p.get('relative_path', '').lower()
        for part in ('/channels/', '/templates/', '/visuals/', '/style/', '/voice/'))}
    return dependency_closure(pool, ids | (set(protected) & pool.keys()))


def select_stage(evidence, formats, *, protected=(), authority=None, defer_execution=True):
    pool = {p['chunk_id']: p for p in evidence}
    protected = set(protected) & pool.keys()
    reasons, retained = {}, set()
    groups = (authority or {}).get('groups', [])
    by_document = {doc: group for group in groups for doc in group['document_ids']}
    # Confirmed source relationships may span scoped channels: retain both ends.
    counterpart_docs = {link[key] for link in (authority or {}).get('supersessions', [])
                        for key in ('newer_id', 'older_id')}
    for key, passage in pool.items():
        headings = passage.get('heading_path') or []
        group = by_document.get(passage.get('document_id'), {})
        channels = group.get('channels')
        counterpart = passage.get('document_id') in counterpart_docs
        if key in protected or counterpart:
            reason = 'prior finding or authority counterpart'
        elif formats and channels and not set(channels) & set(formats):
            reasons[key] = 'deferred: confirmed authority channel does not apply'
            continue
        elif formats and not any(applies(passage, channel) for channel in formats):
            reasons[key] = 'deferred: explicitly unrelated channel'
            continue
        elif defer_execution and execution_only(passage.get('relative_path', ''), tuple(headings), passage.get('content', '')):
            reasons[key] = 'deferred: execution-only section; available to drafting/image selection'
            continue
        else:
            reason = 'planning rule, factual evidence or uncertain applicability'
        retained.add(key)
        reasons[key] = reason
    closed = dependency_closure(pool, retained)
    for key in closed - retained:
        reasons[key] = 'retained: explicit source dependency'
    return closed, {'version': VERSION, 'retained_ids': sorted(closed),
                    'deferred_ids': sorted(pool.keys() - closed), 'reasons': reasons}


def focused_rules(packs, refs):
    """Keep original section siblings, ancestors and explicit exceptions/dependencies."""
    pool = {p['chunk_id']: p for pack in packs.values() for p in pack.get('evidence', [])}
    if not refs or not set(refs) <= pool.keys():
        return packs, 'expanded: no complete source anchors for changed fields'
    selected = set(refs)
    for key in refs:
        p = pool[key]
        heading = p.get('heading_path') or []
        if not heading or not p.get('document_id'):
            # No section boundaries means full document is the safe unit.
            selected.update(k for k, q in pool.items() if q.get('document_id') == p.get('document_id')
                            and q.get('relative_path') == p.get('relative_path'))
            continue
        for other, q in pool.items():
            if q.get('document_id') != p.get('document_id'):
                continue
            h = q.get('heading_path') or []
            if (h[:len(heading)] == heading or heading[:len(h)] == h
                    or re.search(r'definition|scope|applicability|exception|qualification|restriction|supersed', ' '.join(h), re.IGNORECASE)):
                selected.add(other)
    selected = dependency_closure(pool, selected)
    return {channel: {**pack, 'evidence': [p for p in pack.get('evidence', []) if p['chunk_id'] in selected]}
            for channel, pack in packs.items()}, 'focused: changed-field source sections and dependency closure'
