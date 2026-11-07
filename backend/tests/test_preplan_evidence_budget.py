from review_helpers import review_payload
from app.brand.plan_verification import repair_rule_context


def test_repair_keeps_cited_document_exceptions_and_dependencies():
    evidence = [
        {'chunk_id': 'claim', 'document_id': 'd', 'content': 'Claim'},
        {'chunk_id': 'exception', 'document_id': 'd', 'content': 'Exception',
         'dependency_ids': ['definition']},
        {'chunk_id': 'definition', 'document_id': 'other', 'content': 'Definition'},
        {'chunk_id': 'unused', 'document_id': 'unrelated', 'content': 'Unused'}]
    packs = {'blog': {'format': 'blog', 'evidence': evidence}}
    result = repair_rule_context(packs, {'claim'})
    assert [e['chunk_id'] for e in result['blog']['evidence']] == ['claim', 'exception', 'definition']
    assert len(packs['blog']['evidence']) == 4
    assert repair_rule_context(packs, set()) == packs


def test_gap_prompt_omits_source_text_and_reuses_only_matching_requirements():
    import json
    from types import SimpleNamespace

    from app.brand.supplemental import supplement

    campaign = SimpleNamespace(id='c', brand_id='b', data={'requirements': {'topic': 'Containment'}})
    retrieved = {'id': 'r', 'corpus_fingerprint': 'hash', 'groups': [
        {'query': {'query': 'company'}, 'status': 'success', 'results': [
            {'chunk_id': 'source', 'relative_path': 'brand.md', 'heading_path': ['Company'],
             'content': 'Original source text ' * 10000}]}]}
    calls = []
    def run(request, output_model):
        payload = review_payload(request)
        assert request.max_output_tokens >= 2000  # Reasoning shares the JSON output allowance.
        assert 'Original source text' not in str(request.input)
        assert payload['retrieval_coverage'][0]['source_count'] == 1
        assert payload['focused_evidence'] == []
        calls.append(request)
        return SimpleNamespace(parsed=output_model(reason='No identified gap', queries=[]))
    client = SimpleNamespace(run=run)
    settings = SimpleNamespace(magi_retrieval_planner_model='test')
    supplement(None, campaign, retrieved, client, settings, only_if_needed=False)
    supplement(None, campaign, retrieved, client, settings, only_if_needed=False)
    assert len(calls) == 1
    campaign.data['requirements'] = {'topic': 'Changed topic'}
    supplement(None, campaign, retrieved, client, settings, only_if_needed=False)
    assert len(calls) == 2


def test_gap_passages_preserve_complete_originals_within_budget():
    from app.brand.supplemental import gap_passages
    retrieved = {'groups': [{'query': {'id': 'topic'}, 'results': [
        {'chunk_id': 'a', 'content': 'Exact original. Except when restricted.'},
        {'chunk_id': 'b', 'content': 'x' * 13000}]}]}
    result = gap_passages(retrieved)
    assert result == [{'chunk_id': 'a', 'content': 'Exact original. Except when restricted.'}]


def test_repeated_asset_sources_share_one_reference_group():
    from app.llm.prompt_payloads import lean_plan_context
    source = {'chunk_id': 'long-original-citation-id', 'content': 'Original rule',
              'document_id': 'storage-only-document-id', 'start_line': 12}
    package = {'evidence': [source], 'rule_pack': {'format': 'blog', 'limits': []}}
    result = lean_plan_context({'asset_packages': {'a': package, 'b': package}})
    assert len(result['source_groups']) == 1
    assert result['asset_packages']['a']['evidence_group'] == result['asset_packages']['b']['evidence_group']
    assert result['evidence'][0]['chunk_id'] == source['chunk_id']
    assert result['evidence'][0]['content'] == source['content']
    assert 'document_id' not in result['evidence'][0]
