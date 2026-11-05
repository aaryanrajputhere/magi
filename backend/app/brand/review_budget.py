"""Lossless review batching when original passages exceed one request budget."""
from itertools import combinations

from app.brand.retrieval import RetrievalError


def review_inputs(content, fits):
    """Every passage pair is reviewed together; every batch retains the full brief.

    Proposed findings are also retained in every batch unless their own size
    requires partitioning. No source text is summarized or discarded.
    """
    if fits(content):
        return [content]
    proposed = content.get('proposed_review', {})
    findings = proposed.get('conflicts', [])
    if len(findings) > 1 and not fits({**content, 'passages': []}):
        midpoint = len(findings) // 2
        return [batch for part in (findings[:midpoint], findings[midpoint:])
                for batch in review_inputs({**content,
                    'proposed_review': {**proposed, 'conflicts': part}}, fits)]
    passages = content.get('passages', [])
    if not passages:
        raise RetrievalError('Review instructions and findings exceed the request budget',
                             'review_context_too_large')
    # Each group fits twice, reserving enough room to pair it with any similarly
    # sized group. Check actual pairs too: token boundaries need not be additive.
    groups, current = [], []
    for passage in passages:
        candidate = [*current, passage]
        if current and not fits({**content, 'passages': candidate + candidate}):
            groups.append(current)
            current = []
        current.append(passage)
        if not fits({**content, 'passages': current + current}):
            # A large individual passage can still be reviewed alongside smaller
            # passages; only reject when that original passage cannot fit at all.
            if not fits({**content, 'passages': current}):
                if len(findings) > 1:
                    midpoint = len(findings) // 2
                    return [batch for part in (findings[:midpoint], findings[midpoint:])
                            for batch in review_inputs({**content,
                                'proposed_review': {**proposed, 'conflicts': part}}, fits)]
                raise RetrievalError('A single original passage exceeds the review request budget',
                                     'review_context_too_large')
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    if len(groups) == 1:
        return [{**content, 'passages': groups[0]}]
    batches = []
    for left, right in combinations(groups, 2):
        batch = {**content, 'passages': left + right}
        if not fits(batch):
            raise RetrievalError('A pair of original passages exceeds the review request budget',
                                 'review_context_too_large')
        batches.append(batch)
    return batches


def same_grounded_issue(left, right):
    """Overlap batches may rediscover one choice with different wording."""
    assets = set(left.get('affected_asset_ids', []))
    if not assets or assets != set(right.get('affected_asset_ids', [])):
        return False
    if left.get('decision_type') != right.get('decision_type'):
        return False
    def quotes(value, origin):
        return [(q.get('source_ref'), q['quote']) for q in value.get('grounding', []) if q['origin'] == origin]
    if not set(quotes(left, 'source')) & set(quotes(right, 'source')):
        return False
    return any(len(a) >= 20 and len(b) >= 20 and (a in b or b in a)
               for _, a in quotes(left, 'user') for _, b in quotes(right, 'user'))


def deduplicate_findings(findings):
    """Merge only verified overlapping issues, retaining every grounding citation."""
    result = []
    for finding in findings:
        previous = next((item for item in result if same_grounded_issue(item, finding)), None)
        if previous is None:
            result.append(dict(finding))
            continue
        for field in ('source_ids', 'grounding', 'source_quotes'):
            values = list(previous.get(field, []))
            for value in finding.get(field, []):
                if value not in values:
                    values.append(value)
            previous[field] = values
    return result
