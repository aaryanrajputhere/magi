from copy import deepcopy

from app.brand.guidelines import generation_guidelines
from app.brand.image_selection import visual_rules
from app.llm.prompt_payloads import writing_package


def test_packets_keep_facts_exceptions_and_only_applicable_rules():
    rules = {'rules': [
        {'category': 'global', 'channels': [], 'chunk_id': 'g', 'quote': 'Never invent claims.'},
        {'category': 'writing', 'channels': [], 'chunk_id': 'w', 'quote': 'Write clearly.'},
        {'category': 'channel', 'channels': ['linkedin'], 'chunk_id': 'l', 'quote': '180 words including links.'},
        {'category': 'channel', 'channels': ['blog'], 'chunk_id': 'b', 'quote': '900 words.'},
        {'category': 'visual', 'channels': [], 'chunk_id': 'v', 'quote': 'Use licensed illustrations only.'}]}
    package = {'evidence': [{'chunk_id': 'g', 'content': 'Huge original document'},
                            {'chunk_id': 'fact', 'content': 'Supported product fact'}],
               'rule_pack': {'format': 'linkedin', 'limits': [],
                             'evidence': [{'chunk_id': 'g', 'content': 'Huge original document'}]},
               'channel': {'report': {'supported': [{'text': 'Huge original document'}]}}}
    before = deepcopy(package)
    packet = writing_package(generation_guidelines(package, rules, 'linkedin'))
    assert package == before
    text = str(packet)
    assert 'Huge original document' not in text
    assert 'Supported product fact' in text
    assert '180 words including links.' in text
    assert '900 words.' not in text
    assert 'licensed illustrations' not in text
    visual = generation_guidelines(package, rules, 'linkedin', visual=True)
    assert 'Use licensed illustrations only.' in visual_rules(visual)
