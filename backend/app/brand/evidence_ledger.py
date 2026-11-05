"""Retain original retrieved passages across campaign turns."""
import hashlib


def retain_evidence(data, passages):
    ledger = dict(data.get('evidence_ledger', {}))
    for passage in passages:
        identifier = passage['chunk_id']
        digest = hashlib.sha256(passage.get('content', '').encode()).hexdigest()
        key = f'{identifier}:{digest}'
        ledger.setdefault(key, dict(passage))
    data['evidence_ledger'] = ledger
    return list(ledger.values())


def campaign_evidence(campaign, passages):
    data = dict(campaign.data)
    retained = retain_evidence(data, passages)
    campaign.data = data
    return retained
