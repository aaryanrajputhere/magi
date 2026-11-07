import json
from copy import deepcopy

import pytest

from app.llm.shared_evidence import canonical_messages, saved_blocks, text_ref
from app.llm.prompt_cache import cacheable_input


def messages(*groups):
    return [{'role': 'user', 'content': json.dumps({'sources': group})} for group in groups]


def source(id, content):
    return {'chunk_id': id, 'content': content, 'heading_path': ['Original']}


def restore(projected, blocks):
    texts = {k: v for block in blocks for k, v in block.items()}
    def walk(value):
        if isinstance(value, list):
            return [walk(v) for v in value]
        if not isinstance(value, dict):
            return value
        if 'text_ref' in value:
            return {**{k: deepcopy(v) for k, v in value.items() if k != 'text_ref'},
                    'content': texts[value['text_ref']]}
        return {k: walk(v) for k, v in value.items()}
    return [walk(json.loads(m['content'])) for m in projected]


def test_round_trip_preserves_text_metadata_ids_and_duplicate_membership():
    original = messages([source('one', 'Exact α\ntext'), source('two', 'Exact α\ntext')])
    before = deepcopy(original)
    blocks, projected = canonical_messages(original)
    assert restore(projected, saved_blocks({'input': blocks})) == [json.loads(m['content']) for m in original]
    assert sum(len(b) for b in saved_blocks({'input': blocks})) == 1
    assert original == before


def test_planner_verifier_router_reuse_despite_local_aliases():
    planner = messages([source('original', 'Text A'), source('original2', 'Text B')])
    first, _ = canonical_messages(planner)
    previous = [saved_blocks({'input': first})]
    verifier, _ = canonical_messages(messages([source('r1', 'Text A'), source('r2', 'Text B')]), previous)
    assert first == verifier
    router, projected = canonical_messages(messages([
        source('s0', 'Text B'), source('s1', 'Text C'), source('s2', 'Text A')]), previous)
    assert router[:len(first)] == first
    assert len(router) == 2
    assert restore(projected, saved_blocks({'input': router}))[0]['sources'][0]['chunk_id'] == 's0'


def test_partial_overlap_or_changed_content_never_imports_unselected_evidence():
    first, _ = canonical_messages(messages([source('a', 'A'), source('b', 'B')]))
    for selected in ([source('a', 'A')], [source('a', 'Changed'), source('b', 'B')]):
        blocks, projected = canonical_messages(messages(selected), [saved_blocks({'input': first})])
        assert restore(projected, saved_blocks({'input': blocks})) == [{'sources': selected}]
        assert blocks != first


def test_writer_brand_channel_boundaries_survive():
    initial = messages([source('brand', 'Brand')], [source('channel', 'Channel')], [source('a', 'A')])
    first, _ = canonical_messages(initial)
    later, _ = canonical_messages(messages([source('brand', 'Brand')],
        [source('channel', 'Channel')], [source('b', 'B')]), [saved_blocks({'input': first})])
    assert first[:2] == later[:2]
    assert len(first) == len(later) == 3


def test_registry_survives_breakpoints_and_ignores_task_suffix():
    blocks, projected = canonical_messages(messages([source('a', 'A')]))
    marked = cacheable_input(blocks, 'gpt-6-luna', len(blocks))
    assert saved_blocks({'input': [*marked, {'type': 'configuration_update'}, *projected]}) == saved_blocks({'input': blocks})
    assert saved_blocks({'input': messages([source('a', 'A')])}) == []


def test_no_sources_and_reserved_field():
    original = [{'role': 'user', 'content': 'plain text'}]
    assert canonical_messages(original) == ([], original)
    with pytest.raises(ValueError, match='reserved'):
        canonical_messages(messages([{**source('a', 'A'), 'text_ref': 'spoof'}]))


def test_conflicting_originals_rejected_and_ties_deterministic():
    with pytest.raises(ValueError, match='Conflicting originals'):
        canonical_messages(messages([source('a', 'First'), source('a', 'Different')]))
    a, _ = canonical_messages(messages([source('a', 'AA')]))
    b, _ = canonical_messages(messages([source('b', 'BB')]))
    prior = [saved_blocks({'input': a}), saved_blocks({'input': b})]
    current = messages([source('a', 'AA'), source('b', 'BB')])
    assert canonical_messages(current, prior) == canonical_messages(current, prior[::-1])


def test_channel_removal_reuses_general_sources_without_expanding_selection():
    general = [source('product', 'Exact supported qualification'),source('voice', 'Brand voice')]
    channel = {**source('blog', 'Blog rules'), 'relative_path':'brand/channels/blog.md'}
    first, _ = canonical_messages(messages([*general,channel]))
    focused, projected = canonical_messages(messages(general),[saved_blocks({'input':first})])
    assert len(first) == 2
    assert focused == first[:1]
    assert restore(projected,saved_blocks({'input':focused})) == [{'sources':general}]
