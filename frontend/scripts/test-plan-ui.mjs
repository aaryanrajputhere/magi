import assert from 'node:assert/strict';
import {createServer} from 'vite';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
const server = await createServer({server:{middlewareMode:true}, appType:'custom'});
try {
 const {ChatPlan} = await server.ssrLoadModule('/src/components/CampaignWorkspace.tsx');
 const plan = {goal:'Awareness',questions:[],assumptions:[],constraints:[],assets:[{
  id:'a',format:'blog',audience:'Engineers',purpose:'Explain containment',hook:'Learn about containment',
  cta:'Read the article',cta_destination:'b',publish_after_asset_ids:['b'],timing:null}]};
 const render = (state,p=plan) => renderToStaticMarkup(createElement(ChatPlan,{
  plan:p,state,busy:false,onApprove(){},onAnswer(){},onRepair(){}}));
 const pending=render('planning_busy');
 assert.match(pending,/Learn about containment/);
 assert.doesNotMatch(pending,/Approve plan/);
 const available=render('awaiting_approval');
 assert.match(available,/Approve plan/);
 assert.match(available,/Destination:/);
 assert.match(available,/Publish after:/);
 const blocked=render('needs_plan_review',{...plan,recovery:{issues:[{asset_id:'a',field:'hook',explanation:'Unapproved claim'}]}});
 assert.doesNotMatch(blocked,/Unapproved claim|Fix plan|One more check/);
 assert.match(blocked,/Learn about containment/);
 assert.match(blocked,/Approve plan/);
 assert.match(blocked,/Generate 1 assets/);
 const form=render('awaiting_approval',{...plan,assets:[{...plan.assets[0],format:'landing_page',cta_destination:null,cta_destination_kind:'on_page_form'}]});
 assert.match(form,/On-page demo form/);
 console.log('Plan UI: 4 rendering scenarios passed');
} finally {await server.close();}
