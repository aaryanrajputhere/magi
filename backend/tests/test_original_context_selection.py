from copy import deepcopy

from app.llm.prompt_cache import cached_prompt
from app.llm.prompt_payloads import original_passages, writing_package


def test_original_sources_preserved_and_generated_guidelines_ignored():
    source = {'chunk_id': 'a', 'relative_path': 'brand/claims.md',
              'content': 'Approved claim. Except in the EU.'}
    package = {'rule_pack': {'format': 'blog', 'limits': [], 'evidence': [source],
                            'writing_evidence': [{**source, 'content': 'Rewritten claim'}]},
               'evidence': [source]}
    before = deepcopy(package)
    packet = writing_package(package)
    assert package == before
    assert [{k: v for k, v in p.items() if k != 'ref'} for p in packet['evidence']] == [source]
    assert 'Rewritten claim' not in str(packet)


def test_only_clearly_unrelated_channel_chunks_removed():
    passages = [
        {'chunk_id': 'a', 'relative_path': 'brand/channels/mixed.md',
         'heading_path': ['Email'], 'content': 'Email-only rule'},
        {'chunk_id': 'b', 'relative_path': 'brand/channels/mixed.md',
         'heading_path': ['Blog'], 'content': 'Blog rule and exception'},
        {'chunk_id': 'c', 'relative_path': 'brand/legal.md',
         'heading_path': ['Email'], 'content': 'Uncertain cross-channel restriction'}]
    assert [e['chunk_id'] for e in original_passages(passages, 'blog')] == ['b', 'c']


def test_variable_draft_does_not_change_source_prefix():
    a = cached_prompt({'source_context': {'evidence': ['Original']}, 'draft': 'one'}, ('source_context',))
    b = cached_prompt({'source_context': {'evidence': ['Original']}, 'draft': 'two'}, ('source_context',))
    assert a[0] == b[0]
    assert a[1] != b[1]
