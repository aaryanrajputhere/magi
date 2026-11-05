from app.brand.evidence_ledger import retain_evidence


def test_retrieval_cannot_erase_publication_hold():
    data = {}
    hold = {'chunk_id': 'hold', 'content': 'Not cleared for external use'}
    story = {'chunk_id': 'story', 'content': 'Three weeks saved'}
    retain_evidence(data, [hold, story])
    assert retain_evidence(data, [story]) == [hold, story]
    assert len(data['evidence_ledger']) == 2


def test_changed_passage_preserves_original_revision():
    data = {}
    old = {'chunk_id': 'status', 'content': 'Not cleared'}
    new = {'chunk_id': 'status', 'content': 'Cleared'}
    retain_evidence(data, [old])
    assert retain_evidence(data, [new]) == [old, new]
