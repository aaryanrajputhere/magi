import { Fragment } from 'react';
function Inline({ text }: {text: string}) {
  return <>{text.split(/(\[[^\]]+\]\(https?:\/\/[^\s)]+\)|\*\*[^*]+\*\*| `[^`]+`)/g).map((part, i) => {
    const link = part.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    if (part.startsWith('**')) return <strong key={i}>{part.slice(2,-2)}</strong>;
    return <Fragment key={i}>{part}</Fragment>;
  })}</>;
}
export default function MarkdownCopy({body}: {body:string}) {
  return <div className="markdown-copy">{body.replace(/\r\n/g,'\n').split(/(```[^\n]*\n[\s\S]*?```)/g).map((section, s) =>
    section.startsWith('```') ? <pre key={s}><code>{section.replace(/^```[^\n]*\n/,'').replace(/```$/,'')}</code></pre> :
    <Fragment key={s}>{section.split(/\n\s*\n/).filter(b=>b.trim()).map((block,i)=>{
      const heading=block.match(/^#{1,6}\s+([^\n]+)$/);
      if(heading) return <h3 key={i}><Inline text={heading[1]}/></h3>;
      const lines=block.split('\n');
      if(lines.every(l=>/^\s*[-*]\s+/.test(l))) return <ul key={i}>{lines.map((l,j)=><li key={j}><Inline text={l.replace(/^\s*[-*]\s+/,'')}/></li>)}</ul>;
      if(lines.every(l=>/^\s*\d+[.)]\s+/.test(l))) return <ol key={i}>{lines.map((l,j)=><li key={j}><Inline text={l.replace(/^\s*\d+[.)]\s+/,'')}/></li>)}</ol>;
      return <p key={i}><Inline text={block}/></p>;
    })}</Fragment>
  )}</div>;
}
