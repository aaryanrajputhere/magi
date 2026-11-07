from copy import deepcopy
from types import SimpleNamespace

from app.brand.approved_hooks import missing_facts, supporting_sources
from app.brand.draft_validation import check
from app.llm.asset_context_router import route_context, routed_package

HOOK = 'The median time to contain a runtime incident is 7.4 hours [Report, 2026, https://example.test/report].'
CLAIM = {'chunk_id':'claim','content':'Approved: the median time to contain a runtime incident is 7.4 hours.',
         'relative_path':'brand/legal/approved-claims.md','heading_path':['Claims approved as written']}


def test_numeric_hook_requires_actual_copy_and_preserves_meaning_units():
    asset={'hook':HOOK}
    assert missing_facts(asset,'Containment is important.')==['7.4 hours']
    assert missing_facts(asset,'41% take more than six hours.')==['7.4 hours']
    assert missing_facts(asset,'The median is 7.4 days.')==['7.4 hours']
    assert missing_facts(asset,'A 7.4-hour median containment time.')==[]
    assert missing_facts({'hook':'41% of teams [Report, 2026].'},'41% of teams.')==[]


def test_number_alone_or_channel_example_is_not_source_support():
    example={**CLAIM,'chunk_id':'example','relative_path':'brand/channels/linkedin.md'}
    assert supporting_sources({'hook':HOOK},{'example':example})==[]
    unrelated={**CLAIM,'content':'The average deployment takes 7.4 hours.'}
    assert supporting_sources({'hook':HOOK},{'claim':unrelated})==[]
    assert supporting_sources({'hook':HOOK},{'claim':CLAIM,'example':example})==['claim']


def test_router_cannot_drop_support_for_an_approved_statistic():
    voice={'chunk_id':'voice','content':'Write clearly.','relative_path':'brand/voice/style.md'}
    packages={'a':{'evidence':[voice,CLAIM],'rule_pack':{'format':'linkedin','limits':[]}}}
    before=deepcopy(packages)
    plan={'assets':[{'id':'a','format':'linkedin','hook':HOOK}]}
    def run(request,output_model):
        # Sorted source IDs are claim=s0, voice=s1; simulate dropped claim.
        return SimpleNamespace(run_id='routing',parsed=output_model.model_validate({'assets':[{
            'asset_id':'a','writing':['s1'],'validation':['s1'],'image':[],
            'production_dependencies':[]}]}))
    routing=route_context(SimpleNamespace(run=run),SimpleNamespace(magi_retrieval_planner_model='test'),
        SimpleNamespace(id='c',brand_id='b'),plan,packages)
    for task in ('writing','validation'):
        assert 'claim' in routing['assets']['a'][task]
        assert {p['chunk_id']for p in routed_package(packages['a'],routing,'a',task,packages)['evidence']}=={'claim','voice'}
    assert packages==before


def test_missing_hook_cannot_pass_even_when_model_reports_no_findings():
    package={'rule_pack':{'format':'linkedin','limits':[],'evidence':[CLAIM]}}
    draft={'body':'Detection identifies an incident.','source_ids':['claim']}
    def run(request,output_model):
        return SimpleNamespace(run_id='review',parsed=output_model.model_validate({'findings':[]}))
    errors,_=check(SimpleNamespace(run=run),SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c',brand_id='b',data={'messages':[]}),{'id':'a','hook':HOOK},draft,package)
    assert any('7.4 hours' in e for e in errors)


def test_missing_support_blocks_publication_instead_of_forcing_an_unsupported_claim():
    package={'rule_pack':{'format':'linkedin','limits':[],'evidence':[]}}
    def run(request,output_model):
        return SimpleNamespace(run_id='review',parsed=output_model.model_validate({'findings':[]}))
    errors,_=check(SimpleNamespace(run=run),SimpleNamespace(magi_conflict_review_model='test'),
        SimpleNamespace(id='c',brand_id='b',data={'messages':[]}),{'id':'a','hook':HOOK},
        {'body':'Containment.','source_ids':[]},package)
    assert errors==[]
    assert package['dependency_records'][0]['kind']=='missing_evidence'
