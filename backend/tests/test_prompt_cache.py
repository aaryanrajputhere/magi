import json

from app.llm.prompt_cache import cached_prompt, prompt_cache_key


def test_shared_prefix_and_lossless_payload():
    common = {'rules': {'limits': [10, 20]}, 'conversation': ['request']}
    first = cached_prompt({**common, 'draft': 'first'}, tuple(common))
    second = cached_prompt({**common, 'draft': 'second'}, tuple(common))
    assert first[0] == second[0]
    assert first[1] != second[1]
    assert {**json.loads(first[0]['content']), **json.loads(first[1]['content'])} == {**common, 'draft': 'first'}


def test_cache_key_scope():
    assert prompt_cache_key('brand', 'generate') == prompt_cache_key('brand', 'generate')
    assert prompt_cache_key('brand', 'generate') != prompt_cache_key('other', 'generate')
    assert prompt_cache_key('brand', 'generate') != prompt_cache_key('brand', 'validate')


def test_long_cache_keys_fit_provider_limit_and_preserve_scope():
    brand = 'e42c22a9-081f-4a24-adce-6c2982f39cae'
    operation = 'generate_campaign_asset'
    key = prompt_cache_key(brand, operation)
    assert len(key) <= 64
    assert key == prompt_cache_key(brand, operation)
    assert key != prompt_cache_key(brand, operation + '_other')
    assert key != prompt_cache_key(brand + '_other', operation)
    assert len(prompt_cache_key('b' * 1000, 'o' * 1000)) <= 64
