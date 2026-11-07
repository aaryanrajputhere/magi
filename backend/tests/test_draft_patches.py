import pytest

from app.brand.draft_patches import Patch, apply
from app.brand.retrieval import RetrievalError


def test_minimal_patch_preserves_unrelated_text():
    draft = {'body': 'Correct citation. Long CTA. Observe mode records.', 'title': 'Keep'}
    result = apply(draft, [Patch(field='body', start=18, end=26, original='Long CTA', replacement='Read more')])
    assert result['body'] == 'Correct citation. Read more. Observe mode records.'
    assert draft['body'] == 'Correct citation. Long CTA. Observe mode records.'
    assert result['title'] == 'Keep'


def test_invalid_patch_retains_original():
    draft = {'body': 'Original'}
    with pytest.raises(RetrievalError):
        apply(draft, [Patch(field='body', start=0, end=4, original='Wrong', replacement='Changed')])
    assert draft['body'] == 'Original'


def test_stale_hash_and_overlapping_patches_are_rejected_atomically():
    draft = {'body': 'Original text'}
    first = Patch(field='body', start=0, end=8, original='Original', replacement='New')
    with pytest.raises(RetrievalError, match='changed'):
        apply(draft, [first], 'stale')
    second = Patch(field='body', start=4, end=13, original='inal text', replacement='Other')
    with pytest.raises(RetrievalError, match='Overlapping'):
        apply(draft, [first, second])
    assert draft['body'] == 'Original text'


def test_exact_unique_anchor_recovers_model_offset_on_same_hash():
    from app.brand.draft_validation import content_hash
    draft = {'body': 'Preserve citation. Long CTA. Preserve qualification.'}
    patch = Patch(field='body', start=999, end=1007, original='Long CTA', replacement='Read more')
    result = apply(draft, [patch], content_hash(draft))
    assert result['body'] == 'Preserve citation. Read more. Preserve qualification.'


def test_null_offsets_resolve_only_unique_exact_anchor_on_same_hash():
    from app.brand.draft_validation import content_hash
    draft = {'body': 'Keep qualifier. Replace this. Keep citation.', 'title': 'Keep'}
    patch = Patch(field='body', start=None, end=None, original='Replace this', replacement='Read more')
    result = apply(draft, [patch], content_hash(draft))
    assert result == {'body': 'Keep qualifier. Read more. Keep citation.', 'title': 'Keep'}
    with pytest.raises(RetrievalError):
        apply(draft, [patch])
    duplicate = {'body': 'Repeated. Repeated.'}
    with pytest.raises(RetrievalError):
        apply(duplicate, [Patch(field='body', start=None, end=None,
              original='Repeated', replacement='Changed')], content_hash(duplicate))
    assert duplicate['body'] == 'Repeated. Repeated.'
