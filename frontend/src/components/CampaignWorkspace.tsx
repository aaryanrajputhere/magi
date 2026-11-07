import type { Draft } from './Draft';
import { Fragment, useEffect, useRef, useState } from 'react';
import AssetPreview from './AssetPreview';
import MarkdownCopy from './MarkdownCopy';
import { createPortal } from 'react-dom';

type Trace = {name:string;status:string;details?:Record<string,unknown>;error?:string};
type Citation = {chunk_id:string;number:number;title:string;relative_path:string;heading_path:string[];start_line:number;end_line:number;content:string};
type AgentQuestion = {id:string;text:string;reason?:string;kind:"choices"|"text";options:string[];answer:string|null};
type ChatMessage = {question?:AgentQuestion;revision?:{asset_id:string;title:string;format:string;body:string;previous_body:string};plan_index?:number;role:string;text:string;paragraphs?:{text:string;source_ids:string[]}[];citations?:Record<string,Citation>;conflicts?:unknown[]};
type Generation = {phase?:string;last_revised_asset_id?:string;plan_index:number;active_asset:string|null;error:string|null;drafts:Draft[]};
type Requirements = {topic:string;topic_details?:{heading:string;content:string}[];constraints:string[];assets:{id:string;format:string;purpose:string;audience:string;timing:string|null;sender_or_byline:string|null;constraints:string[]}[]};
type Campaign = {title?:string;conflict_coverage?:{status:string};pending_conflict_question_ids?:string[];pending_question?:AgentQuestion|null;questions?:AgentQuestion[];memory_notice?:string;requirements?:Requirements;generation?:Generation;trace?:Trace[];id:string; brand_id:string; state:string; version:number; messages:ChatMessage[]; plans:Plan[]};
type Plan = {recovery?:{reason:string;issues:{asset_id:string|null;field:string;explanation:string}[]};extension?:{base_plan_index:number;added_asset_ids:string[]};goal:string; questions:(string|Omit<AgentQuestion,'id'|'answer'>)[]; assumptions:string[]; constraints:string[]; assets:{id:string;format:string;audience:string;purpose:string;angle?:string;hook?:string;cta:string;timing:string|null;cta_destination?:string|null;cta_destination_kind?:'asset'|'url'|'on_page_form'|null;publish_after_asset_ids?:string[]}[]};
type SearchPlan = {analysis?:{topic?:string;products?:string[];channels?:string[]};queries?:{id:string;purpose:string;query:string;reason:string}[]};
function campaignTitle(c?:Campaign) { return (c?.title || c?.requirements?.topic || c?.messages.find(m=>m.role==='user')?.text.split('\n')[0] || 'New campaign').replace(/^\s*#+\s*/, '').replace(/\*\*/g,'').replace(/^campaign brief:\s*/i,'').trim(); }
function QuestionCard({question,active,busy,onAnswer}:{question:AgentQuestion;active:boolean;busy:boolean;onAnswer:(answer:string)=>void}) {
  const [selected,setSelected] = useState('');
  const [details,setDetails] = useState('');
  const answer=question.kind==='choices'&&selected
    ? `${selected}${details.trim()?`\nAdditional details: ${details.trim()}`:''}`
    : details.trim();
  return <form className="agent-question" onSubmit={e=>{e.preventDefault();if(answer&&active&&!busy)onAnswer(answer);}}>
    <fieldset disabled={!active||busy}><legend>{question.text}</legend>
      {question.reason&&<p>{question.reason}</p>}
      {question.kind==='choices'&&<div className="question-options">{question.options.map(option=><label key={option}><input type="radio" name={question.id} checked={selected===option} onChange={()=>setSelected(option)}/>{option}</label>)}<label><input type="radio" name={question.id} checked={selected===''} onChange={()=>setSelected('')}/>Write my own response</label></div>}
      <label>{question.kind==='choices'&&selected?'Additional details (optional)':'Your answer'}<textarea value={details} onChange={e=>setDetails(e.target.value)} rows={3}/></label>
      {active?<button disabled={busy||!answer}>Submit answer</button>:<small>Answered or superseded</small>}
    </fieldset>
  </form>;
}

function BriefMessage({text}:{text:string}) {
  const [expanded,setExpanded] = useState(false);
  return text.length>500 ? <div className="submitted-brief">
    {!expanded&&<p>{text.replace(/[#*]/g,'').slice(0,220)}…</p>}
    <details onToggle={event=>setExpanded(event.currentTarget.open)}><summary>{expanded?'Collapse brief':'View full brief'}</summary><MarkdownCopy body={text}/></details>
  </div> : <MarkdownCopy body={text}/>;
}
function searchPlan(step:Trace):SearchPlan {
  return (step.details?.search_plan || step.details?.plan || {}) as SearchPlan;
}
function headline(step:Trace, topic:string) {
  const about=topic ? ` about ${topic}` : '';
  switch(step.name) {
    case 'Understand brief and plan searches': return topic ? `Understanding the campaign about ${topic}` : 'Understanding your brief and deciding what to look up';
    case 'Retrieve evidence': return `Retrieving brand information${about}`;
    case 'Add governing guidance': return `Gathering brand rules and restrictions${about}`;
    case 'Select relevant brand guidance': return `Selecting relevant brand guidance${about}`;
    case 'Review brand evidence': return `Reviewing facts, restrictions and conflicts${about}`;
    case 'Check conflicts': return `Checking claims and conflicting guidance${about}`;
    case 'Assemble context': return `Preparing source-backed context${about}`;
    case 'Propose campaign plan': return 'Building your campaign plan from the evidence';
    default: return step.name;
  }
}
function searchHeadlines(step:Trace) {
  const groups=step.details?.groups as {query:{id:string;purpose:string;query:string};results:unknown[]}[]|undefined;
  if(groups) return groups.map(g=>({id:g.query.id,text:g.query.query,count:g.results.length}));
  return (searchPlan(step).queries||[]).map(q=>({id:q.id,text:q.query,count:null}));
}
async function api(path:string, body?:unknown) {
  const response = await fetch(`/api/${path}`, body === undefined ? undefined : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if (!response.ok) { const error = await response.json(); throw new Error(error.detail || 'Request failed'); }
  return response.json();
}
export default function CampaignWorkspace() {
  const [mobileView, setMobileView] = useState('chat');
  const [campaigns,commitCampaigns]=useState<Campaign[]>([]);
  const campaignsRef=useRef<Campaign[]>([]);
  function setCampaigns(update:Campaign[]|((previous:Campaign[])=>Campaign[])) {
    const next=typeof update==='function' ? update(campaignsRef.current) : update;
    // Publish new IDs before changing the URL: hashchange can precede React's render.
    campaignsRef.current=next;
    commitCampaigns(next);
  }
  const [brands,setBrands]=useState<{id:string;name:string}[]>([]);
  const [brand,setBrand]=useState('');
  const [selected,setSelected]=useState('');
  const [drafts,setDrafts]=useState<Record<string,string>>({});
  const text=drafts[selected]||'';
  const setText=(value:string)=>setDrafts(old=>({...old,[selected]:value}));
  const [source,setSource]=useState<{citation:Citation;conflicts:unknown[];references:Record<string,Citation>}|null>(null);
  const [busy,setBusy]=useState(false);
  const responseEpoch = useRef(0);
  const [error,setError]=useState('');
  const campaign=campaigns.find(c=>c.id===selected);
  const topic=campaign?.trace?.map(s=>searchPlan(s).analysis?.topic).find(Boolean)||'';
  const compactTracks = campaign?.trace?.find(step => step.name === 'Compact topic and channel context' && step.status === 'completed')?.details?.tracks as Record<string,{context_method?:string;input_tokens?:number|null;decision_reason?:string;report:{supported:{text:string;source_ids:string[]}[]};evidence:unknown[]}> | undefined;
  const retrievalQueries = campaign?.trace?.map(step => searchPlan(step).queries).find(queries => queries?.length) || [];
  function selectCampaign(id:string) {
    setSelected(id);
    setError('');
    window.location.hash=id ? `campaigns/${encodeURIComponent(id)}` : 'campaigns';
  }
  useEffect(()=>{
    let loaded=false;
    const update=()=>{
      if(!loaded||window.location.hash==='#sources') return;
      const hash=window.location.hash;
      const match=campaignsRef.current.find(c=>`#campaigns/${encodeURIComponent(c.id)}`===hash);
      setSelected(match?.id||'');
      if(match) setBrand(match.brand_id);
      setError(hash.startsWith('#campaigns/')&&!match ? 'Campaign not found.' : '');
    };
    window.addEventListener('hashchange',update);
    Promise.all([api('campaigns?summary=true'),api('brands')]).then(([c,b])=>{
      loaded=true;setCampaigns(c);setBrands(b);setBrand(b[0]?.id||'');
      if((!window.location.hash||window.location.hash==='#campaigns')&&c[0]) {
        window.history.replaceState(null,'',`#campaigns/${encodeURIComponent(c[0].id)}`);
      }
      update();
    }).catch(e=>setError(e.message));
    return ()=>window.removeEventListener('hashchange',update);
  },[]);
  useEffect(()=>{
    if (!selected) return;
    let active=true;
    let timer:ReturnType<typeof setTimeout>;
    const refresh=async()=>{
      try {
        const epoch = responseEpoch.current;
        const c=await api(`campaigns/${selected}`);
        if(active && epoch === responseEpoch.current){
          setCampaigns(old=>old.map(x=>x.id===c.id?c:x));
          if(busy || ['planning_busy','generating'].includes(c.state))timer=setTimeout(()=>void refresh(),2000);
        }
      }catch(e){if(active){setError(String(e));timer=setTimeout(()=>void refresh(),2000);}}

    };
    void refresh();
    return ()=>{active=false;clearTimeout(timer);};
  },[selected,campaign?.state,busy]);
  useEffect(()=>{setSource(null);setMobileView('chat');},[selected]);
  async function act(path:string,body:unknown) {
    setBusy(true);setError('');
    responseEpoch.current += 1;
    try {
      const c=await api(path,body);
      responseEpoch.current += 1;
      setCampaigns(old=>[c,...old.filter(x=>x.id!==c.id)]);
      selectCampaign(c.id);setText('');
    } catch(e) {
      responseEpoch.current += 1;
      setError(String(e));
      // Failed mutations can still persist progress and a terminal error state.
      const id = path.match(/^campaigns\/([^/]+)/)?.[1];
      if(id) {
        try {
          const c=await api(`campaigns/${id}`);
          responseEpoch.current += 1;
          setCampaigns(old=>old.map(x=>x.id===c.id?c:x));
        } catch {
          // Preserve the action error; polling retries the state read separately.
        }
      }
    } finally {setBusy(false);}
  }

  const lastUserIndex = campaign?.messages.map(m => m.role).lastIndexOf('user') ?? -1;
  const firstUserIndex = campaign?.messages.findIndex(m => m.role === 'user') ?? -1;
  const initialTurn = lastUserIndex === firstUserIndex;
  const progressMessageIndex = campaign?.messages.findIndex((m, i) => i > lastUserIndex && m.role === 'assistant') ?? -1;
  const isRevision = campaign?.trace?.some(step => step.name === 'Revise draft');
  const planningProgress = (campaign?.trace && <div className="planning-headlines" aria-label="Planning progress">
      {campaign.trace.map((step,i)=><details className={`planning-step ${step.status}`} key={`${campaign.id}-${i}`}>
        <summary><span className="step-indicator" aria-hidden="true">{step.status==='completed'?'✓':step.status==='failed'?'!':'◌'}</span><span>{headline(step,topic)}</span><span className="step-state">{step.status==='running'?'Working…':step.status==='failed'?'Needs attention':'Done'}</span></summary>
        {(step.name==='Retrieve evidence'||step.name==='Understand brief and plan searches')&&<ul className="search-headlines">{searchHeadlines(step).map(q=><li key={q.id}><span>{step.status==='completed'?'Retrieved context for':'Looking up'}: {q.text}</span>{q.count!==null&&<small>{q.count} source passages</small>}</li>)}</ul>}
        {step.error&&<p className="step-error">{step.error}</p>}
        {step.name==='Select relevant brand guidance'&&step.details&&typeof step.details.input_characters==='number'&&<div className="selection-summary">
          {Array.isArray(step.details.validation_warnings)&&step.details.validation_warnings.length>0&&<p>Kept {step.details.validation_warnings.length} additional sections because their exclusions could not be verified.</p>}
          <p>{Number(step.details.original_characters).toLocaleString()} → {Number(step.details.input_characters).toLocaleString()} characters</p>
          <p>{step.details.coverage_complete ? 'Selection coverage complete' : 'Coverage needs review'}</p>
          {((step.details.coverage_gaps||[]) as string[]).map((gap,i)=><p key={i}>{gap}</p>)}
        </div>}
        {step.name==='Review brand evidence'&&step.details?.report!=null&&<div className="evidence-report">{(step.details.report as {coverage_complete?:boolean}).coverage_complete===false&&<p role="alert">Evidence review is incomplete. Check the limitations below.</p>}{Object.entries(step.details.report as Record<string,unknown>).filter((entry):entry is [string,unknown[]]=>Array.isArray(entry[1])).map(([category,items])=><section key={category}><h4>{category}</h4>{items.map((item,i)=><p key={i}>{typeof item==='string'?item:((item as {text?:string;explanation?:string;status?:string}).text||(item as {explanation:string}).explanation)}{typeof item==='object'&&item!==null&&'status' in item?` (${String(item.status)})`:''}</p>)}</section>)}</div>}
        {step.details&&<details className="step-debug"><summary>Inspect evidence</summary><pre>{JSON.stringify(step.details,null,2)}</pre></details>}
      </details>)}
      {campaign.state==='planning_error'&&<p className="planning-stopped">Planning paused. Expand the highlighted step for details.</p>}
    </div>);
  return <section className="campaign-workspace" aria-label="Campaign planning">{campaign?.memory_notice&&<p role="status">{campaign.memory_notice}</p>}
    {document.getElementById('campaign-navigation') && createPortal(<>
      <button className="new-campaign" disabled={busy||!brand||campaign?.state==='generating'} onClick={()=>{selectCampaign('');}}>＋ New campaign</button>
      <p className="history-label">Your campaigns</p>
      <nav aria-label="Campaign history">{campaigns.filter(c=>c.brand_id===brand).map(c=><button className={selected===c.id?'selected':''} key={c.id} title={campaignTitle(c)} onClick={()=>{selectCampaign(c.id);setBrand(c.brand_id);}}>{campaignTitle(c)}</button>)}</nav>
    </>,document.getElementById('campaign-navigation')!)}
    {source&&<SourceDrawer source={source} onClose={()=>setSource(null)}/>}
    <div className="campaign-toolbar"><span title={campaignTitle(campaign)}>{campaignTitle(campaign)}</span>{campaign&&<>
      {campaign.requirements&&(campaign.plans.length>0||campaign.conflict_coverage?.status==='incomplete')&&!campaign.pending_conflict_question_ids?.length&&!campaign.generation?.drafts.length&&!['planning_busy','generating'].includes(campaign.state)&&<button disabled={busy} onClick={()=>act(`campaigns/${campaign.id}/review`,{version:campaign.version})}>{campaign.plans.length?'Review evidence':'Retry evidence review'}</button>}
      {!!campaign.generation?.drafts.length && <button disabled={busy || campaign.state==='generating'}
        onClick={()=>void act(`campaigns/${campaign.id}/recheck-drafts`,{version:campaign.version})}>Recheck & repair saved drafts</button>}
      <a className="inspect-link" href={`/inspect/${encodeURIComponent(campaign.id)}`} target="_blank" rel="noopener noreferrer">Inspect ↗</a>
    </>}</div>
    {error&&<p role="alert">{error}</p>}
    <div className="preview-view-switch" role="group" aria-label="Workspace view"><button aria-pressed={mobileView === 'chat'} onClick={() => setMobileView('chat')}>Chat</button><button aria-pressed={mobileView === 'preview'} onClick={() => setMobileView('preview')}>Preview</button></div><div className={`workspace inline-plan-workspace preview-layout view-${mobileView}`}><div className="chat-panel"><div className="panel-heading">Campaign conversation</div>
      <div className="conversation-scroll">{campaign?.messages.map((m,i)=><Fragment key={i}><div className={`message ${m.role}`} style={{whiteSpace:'pre-wrap'}}>{initialTurn && i === progressMessageIndex && (isRevision ? <details className="revision-progress"><summary>{campaign.trace?.some(step=>step.status==='failed') ? 'Edit needs attention' : campaign.trace?.some(step=>step.status==='running') ? 'Updating draft…' : 'Edit saved'}</summary>{planningProgress}</details> : planningProgress)}{m.paragraphs?<>{m.paragraphs.slice(0,1).map((p,j)=><p key={j}>{p.text}</p>)}{m.paragraphs.length>1&&<details><summary>Claim guidance and evidence</summary>{m.paragraphs.slice(1).map((p,j)=>{const paragraph=<p key={j}>{p.text} {Array.from(new Set(p.source_ids)).map(id=>m.citations?.[id]&&<button className="citation-chip" key={id} aria-label={`Source ${m.citations[id].number}: ${m.citations[id].title}`} onClick={()=>setSource({citation:m.citations![id],conflicts:m.conflicts||[],references:m.citations!})}>[{m.citations[id].number}]</button>)}</p>;return paragraph;})}</details>}</> :m.revision?<section className="revision-result" aria-label="Updated draft"><h3>Updated · {m.revision.format === 'x' ? 'X' : m.revision.format.replaceAll('_',' ')} · {m.revision.title || m.revision.asset_id}</h3><MarkdownCopy body={m.revision.body}/><small>{m.revision.body.length} characters{m.revision.body.length < m.revision.previous_body.length ? ` · shortened from ${m.revision.previous_body.length}` : ''}</small><details><summary>View changes</summary><h4>Previous copy</h4><MarkdownCopy body={m.revision.previous_body}/><h4>Saved copy</h4><MarkdownCopy body={m.revision.body}/></details></section>:m.question?<QuestionCard question={m.question} active={campaign.pending_question?.id===m.question.id || !!campaign.pending_conflict_question_ids?.includes(m.question.id)} busy={busy} onAnswer={answer=>void act(`campaigns/${campaign.id}/messages`,{text:answer,question_id:m.question!.id,version:campaign.version})}/>:m.role==='user'?<BriefMessage text={m.text}/>:<MarkdownCopy body={m.text}/>}{m.role==='assistant'&&(()=>{
        const index=m.plan_index ?? campaign.plans.findIndex(p=>(p as Plan & {reply?:string}).reply===m.text);
        const proposed=campaign.plans[index];
        if(!proposed)return null;
        const current=index===campaign.plans.length-1;
        const generation = campaign.generation;
        const generated = campaign.state === 'needs_review' && generation && !generation.error
          && generation.plan_index === index && !generation.active_asset
          && proposed.assets.every(asset => generation.drafts.some(draft => draft.asset_id === asset.id));
        return <ChatPlan onRepair={()=>act(`campaigns/${campaign.id}/repair-plan`,{version:campaign.version})} hasPendingQuestion={!!campaign.pending_question && campaign.messages.some(message=>message.question?.id===campaign.pending_question?.id && !message.paragraphs && !message.revision)} plan={proposed} state={current?(generated?'ready':campaign.state):'superseded'} busy={busy} onAnswer={answer=>act(`campaigns/${campaign.id}/messages`,{text:answer,version:campaign.version})} onApprove={()=>act(`campaigns/${campaign.id}/execute`,{version:campaign.version})}/>;
      })()}</div>{i === firstUserIndex && !isRevision && campaign?.requirements&&<details className="chat-plan brief-summary" aria-label="Extracted brief"><summary className="brief-summary-heading"><span className="brief-summary-title">{campaign.requirements.topic}</span><span className="brief-summary-count">{campaign.requirements.assets.length} assets</span></summary><div className="brief-summary-content"><h3>Topic</h3><p>{campaign.requirements.topic}</p><details><summary>Full campaign context</summary>{campaign.requirements.topic_details?.map((s,i)=><section key={i}><h4>{s.heading}</h4><p style={{whiteSpace:'pre-wrap'}}>{s.content}</p></section>)}{campaign.requirements.constraints.map((c,i)=><p key={i}>{c}</p>)}</details><h3>Requested assets · {campaign.requirements.assets.length}</h3>{campaign.requirements.assets.map((a,i)=><details key={a.id}><summary>{i+1}. {a.format.replaceAll('_',' ')} · {a.purpose}</summary><p>Audience: {a.audience}</p><p>Sender / byline: {a.sender_or_byline||'Not specified'}</p><p>Publish: {a.timing||'Not specified'}</p>{a.constraints.map((c,j)=><p key={j}>{c}</p>)}</details>)}</div></details>}{i === firstUserIndex && retrievalQueries.length > 0 && <details className="chat-plan retrieval-questions" aria-label="Retrieval questions"><summary>Retrieval questions · {retrievalQueries.length}</summary>{[{title:'Topic questions',queries:retrievalQueries.filter(q=>!q.id.startsWith('channel__'))},{title:'Asset guidelines',queries:retrievalQueries.filter(q=>q.id.startsWith('channel__'))}].filter(group=>group.queries.length>0).map(group=><section key={group.title}><h4>{group.title}</h4><ol>{group.queries.map(q=><li key={q.id}><p>{q.query}</p><details><summary>Why this search?</summary><p>{q.reason}</p></details></li>)}</ol></section>)}</details>}{i === firstUserIndex && compactTracks && <details className="chat-plan"><summary>Compact contexts</summary>{Object.entries(compactTracks).map(([name,track])=><details key={name}><summary>{name === 'topic' ? 'Topic context' : name.replaceAll('_',' ') + ' context'} · {track.evidence.length} chunks{track.context_method ? ` · ${track.context_method}` : ''}</summary><p>{track.decision_reason}{track.input_tokens != null ? ` · ${track.input_tokens} tokens` : ''}</p>{track.report.supported.length ? track.report.supported.map((entry,index)=><div key={index}><p>{entry.text}</p><small>Sources: {entry.source_ids.join(', ')}</small></div>) : <p>No evidence retrieved</p>}</details>)}</details>}</Fragment>)}{initialTurn && progressMessageIndex === -1 && (isRevision ? <details><summary>Updating draft…</summary>{planningProgress}</details> : planningProgress)}{!campaign&&<p>What are we planning today?</p>}{!initialTurn && (busy || campaign?.state==='planning_busy') && <p role="status">{campaign?.generation?.drafts.length ? 'Updating campaign…' : 'Updating plan…'}</p>}{initialTurn&&busy&&!campaign?.trace?.length&&<p role="status">Reviewing brand evidence and planning…</p>}</div>
      <form className="composer" onSubmit={e=>{e.preventDefault();if(!text.trim()||busy||campaign?.state==='generating')return; if(campaign) void act(`campaigns/${campaign.id}/messages`,{text,version:campaign.version}); else {setBusy(true);api('campaigns',{brand_id:brand}).then(c=>{setCampaigns(old=>[c,...old]);selectCampaign(c.id);return act(`campaigns/${c.id}/messages`,{text,version:c.version});}).catch(e=>setError(String(e))).finally(()=>setBusy(false));}}}><label htmlFor="campaign-message">Brief or plan adjustment</label><textarea placeholder={campaign ? "Paste your brief or describe a change to the plan…" : "Describe your campaign…"} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();e.currentTarget.form?.requestSubmit();}}} rows={2} id="campaign-message" value={text} onChange={e=>{setText(e.target.value);e.target.style.height='auto';e.target.style.height=`${Math.min(e.target.scrollHeight,156)}px`;}} disabled={busy||!brand||campaign?.state==='generating'}/><button disabled={busy||!brand||!text.trim()||campaign?.state==='generating'}>Send</button><small className="composer-hint">Enter to send · Shift+Enter for a new line</small></form>
    </div><AssetPreview onRechecking={setBusy} campaignId={campaign?.id} version={campaign?.version} onUpdate={value => { responseEpoch.current += 1; const updated = value as Campaign; setCampaigns(old => old.map(c => c.id === updated.id ? updated : c)); }} revisedAssetId={campaign?.generation?.last_revised_asset_id} key={selected || 'new'} drafts={campaign?.generation?.drafts ?? []} brandName={brands.find(b => b.id === (campaign?.brand_id || brand))?.name || 'Brand'} generating={campaign?.state === 'generating'} rechecking={campaign?.generation?.phase === 'rechecking'} error={campaign?.generation?.error}/></div>
  </section>;
}

function SourceDrawer({source,onClose}:{source:{citation:Citation;conflicts:unknown[];references:Record<string,Citation>};onClose:()=>void}) {
  useEffect(()=>{
    const previous=document.activeElement as HTMLElement|null;
    const dialog=document.getElementById('source-dialog') as HTMLDialogElement;
    dialog.showModal();
    return ()=>{dialog.close();previous?.focus();};
  },[]);
  const c=source.citation;
  return <dialog id="source-dialog" className="source-drawer" onCancel={onClose} aria-labelledby="source-title">
    <button onClick={onClose} autoFocus>Close source</button><h2 id="source-title">{c.title||c.relative_path}</h2>
    <p>{c.relative_path} · lines {c.start_line}–{c.end_line}</p><p>{c.heading_path.join(' › ')}</p>
    <small>Source snapshot used for this response</small><pre>{c.content}</pre>
    {source.conflicts.filter(v=>(v as {source_ids?:string[]}).source_ids?.includes(c.chunk_id)).map((v,i)=>{
      const conflict=v as {explanation:string;status:string;basis?:string[];source_ids:string[];preferred_source_id:string|null};
      return <section key={i}><h3>Conflicting evidence · {conflict.status}</h3><p>{conflict.explanation}</p><p>{conflict.basis?.join(', ')}</p>{conflict.source_ids.map(id=>{const ref=source.references[id];return ref&&<details key={id}><summary>{ref.title||ref.relative_path}{id===conflict.preferred_source_id?' · Governing evidence':' · Conflicting evidence'}</summary><p>{ref.relative_path} · lines {ref.start_line}–{ref.end_line}</p><pre>{ref.content}</pre></details>;})}</section>;
    })}
  </dialog>;
}

export function ChatPlan({plan,state,busy,onApprove,onAnswer,hasPendingQuestion=false}:{plan:Plan;state:string;busy:boolean;onApprove:()=>void;onAnswer:(answer:string)=>void;onRepair:()=>void;hasPendingQuestion?:boolean}) {
  return <section className="chat-plan" aria-label="Proposed campaign plan"><h3>Proposed campaign plan</h3><p>{plan.goal}</p>
    {!hasPendingQuestion&&plan.questions.length>0&&<section aria-label="Essential questions"><h4>To finish the plan</h4>{plan.questions.map((q,i)=><Fragment key={i}>{typeof q==='string'?<ClarificationQuestion question={q} disabled={busy||state!=='clarifying'} onAnswer={onAnswer} index={i}/>:<QuestionCard question={{...q,id:`plan-question-${i}`,answer:null}} active={state==='clarifying'} busy={busy} onAnswer={onAnswer}/>}</Fragment>)}</section>}
    {plan.assets.length>0&&<h4>Content angles</h4>}
    {plan.extension&&<p>Your {plan.assets.length-plan.extension.added_asset_ids.length} existing posts will be kept.</p>}
    {plan.assets.map((a,i)=>plan.extension&&!plan.extension.added_asset_ids.includes(a.id)?null:<article key={a.id}><h4>{i+1}. {a.angle||a.format.replace('_',' ')}</h4>{a.angle&&<small>{a.format === 'x' ? 'X' : a.format.replaceAll('_',' ')} · {a.audience}{a.timing ? ` · ${a.timing}` : ''}</small>}{a.hook&&<p><strong>Suggested opening:</strong> {a.hook}</p>}<details><summary>Asset details</summary><p>{a.purpose}</p><p><strong>CTA:</strong> {a.cta}</p>{(a.cta_destination||a.cta_destination_kind==='on_page_form')&&<p><strong>Destination:</strong> {a.cta_destination_kind==='on_page_form'?'On-page demo form':a.cta_destination}</p>}{!!a.publish_after_asset_ids?.length&&<p><strong>Publish after:</strong> {a.publish_after_asset_ids.join(', ')}</p>}</details></article>)}
    {plan.assumptions.length>0&&<details><summary>Assumptions</summary>{plan.assumptions.map((a,i)=><p key={i}>{a}</p>)}</details>}
    {plan.constraints.length>0&&<details><summary>Brand and campaign constraints</summary>{plan.constraints.map((a,i)=><p key={i}>{a}</p>)}</details>}
    {['needs_plan_review','awaiting_approval','approved','generation_error','needs_review'].includes(state)&&<div className="plan-approval"><p>Generate {plan.extension?.added_asset_ids.length ?? plan.assets.length} assets</p><button className="approve-plan" disabled={busy} onClick={onApprove}>Approve plan</button></div>}
    {state==='generating'&&<p role="status">Generating the approved drafts…</p>}{state==='ready'&&<p>✓ Drafts ready in preview.</p>}{state==='needs_review'&&<p role="status">Some drafts need review. Open their previews for the required corrections.</p>}
    {state==='superseded'&&<small>Previous plan version</small>}
  </section>;
}

function ClarificationQuestion({question,disabled,onAnswer,index}:{question:string;disabled:boolean;onAnswer:(answer:string)=>void;index:number}) {
  const [selected,setSelected]=useState('');
  const [other,setOther]=useState('');
  const matches=Array.from(question.matchAll(/(?:^|\n)\s*([A-C])[).]\s+([^\n]+)/g));
  const choices=matches.map(match=>({id:match[1],text:match[2].trim()}));
  const isMultipleChoice=choices.length>=2&&choices.length<=3&&choices.every((choice,i)=>choice.id===String.fromCharCode(65+i));
  if(!isMultipleChoice) return <p>{question}</p>;
  const prompt=question.slice(0,matches[0].index).trim();
  const answer=selected==='other'?other.trim():choices.find(choice=>choice.id===selected)?.text;
  return <form className="clarification-question" onSubmit={event=>{
    event.preventDefault();
    if(!disabled&&answer) onAnswer(`${prompt}\nAnswer: ${selected==='other'?answer:`${selected}) ${answer}`}`);
  }}>
    <fieldset disabled={disabled}>
      <legend>{prompt}</legend>
      {choices.map(choice=><label className="clarification-choice" key={choice.id}>
        <input type="radio" name={`clarification-${index}`} value={choice.id} checked={selected===choice.id} onChange={()=>setSelected(choice.id)}/>
        <span>{choice.text}</span>
      </label>)}
      <label className="clarification-choice"><input type="radio" name={`clarification-${index}`} value="other" checked={selected==='other'} onChange={()=>setSelected('other')}/><span>Other response</span></label>
      {selected==='other'&&<label className="clarification-other">Your response<textarea value={other} onChange={event=>setOther(event.target.value)} required/></label>}
      <button type="submit" disabled={disabled||!answer}>Submit answer</button>
    </fieldset>
  </form>;
}
