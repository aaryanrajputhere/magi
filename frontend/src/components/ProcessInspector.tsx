import {useEffect,useRef,useState} from 'react';
import InspectorEvidence,{StepPreview,inputPreview,type Activity} from './InspectorEvidence';

type Run={id:string;operation:string;model:string;status:string;duration_ms:number|null;input_tokens:number|null;output_tokens:number|null;cost:string|null;request:unknown;response:unknown;error:unknown};
const steps:Record<string,{title:string;description:string}>={
 campaign_agent_step:{title:'Understand the campaign brief',description:'Read the request and campaign state, then choose the next tool.'},
 compact_campaign_context:{title:'Compact retrieved context',description:'Condense every retrieved chunk into attributed topic or channel context without resolving conflicts.'},
 campaign_requirements:{title:'Extract requested assets and constraints',description:'Read the full original conversation and record each requested deliverable, audience, date, byline, and constraint before planning searches.'},
 campaign_retrieval_brief:{title:'Prepare the retrieval brief',description:'Condense the conversation into the subject, assets, dates, and constraints used for search.'},
 campaign_conflict_review:{title:'Check retrieved chunks for conflicts',description:'Compare all retrieved evidence and governing guidance, with quotations from conflicting chunks.'},
 plan_retrieval:{title:'Plan retrieval questions',description:'Turn your brief into questions and search queries for the brand sources.'},
 campaign_message_route:{title:'Understand your message',description:'Decide whether your message needs a campaign plan or a quick answer.'},
 campaign_question_review:{title:'Review brand information',description:'Review retrieved chunks for one question using confirmed source authority. Up to three question reviews run concurrently.'},
 campaign_reconciliation:{title:'Compare the findings',description:'Compare question findings and their cited original passages for cross-question conflicts.'},
 campaign_plan:{title:'Create your campaign plan',description:'Turn the reviewed information into content angles and a proposed plan.'},
 generate_campaign_asset:{title:'Write a draft',description:'Write one piece of content from your approved plan.'},
};
const label=(value:string)=>value.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase());
function readable(value:unknown):unknown {
 if(typeof value!=='string')return value;
 try{return JSON.parse(value);}catch{return value;}
}
function ReadableValue({value}:{value:unknown}) {
 const parsed=readable(value);
 if(parsed===null||parsed===undefined)return <p>None recorded.</p>;
 if(Array.isArray(parsed))return parsed.length?<ol>{parsed.map((item,i)=><li key={i}><ReadableValue value={item}/></li>)}</ol>:<p>None.</p>;
 if(typeof parsed==='object')return <div>{Object.entries(parsed).map(([key,item])=><section key={key} style={{marginBottom:16}}><strong>{label(key)}</strong><ReadableValue value={item}/></section>)}</div>;
 return <p style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{typeof parsed==='boolean'?(parsed?'Yes':'No'):String(parsed)}</p>;
}
function responseContent(response:unknown):unknown {
 if(!response||typeof response!=='object')return response;
 const data=response as Record<string,unknown>;
 if(Array.isArray(data.output)) {
  const texts=data.output.flatMap(item=>{
   const content=(item as {content?:{text?:string}[]}).content;
   return Array.isArray(content)?content.flatMap(part=>typeof part.text==='string'?[readable(part.text)]:[]):[];
  });
  if(texts.length)return texts.length===1?texts[0]:texts;
  return data.output;
 }
 return data.output_text??response;
}
function outputPreview(run:Run):string {
 const content=responseContent(run.response);
 if(content&&typeof content==='object'&&!Array.isArray(content)) {
  const data=content as Record<string,unknown>;
  if(Array.isArray(data.queries))return `${data.queries.length} search queries planned`;
  if(Array.isArray(data.supported))return `${data.supported.length} findings · ${Array.isArray(data.restricted)?data.restricted.length:0} restrictions · ${Array.isArray(data.unresolved)?data.unresolved.length:0} unresolved`;
  if(typeof data.brief==='string')return data.brief.slice(0,100);
  if(Array.isArray(data.assets))return `${data.assets.length} assets ${run.operation==='campaign_requirements'?'requested':'proposed'}`;
 }
 const calls=toolEvents(run).filter(event=>event.type==='function_call');
 if(calls.length)return `Next action: ${calls.map(call=>call.name).join(', ')}`;
 return typeof content==='string'?content.slice(0,100):'';
}
type ToolEvent={type:string;name?:string;call_id?:string;arguments?:unknown;output?:unknown};
function toolEvents(run:Run):ToolEvent[] {
 const request=run.request as {input?:unknown}|null;
 const response=run.response as {output?:unknown}|null;
 return [request?.input,response?.output].flatMap(items=>Array.isArray(items)?items.filter((item):item is ToolEvent=>item!==null&&typeof item==='object'&&['function_call','function_call_output'].includes(item.type)):[]);
}
type SearchQuery={id:string;purpose:string;query:string;reason:string};
function retrievalBranches(run:Run,activity:Activity|null) {
 if(run.operation!=='plan_retrieval')return null;
 const saved=activity?.search_plans?.find(plan=>plan.run_id===run.id) as {plan?:{queries?:SearchQuery[]}}|undefined;
 const output=responseContent(run.response) as {queries?:SearchQuery[]}|null;
 const queries=saved?.plan?.queries??(Array.isArray(output?.queries)?output.queries:[]);
 const topic=queries.filter(query=>!query.id.startsWith('channel__')&&!['channel','voice'].includes(query.purpose));
 const channels=new Map<string,SearchQuery[]>();
 for(const query of queries.filter(query=>query.id.startsWith('channel__')||['channel','voice'].includes(query.purpose))) {
  const channel=query.id.startsWith('channel__')?label(query.id.split('__')[1]):'General';
  channels.set(channel,[...(channels.get(channel)??[]),query]);
 }
 return {topic,channels,saved:!!saved};
}
type DetailTab='request'|'response'|'tools'|'error';
function ActivityGraph({runs,activity,selected,onSelect}:{runs:Run[];activity:Activity|null;selected:string|undefined;onSelect:(id:string,tab:DetailTab)=>void}) {
 const [zoom,setZoom]=useState(1);
 const graphRef=useRef<HTMLElement>(null);
 const [expanded,setExpanded]=useState(false);
 const [preview,setPreview]=useState<{id:string;tab:DetailTab}|null>(null);
 const previewRun=runs.find(run=>run.id===preview?.id);
 useEffect(()=>{
  const sync=()=>setExpanded(document.fullscreenElement===graphRef.current);
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){setExpanded(false);setPreview(null);}};
  document.addEventListener('fullscreenchange',sync);
  document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('fullscreenchange',sync);document.removeEventListener('keydown',escape);};
 },[]);
 useEffect(()=>{
  if(!expanded)return;
  const previous=document.body.style.overflow;
  document.body.style.overflow='hidden';
  return()=>{document.body.style.overflow=previous;};
 },[expanded]);
 const toggleFullscreen=async()=>{
  if(expanded){if(document.fullscreenElement===graphRef.current)await document.exitFullscreen();setExpanded(false);}
  else {try{await graphRef.current?.requestFullscreen();setExpanded(true);}catch{setExpanded(true);}}
 };
 const showPreview=(target:EventTarget|null)=>{
  const node=target instanceof Element?target.closest<HTMLButtonElement>('button[data-run]'):null;
  if(node?.dataset.run&&node.dataset.tab)setPreview({id:node.dataset.run,tab:node.dataset.tab as DetailTab});
 };
 const branchCount=Math.max(0,...runs.map(run=>{const branches=retrievalBranches(run,activity);return branches?1+Math.max(1,branches.channels.size):0;}));
 const width=Math.max(850,runs.length*340+80),height=branchCount?700+branchCount*220:650;
 return <section ref={graphRef} className={`activity-graph${expanded?' activity-graph-expanded':''}`} aria-label="Campaign activity graph" onMouseOver={event=>showPreview(event.target)} onFocusCapture={event=>showPreview(event.target)}>
  <header className="activity-graph-toolbar"><div><strong>Execution map</strong><span>Hover or focus a node for details. Click to select.</span></div><div className="activity-graph-controls"><button aria-label="Zoom out" disabled={zoom<=0.5} onClick={()=>setZoom(z=>Math.max(0.5,z-0.1))}>−</button><output>{Math.round(zoom*100)}%</output><button aria-label="Zoom in" disabled={zoom>=1.5} onClick={()=>setZoom(z=>Math.min(1.5,z+0.1))}>+</button><button onClick={()=>setZoom(1)}>Reset</button><button aria-expanded={expanded} onClick={()=>void toggleFullscreen()}>{expanded?'Exit full screen':'Full screen ⛶'}</button></div></header>
  <div className={`activity-graph-body${previewRun?' has-preview':''}`}><div className="activity-graph-viewport">{!runs.length?<div className="activity-graph-empty"><strong>Waiting for campaign activity</strong><p>Requests and tool calls will appear here as connected nodes.</p></div>:<div style={{width:width*zoom,height:height*zoom}}><div className="activity-graph-canvas" style={{width,height,transform:`scale(${zoom})`}}>
   <svg className="activity-graph-edges" width={width} height={height} aria-hidden="true"><defs><marker id="activity-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#ac96c7"/></marker></defs>{runs.map((r,i)=>{const x=40+i*340;return <g key={r.id}><path d={`M ${x+130} 140 V 175 M ${x+130} 275 V 310`}/>{i<runs.length-1&&<path className="activity-order-edge" d={`M ${x+260} 225 H ${x+340}`}/ >}{toolEvents(r).length>0&&<path d={`M ${x+260} 355 H ${x+285} V 465 H ${x+260}`}/>}</g>;})}</svg>
   {runs.map((r,i)=>{const x=40+i*340;const calls=toolEvents(r).filter(e=>e.type==='function_call');const requirements=r.operation==='campaign_requirements'&&r.status==='completed'?responseContent(r.response) as {assets?:{id:string;format:string;purpose:string;timing?:string}[]}:null;const assets=Array.isArray(requirements?.assets)?requirements.assets:[];return <div key={r.id}>
    <div className="activity-column-label" style={{left:x,top:20}}>REQUEST {String(i+1).padStart(2,'0')}</div>
    <button data-run={r.id} data-tab="request" className="activity-node activity-input" style={{left:x,top:50}} onClick={()=>onSelect(r.id,'request')}><span className="activity-node-kind">↳ Input</span><strong>Instructions & context</strong><small className="activity-preview">{inputPreview(r.request).slice(0,150)||'View instructions and supplied context'}</small></button>
    <button data-run={r.id} data-tab="response" className={`activity-node activity-model ${r.status}`} style={{left:x,top:175}} onClick={()=>onSelect(r.id,'response')} aria-pressed={selected===r.id}><span className="activity-node-kind">✦ Model <span className="activity-status">{label(r.status)}</span></span><strong>{steps[r.operation]?.title??label(r.operation)}</strong><small>{r.model}</small></button>
    <button data-run={r.id} data-tab={r.error?'error':'response'} className={`activity-node activity-output${assets.length?' activity-requested-assets':''}`} style={{left:x,top:310}} onClick={()=>onSelect(r.id,r.error?'error':'response')}><span className="activity-node-kind">{r.error?'! Error':'↗ Output'}</span><strong>{r.error?'Request failed':r.status==='completed'?(outputPreview(r)||'Response ready'):'Awaiting response'}</strong>{assets.length>0&&<ol className="activity-asset-list">{assets.map(asset=><li key={asset.id}><span>{label(asset.format)} · {asset.purpose}</span>{asset.timing&&<time>{asset.timing}</time>}</li>)}</ol>}<small>{r.duration_ms===null?'In progress':`${(r.duration_ms/1000).toFixed(1)}s`} · {((r.input_tokens??0)+(r.output_tokens??0)).toLocaleString()} tokens</small></button>
    {toolEvents(r).length>0&&<button data-run={r.id} data-tab="tools" className="activity-node activity-tools" style={{left:x,top:420}} onClick={()=>onSelect(r.id,'tools')}><span className="activity-node-kind">⚒ Tools & results</span><strong>{calls.length?`${calls.length} tool call${calls.length===1?'':'s'}`:'Tool results'}</strong><small>{calls.map(e=>e.name||'Unnamed tool').join(', ')||'Returned to model'}</small></button>}
   </div>;})}
   {runs.map((run,index)=>{
    const branches=retrievalBranches(run,activity);if(!branches)return null;
    const left=40+index*340;
    const groups=[{title:'Topic questions',queries:branches.topic},...(branches.channels.size?[...branches.channels].map(([channel,queries])=>({title:`${channel} guidelines`,queries})):[{title:'Asset guidelines',queries:[]}])];
    return <div key={`branches-${run.id}`}>
     <svg className="activity-graph-edges" width={width} height={height} aria-hidden="true"><path d={`M ${left+130} 400 V 665 H ${left-15} V ${700+(groups.length-1)*220+90}`} fill="none" stroke="#ac96c7"/>{groups.map((group,i)=><path key={group.title} d={`M ${left-15} ${790+i*220} H ${left}`} fill="none" stroke="#ac96c7" markerEnd="url(#activity-arrow)"/>)}</svg>
     <div className="activity-column-label" style={{left,top:640}}>RETRIEVAL BRANCHES · {branches.saved?'SAVED PLAN':'MODEL OUTPUT · NOT VALIDATED'}</div>
     {groups.map((group,i)=><section key={group.title} className={`activity-retrieval-branch ${i===0?'topic-branch':'guidelines-branch'}`} style={{left,top:700+i*220}} aria-label={group.title}>
      <h4>{group.title}</h4><small>{group.queries.length} {branches.saved?'planned searches':'proposed searches'}</small>
      {group.queries.length?<div className="activity-query-list">{group.queries.map(query=><details key={query.id}><summary>{query.query}</summary><p>{query.reason}</p><small>{query.id} · {query.purpose}</small></details>)}</div>:<p>{branches.saved?'No searches recorded.':'Pending: search planning must succeed before format guidelines are added.'}</p>}
     </section>)}
    </div>;
   })}
  </div></div>}</div>
  {previewRun&&preview&&<aside className="activity-node-details" aria-label="Node details">
   <header><div><small>NODE DETAILS</small><h3>{steps[previewRun.operation]?.title??label(previewRun.operation)}</h3></div><button aria-label="Close node details" onClick={()=>setPreview(null)}>×</button></header>
   <p>{steps[previewRun.operation]?.description}</p>
   <div className="activity-detail-metadata"><span>{previewRun.model}</span><span>{label(previewRun.status)}</span><span>{previewRun.duration_ms==null?'In progress':`${(previewRun.duration_ms/1000).toFixed(1)}s`}</span><span>{((previewRun.input_tokens??0)+(previewRun.output_tokens??0)).toLocaleString()} tokens</span><span>Cost: {previewRun.cost==null?'Unknown':`$${previewRun.cost}`}</span></div>
   <div className="inspector-tabs" role="group" aria-label="Node detail view">{(['request','response','tools','error'] as const).map(t=><button key={t} aria-pressed={preview.tab===t} onClick={()=>setPreview({...preview,tab:t})}>{t==='request'?'Input':t==='response'?'Output':t==='tools'?'Tools':'Errors'}</button>)}</div>
   <div className="activity-detail-content" key={`${preview.id}-${preview.tab}`}>
    {preview.tab==='request'?<><StepPreview request={previewRun.request}/><details><summary>Instructions and full request</summary><ReadableValue value={previewRun.request}/></details></>:preview.tab==='tools'?<ReadableValue value={toolEvents(previewRun)}/>:preview.tab==='error'?<ReadableValue value={previewRun.error}/>:previewRun.operation==='campaign_requirements'?<RequestedAssets runs={[previewRun]}/>:<ReadableValue value={responseContent(previewRun.response)}/>}
    <details className="inspector-raw"><summary>Raw JSON</summary><pre>{JSON.stringify(preview.tab==='tools'?toolEvents(previewRun):preview.tab==='request'?previewRun.request:preview.tab==='error'?previewRun.error:previewRun.response,null,2)}</pre></details>
   </div>
   <button className="activity-select-details" onClick={async()=>{if(expanded)await toggleFullscreen();onSelect(preview.id,preview.tab);requestAnimationFrame(()=>document.querySelector('.inspector-detail')?.scrollIntoView({behavior:'smooth',block:'start'}));}}>Select this step in inspector</button>
  </aside>}</div>
  <footer className="activity-graph-legend"><span>✦ Model request</span><span>⚒ Tool activity</span><span>↗ Output</span><span>Dashed arrows show recorded request order</span></footer>
 </section>;
}
function RequestedAssets({runs}:{runs:Run[]}) {
 const extraction=[...runs].reverse().find(run=>run.operation==='campaign_requirements'&&run.status==='completed');
 const result=extraction?responseContent(extraction.response):null;
 if(!result||typeof result!=='object'||Array.isArray(result))return null;
 const requirements=result as {topic?:string;assets?:{id:string;format:string;audience:string;purpose:string;timing?:string;sender_or_byline?:string;constraints?:string[]}[];constraints?:string[];unknowns?:string[]};
 if(!Array.isArray(requirements.assets))return null;
 return <section className="inspector-requirements" aria-label="Requested campaign assets">
  <h3>Requested assets · {requirements.assets.length}</h3>
  <p>{requirements.topic}</p><p>Extracted from your brief before search planning. These are requested deliverables; the campaign plan still needs review and approval.</p>
  <div className="inspector-assets-table"><table><caption>Deliverables recorded by the requirements extraction step</caption><thead><tr><th scope="col">#</th><th scope="col">Asset and purpose</th><th scope="col">Audience</th><th scope="col">Sender / byline</th><th scope="col">Publish</th></tr></thead><tbody>{requirements.assets.map((asset,index)=><tr key={asset.id}><td>{index+1}</td><td><strong>{label(asset.format)}</strong><p>{asset.purpose}</p>{!!asset.constraints?.length&&<details><summary>Asset constraints</summary><ul>{asset.constraints.map((constraint,i)=><li key={i}>{constraint}</li>)}</ul></details>}</td><td>{asset.audience}</td><td>{asset.sender_or_byline||'Not specified'}</td><td>{asset.timing||'Not specified'}</td></tr>)}</tbody></table></div>
  {!!requirements.constraints?.length&&<details><summary>Campaign constraints · {requirements.constraints.length}</summary><ul>{requirements.constraints.map((constraint,index)=><li key={index}>{constraint}</li>)}</ul></details>}
  {!!requirements.unknowns?.length&&<details><summary>Open questions · {requirements.unknowns.length}</summary><ul>{requirements.unknowns.map((question,index)=><li key={index}>{question}</li>)}</ul></details>}
  <small>Source: requirements extraction run <code>{extraction?.id}</code></small>
 </section>;
}
export default function ProcessInspector({campaignId}:{campaignId:string}) {
 const [activity,setActivity]=useState<Activity|null>(null);
 const [runs,setRuns]=useState<Run[]>([]),[selected,setSelected]=useState(''),[tab,setTab]=useState<DetailTab>('response'),[error,setError]=useState('');
 useEffect(()=>{
  setRuns([]);setActivity(null);setSelected('');setError('');
  let alive=true;
  const controller=new AbortController();
  let refreshing=false;
  const refresh=async()=>{
   if(refreshing)return;
   refreshing=true;
   try {
    const [response,evidence]=await Promise.all([fetch(`/api/campaigns/${campaignId}/runs`,{signal:controller.signal}),fetch(`/api/campaigns/${campaignId}/activity`,{signal:controller.signal})]);
    if(!response.ok||!evidence.ok)throw Error('Could not load process steps and retrieval evidence');
    const result=await response.json();
    const activityResult=await evidence.json();
    if(alive){setRuns(result);setActivity(activityResult);setError('');}
   }catch(e){if(alive)setError(String(e));}finally{refreshing=false;}
  };
  void refresh();
  const timer=setInterval(()=>void refresh(),2000);
  return()=>{alive=false;controller.abort();clearInterval(timer);};
 },[campaignId]);
 const run=runs.find(r=>r.id===selected)||runs[runs.length-1];
 const step=run&&(steps[run.operation]??{title:label(run.operation),description:'Review what this step received and returned.'});
 const events=run?toolEvents(run):[];
 const content=run&&(tab==='response'?responseContent(run.response):tab==='error'?run.error:run.request);
 return <main className="campaign-workspace inspector-page">
  <header className="inspector-page-heading"><a href={`/#campaigns/${encodeURIComponent(campaignId)}`}>← Campaign</a><span>Campaign ID: <code>{campaignId}</code></span></header>
  <div id="campaign-inspector" className="inspector-shell">
   <header className="inspector-heading"><div><h2>Campaign activity</h2><p>Explore model requests, tool calls, and results</p></div><span className="inspector-live">● Updating every 2s</span></header>
   <div className="inspector-stats"><span><strong>{runs.length}</strong> LLM requests</span><span><strong>{runs.reduce((n,r)=>n+toolEvents(r).filter(e=>e.type==='function_call').length,0)}</strong> tool calls</span><span><strong>{runs.reduce((n,r)=>n+(r.input_tokens??0)+(r.output_tokens??0),0).toLocaleString()}</strong> tokens</span></div>
   {error&&<p role="alert">{error}</p>}
   {activity&&<InspectorEvidence activity={activity}/>}
   <ActivityGraph runs={runs} activity={activity} selected={run?.id} onSelect={(id,nextTab)=>{setSelected(id);setTab(nextTab);}}/>
   <div className="inspector-layout inspector-graph-layout">
    {run&&step&&<article className="inspector-detail"><h3>{step.title}</h3><p>{step.description}</p><StepPreview request={run.request}/>
     <div className="inspector-tabs" role="group" aria-label="Request information">{(['response','request','tools','error'] as const).map(t=><button key={t} aria-pressed={tab===t} onClick={()=>setTab(t)}>{t==='request'?'Full request':t==='response'?'Response':t==='tools'?'Tool calls & results':'Errors'}</button>)}</div>
     <div className="inspector-content">{tab==='tools'?(events.length?events.map((event,i)=><section key={i}><h4>{event.name||'Tool result'}</h4><ReadableValue value={event}/></section>):<p>No tool calls recorded in this request.</p>):tab==='error'&&!run.error?<p>No errors recorded.</p>:tab==='response'&&run.operation==='campaign_requirements'?<RequestedAssets runs={[run]}/>:<ReadableValue value={content}/>}</div>
     <details key={`${run.id}-${tab}`} className="inspector-raw"><summary>Raw JSON and metadata</summary><p>Run: {run.id} · Estimated cost: {run.cost==null?'Unknown':`$${run.cost}`}</p><pre>{JSON.stringify(tab==='tools'?events:run[tab],null,2)}</pre></details>
    </article>}
   </div>
  </div>
 </main>;
}
