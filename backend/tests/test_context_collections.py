from copy import deepcopy

from app.llm.context_collections import assemble_collections
from app.llm.prompt_payloads import lean_plan_context


def test_task_packets_keep_original_text_scope_and_dependencies():
    evidence = [
        {'chunk_id': 'product', 'relative_path': 'brand/products/runtime.md', 'content': 'Exact product facts. Except in observe mode.'},
        {'chunk_id': 'linkedin', 'relative_path': 'brand/channels/linkedin-playbook.md', 'content': 'Original LinkedIn rules.'},
        {'chunk_id': 'blog', 'relative_path': 'brand/channels/blog-playbook.md', 'content': 'Original blog rules.'},
        {'chunk_id': 'voice', 'relative_path': 'brand/voice/voice-and-tone.md', 'content': 'Original voice.'},
        {'chunk_id': 'image', 'relative_path': 'brand/visuals/common.md', 'content': 'Exact visual exceptions.'}]
    before = deepcopy(evidence)
    text = assemble_collections(evidence, 'linkedin')
    assert {p['chunk_id'] for p in text['evidence']} == {'product', 'linkedin', 'voice'}
    images = assemble_collections(evidence, 'linkedin', 'image')
    assert {p['chunk_id'] for p in images['evidence']} == {'linkedin', 'image'}
    assert evidence == before
    for p in text['evidence'] + images['evidence']:
        assert p['content'] == next(e['content'] for e in evidence if e['chunk_id'] == p['chunk_id'])
    evidence[0]['dependency_ids'] = ['image']
    assert 'image' in {p['chunk_id'] for p in assemble_collections(evidence, 'linkedin')['evidence']}


def test_planning_group_references_resolve_without_mutating_saved_plan():
    package = {'requirement': {'format': 'linkedin'}, 'evidence': [
        {'chunk_id': 'p', 'relative_path': 'brand/products/runtime.md', 'content': 'Original facts'}]}
    context = {'asset_packages': {'asset': package}}
    before = deepcopy(context)
    result = lean_plan_context(context)
    assert context == before
    assert result['asset_packages']['asset']['guideline_collections']['product'] == ['s0']
    assert result['evidence'][0]['content'] == 'Original facts'
