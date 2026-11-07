import type { Draft } from './Draft';
import MarkdownCopy from './MarkdownCopy';
import { useEffect, useRef, useState } from 'react';
import EmailPreview from './EmailPreview';
import ImageSkeleton from './ImageSkeleton';

function PostIcon({ name }: { name: 'like' | 'comment' | 'repost' | 'send' | 'globe' }) {
  const paths = {
    like: <path d="M7 10v11H3V10h4Zm0 0 5-7c2 0 3 1 2 4l-1 3h6a2 2 0 0 1 2 2l-2 7a2 2 0 0 1-2 2H7" />,
    comment: <path d="M21 11c0 5-4 8-9 8H8l-5 3 1-6a7 7 0 0 1-2-5c0-5 4-8 10-8s9 3 9 8ZM7 8h10M7 12h7" />,
    repost: <path d="m16 2 4 4-4 4M4 11V6h16M8 22l-4-4 4-4m12-1v5H4" />,
    send: <><path d="m22 2-7 20-4-9-9-4L22 2ZM11 13 22 2" /></>,
    globe: <><circle cx="12" cy="12" r="9" /><ellipse cx="12" cy="12" rx="4" ry="9" /><path d="M3 12h18" /></>,
  };
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function PostText({ text }: { text: string }) {
  return <>{text.split(/(https?:\/\/[^\s\]]+)/g).map((part, index) =>
    /^https?:\/\//.test(part) ? <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a> : part
  )}</>;
}

type ImageRecord = {id: string; title: string; description: string};

export default function AssetPreview({ drafts, brandName, generating, rechecking, error, revisedAssetId, campaignId, version, onUpdate }: {
  onRechecking?: (active: boolean) => void;
  campaignId?: string; version?: number; onUpdate?: (value: never) => void;
  drafts: Draft[]; brandName: string; generating: boolean; rechecking?: boolean; error?: string | null; revisedAssetId?: string;
}) {
  const [library, setLibrary] = useState<ImageRecord[] | null>(null);
  const [imageError, setImageError] = useState('');
  const [savingImage, setSavingImage] = useState(false);
  const [selectedId, setSelectedId] = useState('');
  const previewBody = useRef<HTMLDivElement | null>(null);
  const formatBar = useRef<HTMLElement | null>(null);
  const swipeStart = useRef<{ x: number; y: number } | null>(null);
  const [expandedBody, setExpandedBody] = useState<string | null>(null);
  const revisionRunId = drafts.find(d => d.asset_id === revisedAssetId)?.run_id;
  useEffect(() => {
    if (revisedAssetId) setSelectedId(revisedAssetId);
  }, [revisedAssetId, revisionRunId]);
  const selected = drafts.find(d => d.asset_id === selectedId) ?? drafts.find(d => d.format === 'linkedin') ?? drafts[0];
  const imagePending = generating && !rechecking && !error && !!selected && !selected.image
    && ['linkedin', 'email', 'x', 'blog', 'landing_page'].includes(selected.format);
  const formatDrafts = drafts.filter(draft => draft.format === selected?.format);
  const selectedIndex = selected ? formatDrafts.indexOf(selected) : 0;
  const formats = [...new Set(drafts.map(draft => draft.format))];
  const formatLabel = (format: string) => ({ linkedin: 'LinkedIn', x: 'X / Twitter', email: 'Email', blog: 'Blog', landing_page: 'Landing page' }[format] ?? format.replaceAll('_', ' '));
  useEffect(() => {
    formatBar.current?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
  }, [selected?.asset_id]);
  function goTo(index: number) {
    if (!formatDrafts.length) return;
    const next = (index + formatDrafts.length) % formatDrafts.length;
    setSelectedId(formatDrafts[next].asset_id);
    setExpandedBody(null);
  }
  async function chooseImage(imageId: string | null) {
    if (!selected || !campaignId) return;
    setSavingImage(true); setImageError('');
    try {
      const response = await fetch(`/api/campaigns/${campaignId}/drafts/${selected.asset_id}/image`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({version, image_id: imageId})});
      const value = await response.json();
      if (!response.ok) throw new Error(value.detail || 'Unable to save image');
      onUpdate?.(value as never); setLibrary(null);
    } catch (error) { setImageError(String(error)); } finally { setSavingImage(false); }
  }
  async function openImages() {
    setImageError('');
    try {
      const response = await fetch(`/api/campaigns/${campaignId}/images`);
      if (!response.ok) throw new Error('Unable to load brand images');
      setLibrary(await response.json());
    } catch (error) { setImageError(String(error)); }
  }
  useEffect(()=>{previewBody.current?.scrollTo({top:0});},[selected?.asset_id]);
  const expanded = expandedBody === selected?.body;
  const collapsed = !!selected && selected.body.length > 280 && !expanded;
  const visibleBody = collapsed ? selected.body.slice(0, 280).replace(/\s+\S*$/, '') : selected?.body ?? '';
  const initials = brandName.split(/\s+/).map(word => word[0]).slice(0, 2).join('').toUpperCase();
  return <aside className="asset-preview" aria-label="Asset preview">
    <header className="preview-heading"><h2>Asset preview</h2><span>Draft</span></header>
    {formats.length > 0 && <nav className="preview-format-bar" aria-label="Asset formats" ref={formatBar}>
      {formats.map(format => <button key={format} type="button" aria-current={selected?.format === format ? 'true' : undefined}
        onClick={() => {
          if (selected?.format === format) return;
          setSelectedId(drafts.find(draft => draft.format === format)!.asset_id);
          setExpandedBody(null);
        }}>
        {formatLabel(format)}<span>{drafts.filter(draft => draft.format === format).length}</span>
      </button>)}
    </nav>}
    {selected && <div className="preview-slide-heading"><div className="preview-slide-copy" aria-live="polite"><span>{selectedIndex + 1} of {formatDrafts.length} · {formatLabel(selected.format)}</span><h3>{selected.title}</h3></div><nav className="preview-heading-arrows" aria-label="Browse assets"><button disabled={formatDrafts.length < 2} onClick={() => goTo(selectedIndex - 1)} aria-label="Previous asset">‹</button><button disabled={formatDrafts.length < 2} onClick={() => goTo(selectedIndex + 1)} aria-label="Next asset">›</button></nav></div>}
    {generating && <p role="status">{rechecking ? "Rechecking saved drafts…" : "Writing campaign drafts…"}</p>}
    {error && <p role="alert">{error}</p>}
    <div ref={previewBody} className="preview-carousel" role="region" aria-roledescription="carousel" aria-label="Campaign assets" tabIndex={formatDrafts.length > 1 ? 0 : undefined}
      onKeyDown={event => {
        if (event.target !== event.currentTarget) return;
        if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
          event.preventDefault();
          goTo(selectedIndex + (event.key === 'ArrowRight' ? 1 : -1));
        }
      }}
      onTouchStart={event => {
        const touch = event.touches[0];
        swipeStart.current = event.touches.length === 1 ? { x: touch.clientX, y: touch.clientY } : null;
      }}
      onTouchCancel={() => { swipeStart.current = null; }}
      onTouchEnd={event => {
        const start = swipeStart.current;
        swipeStart.current = null;
        if (!start || !event.changedTouches.length) return;
        const dx = event.changedTouches[0].clientX - start.x;
        const dy = event.changedTouches[0].clientY - start.y;
        if (Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy) * 1.5) goTo(selectedIndex + (dx < 0 ? 1 : -1));
      }}>
    {!selected ? <p className="preview-empty">Your generated assets will appear here after you approve the plan.</p> : selected.format === 'linkedin' ? <article className="linkedin-card" aria-label="LinkedIn post preview">
      <header className="linkedin-post-header">
        <span className="linkedin-avatar" aria-hidden="true">{initials}</span>
        <div className="linkedin-author"><strong>{brandName}</strong><small>Company page</small><small className="linkedin-visibility">Now · <PostIcon name="globe" /><span className="sr-only">Public</span></small></div>
        <span className="linkedin-follow" aria-hidden="true">+ Follow</span>
        <span className="linkedin-menu" aria-hidden="true">···</span>
      </header>
      <div className="linkedin-body"><PostText text={visibleBody} />{collapsed && <button className="linkedin-more" onClick={() => setExpandedBody(selected.body)} aria-expanded={false}>…more</button>}{expanded && selected.body.length > 280 && <button className="linkedin-more linkedin-less" onClick={() => setExpandedBody(null)} aria-expanded={true}>Show less</button>}</div>
      {selected.image && <img className="linkedin-post-image" src={`/api/campaigns/${campaignId}/images/${encodeURIComponent(selected.image.image_id)}`} alt={selected.image.alt_text} />}
      {imagePending && <ImageSkeleton />}
      <footer className="linkedin-actions" aria-hidden="true">{(['like', 'comment', 'repost', 'send'] as const).map(action => <span key={action}><PostIcon name={action} /><span>{action[0].toUpperCase() + action.slice(1)}</span></span>)}</footer>
    </article> : selected.format === 'email' ? <EmailPreview brandName={brandName} subject={selected.subject} preheader={selected.preheader} senderName={selected.sender_name} body={selected.body} imageUrl={selected.image && campaignId ? `/api/campaigns/${campaignId}/images/${encodeURIComponent(selected.image.image_id)}` : undefined} imageAlt={selected.image?.alt_text} imagePending={imagePending} /> : selected.format === 'x' ? <article className="x-card" aria-label="X post preview">
      <header className="x-post-header"><span className="x-avatar" aria-hidden="true">{initials}</span><strong>{brandName}</strong><span className="x-verified" aria-label="Verified">✓</span><span className="x-menu" aria-hidden="true">···</span></header>
      <div className="x-post-content">
        <div className="x-post-body"><PostText text={visibleBody}/></div>
        {selected.body.length > 280 && <button className="x-show-more" onClick={() => setExpandedBody(expanded ? null : selected.body)} aria-expanded={expanded}>{expanded ? 'Show less' : 'Show more'}</button>}
        {selected.image && campaignId ? <img className="x-post-image" src={`/api/campaigns/${campaignId}/images/${encodeURIComponent(selected.image.image_id)}`} alt={selected.image.alt_text}/> : imagePending ? <ImageSkeleton /> : <div className="x-image-placeholder" aria-hidden="true" />}
      </div>
        <div className="x-engagement" aria-hidden="true"><span>Reposts</span><span>Quotes</span><span>Likes</span></div>
        <footer className="x-actions" aria-hidden="true">{[
          <path d="M21 11a9 9 0 0 1-9 9H9l-5 3 1-6a9 9 0 1 1 16-6Z"/>,
          <path d="m3 7 4-4 4 4M7 3v13a3 3 0 0 0 3 3h3m8-2-4 4-4-4m4 4V8a3 3 0 0 0-3-3h-2"/>,
          <path d="M12 21 3 12a5.5 5.5 0 0 1 9-6 5.5 5.5 0 0 1 9 6Z"/>,
          <path d="M12 16V2m-5 5 5-5 5 5M4 14v7h16v-7"/>
        ].map((path, index) => <span key={index}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">{path}</svg></span>)}</footer>
    </article> : selected.format === 'blog' ? <article className="blog-preview" aria-label="Blog article preview">
      <header className="blog-preview-header">
        <div className="blog-preview-meta"><span className="blog-preview-category">{brandName} / Blog</span><span className="blog-preview-status">Draft</span></div>
        <h2>{selected.title}</h2>
        <div className="blog-preview-author">
          {selected.byline && <><span className="blog-author-avatar" aria-hidden="true">{selected.byline.replace(/^by\s+/i, '').trim().charAt(0).toUpperCase()}</span><span className="blog-author-name">By {selected.byline.replace(/^(by\s+)+/i, '').trim()}</span></>}
          <span className="blog-reading-time">{Math.max(1, Math.ceil(selected.body.split(/\s+/).filter(Boolean).length / 200))} min read</span>
        </div>
      </header>
      {selected.image && campaignId && <figure className="blog-preview-hero"><img src={`/api/campaigns/${campaignId}/images/${encodeURIComponent(selected.image.image_id)}`} alt={selected.image.alt_text}/></figure>}
      {imagePending && <div className="blog-preview-hero"><ImageSkeleton /></div>}
      <div className="blog-preview-copy"><MarkdownCopy body={selected.body}/></div>
      <footer className="blog-preview-footer">{selected.body.split(/\s+/).filter(Boolean).length + selected.title.split(/\s+/).filter(Boolean).length} words<span>Article preview</span></footer>
    </article> : selected.format === 'landing_page' ? <article className="landing-preview" aria-label="Landing page preview">
      <header className="landing-preview-nav"><span className="landing-preview-brand"><span className="landing-brand-mark" aria-hidden="true">{initials}</span>{brandName}</span><span className="landing-preview-draft">Draft</span></header>
      <section className="landing-preview-hero">
        <span className="landing-preview-eyebrow">{brandName}</span>
        <h2>{selected.title}</h2>
        {selected.byline && <p className="landing-preview-subtitle">{selected.byline}</p>}
        {selected.image && campaignId && <figure className="landing-preview-image"><img src={`/api/campaigns/${campaignId}/images/${encodeURIComponent(selected.image.image_id)}`} alt={selected.image.alt_text}/></figure>}
        {imagePending && <div className="landing-preview-image"><ImageSkeleton /></div>}
      </section>
      <div className="landing-preview-copy"><MarkdownCopy body={selected.body}/></div>
      <footer className="landing-preview-footer"><span>{brandName}</span><span>Landing page preview</span></footer>
    </article> : <article className={`format-preview format-${selected.format}`} aria-label={`${selected.format.replaceAll('_', ' ')} preview`}>
      <small>{selected.format.replaceAll('_', ' ')} · Draft</small>
      <h2>{selected.title}</h2>{selected.byline && <p>By {selected.byline}</p>}
      {selected.image && campaignId && <img className="format-hero" src={`/api/campaigns/${campaignId}/images/${encodeURIComponent(selected.image.image_id)}`} alt={selected.image.alt_text}/>}
      {selected.format !== 'x' && <><MarkdownCopy body={selected.body}/><small>{selected.body.split(/\s+/).filter(Boolean).length + selected.title.split(/\s+/).filter(Boolean).length} words</small></>}
    </article>}
    </div>
    {selected && ['linkedin', 'email', 'x', 'blog', 'landing_page'].includes(selected.format) && campaignId && <div className="image-controls"><button onClick={openImages} disabled={savingImage || generating}>{selected.image ? 'Change image' : 'Add image'}</button>{selected.image && <button onClick={() => chooseImage(null)} disabled={savingImage || generating}>Remove image</button>}{imageError && <p role="alert">{imageError}</p>}{library && <section aria-label="Brand image picker"><button onClick={() => setLibrary(null)}>Close picker</button><div className="image-library">{library.map(image => <button key={image.id} disabled={savingImage} onClick={() => chooseImage(image.id)}><img loading="lazy" src={`/api/campaigns/${campaignId}/images/${encodeURIComponent(image.id)}`} alt={image.description || image.title}/><span>{image.title}</span></button>)}</div>{!library.length && <p>No eligible brand images available.</p>}</section>}</div>}

  </aside>;
}
