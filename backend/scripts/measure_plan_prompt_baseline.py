"""Compare serialized prompt tokens offline; does not make model requests.

Run from backend: python -m scripts.measure_plan_prompt_baseline /path/to/runs.json
Counts use cl100k_base and exclude instructions/schema/transport overhead. They are
payload estimates, not provider usage or a forecast of repair/recheck outcomes.
"""
import json
import sys

import tiktoken

from app.llm.prompt_payloads import lean_plan_context, shared_rule_context


def main():
    with open(sys.argv[1]) as source:
        runs = json.load(source)
    encoding = tiktoken.get_encoding('cl100k_base')
    for run in runs:
        operation = run['operation']
        if operation not in ('campaign_plan', 'campaign_plan_verification'):
            continue
        old = json.loads(run['request']['input'])
        updated = dict(old)
        if operation == 'campaign_plan':
            # Saved request is already lean: reconstruct packages for the helper.
            context = old['brand_context']
            sources = {e['chunk_id']: e for e in context.get('evidence', [])}
            packages = {}
            for asset_id, package in context.get('asset_packages', {}).items():
                package = dict(package)
                package['evidence'] = [sources[i] for i in package.pop('evidence_ids', [])]
                ref = package.pop('rule_pack_ref', None)
                if ref:
                    package['rule_pack'] = context['rule_packs'][ref]
                packages[asset_id] = package
            updated['brand_context'] = lean_plan_context({**context, 'asset_packages': packages})
        else:
            updated['governing_rules'] = shared_rule_context(old['governing_rules'])
        before = len(encoding.encode(json.dumps(old)))
        after = len(encoding.encode(json.dumps(updated)))
        print(json.dumps({'operation': operation, 'run_id': run['id'],
                          'before_payload_tokens': before, 'after_payload_tokens': after,
                          'reduction_percent': round(100 * (before - after) / before, 1)}))


if __name__ == '__main__':
    main()
