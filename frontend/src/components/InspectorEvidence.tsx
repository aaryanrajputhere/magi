type Data = Record<string, unknown>;
export type Activity = {search_plans?:Data[];brief:string;state:string;trace:{name:string;status:string;error?:string}[];retrievals:Data[];selections:Data[]};
const object=(v:unknown):Data=>v!==null&&typeof v==='object'&&!Array.isArray(v)?v as Data:{};
const list=(v:unknown):Data[]=>Array.isArray(v)?v.map(object):[];
const text=(v:unknown)=>v==null?'':String(v);
export function parseInput(value:unknown):unknown {
 if(typeof value!=='string')return value;
 try{return JSON.parse(value);}catch{return value;}
}
export function inputPreview(request:unknown):string {
 const input=parseInput(object(request).input);
 if(Array.isArray(input)) {
  for(const message of input) {
   const content=parseInput(object(message).content);const data=object(content);
   if(data.request)return text(data.request);
   if(typeof content==='string')return content;
  }
 }
 const data=object(input);
 const brief=parseInput(data.brief);
 if(Array.isArray(brief))return brief.map(message=>text(object(message).text||object(message).content)).filter(Boolean).join('\n\n');
 return text(data.brief||object(data.question).query||data.instruction||(typeof input==='string'?input:''));
}
function Chunk({chunk}:{chunk:Data}) {
 return <details className="evidence-chunk"><summary><strong>{chunk.rank!=null?`#${chunk.rank} · `:''}{text(chunk.title||chunk.relative_path||chunk.chunk_id)}</strong><span>{Array.isArray(chunk.heading_path)?chunk.heading_path.join(' / '):''}</span></summary>
  <div className="evidence-metadata"><span>{text(chunk.relative_path)}</span><span>Lines {text(chunk.start_line)}–{text(chunk.end_line)}</span>{chunk.score!=null&&<span>{text(chunk.score_type||'Score')}: {text(chunk.score)}</span>}{chunk.keyword_rank!=null&&<span>Keyword rank: {text(chunk.keyword_rank)}</span>}{chunk.semantic_rank!=null&&<span>Semantic rank: {text(chunk.semantic_rank)}</span>}<code>{text(chunk.chunk_id)}</code></div>
  <pre className="evidence-text">{text(chunk.content)}</pre>
 </details>;
}
export function StepPreview({request}:{request:unknown}) {
 const data=object(parseInput(object(request).input));const passages=list(data.passages);const question=object(data.question);
 return <>{inputPreview(request)&&<details className="evidence-brief" open><summary>Brief / input preview</summary><p>{inputPreview(request)}</p></details>}{question.query!=null&&<p><strong>Retrieval question:</strong> {text(question.query)}</p>}{passages.length>0&&<details><summary>Evidence supplied to this step · {passages.length} passages</summary>{passages.map((chunk,i)=><Chunk key={`${text(chunk.chunk_id)}-${i}`} chunk={chunk}/>)}</details>}</>;
}
export default function InspectorEvidence({activity}:{activity:Activity}) {
 return <section className="inspector-evidence" aria-label="Brief and retrieval evidence">
  <h3>Brief → searches → retrieved chunks</h3><p>New campaign reviews use retrieved evidence and confirmed authority. Policies outside retrieval are not checked.</p>
  <details className="evidence-brief"><summary>Original campaign brief</summary><p>{activity.brief||'No brief recorded.'}</p></details>
  {activity.trace.some(s=>s.status==='failed')&&<div role="status" className="evidence-failure">{activity.trace.filter(s=>s.status==='failed').map((s,i)=><p key={i}><strong>{s.name} failed.</strong> {s.error}</p>)}</div>}
  {!activity.retrievals.length&&<p>Retrieval results will appear here once a search finishes.</p>}
  {activity.retrievals.map((retrieval,index)=><div key={text(retrieval.id)||index}>
   <p className="evidence-caption">Retrieval {index+1} · {list(retrieval.groups).length} queries · {list(retrieval.groups).reduce((count,group)=>count+list(group.results).length,0)} retrieved chunks · {text(retrieval.status)}</p>
   {list(retrieval.groups).map((group,i)=>{const query=object(group.query);const results=list(group.results);const selections=activity.selections.filter(s=>s.retrieval_id===retrieval.id&&text(s.brief).includes(`Focus question: ${text(query.query)}`));return <details className="evidence-query" key={text(query.id)||i}>
    <summary><strong>{text(query.id)||`Query ${i+1}`} · {text(query.purpose)}</strong><span>{text(query.query)}</span><small>{results.length} retrieved chunks · {text(group.status)}</small></summary>
    <p><strong>Why this search:</strong> {text(query.reason)}</p>{group.error!=null&&<p role="alert">{typeof group.error==='string'?group.error:JSON.stringify(group.error)}</p>}
    <h4>Retrieved chunks</h4>{results.length?results.map((chunk,j)=><Chunk key={`${text(chunk.chunk_id)}-${j}`} chunk={chunk}/>):<p>No chunks returned.</p>}
    {selections.map(selection=><details className="evidence-policy" key={text(selection.id)}><summary>Added policy context · {list(selection.sections).length} retained sections · {selection.coverage_complete?'Selection complete':'Selection incomplete'}</summary>{Array.isArray(selection.limitations)&&selection.limitations.map((v,j)=><p key={j}>{text(v)}</p>)}{list(selection.sections).map((section,j)=><section key={text(section.id)||j}><p>{text(section.decision)} · {text(section.reason)}</p>{list(section.passages).map((chunk,k)=><Chunk key={`${text(chunk.chunk_id)}-${k}`} chunk={chunk}/>)}</section>)}</details>)}
   </details>;})}
  </div>)}
 </section>;
}
