import MarkdownCopy from './MarkdownCopy';

export default function EmailPreview({ brandName, subject, body, preheader, senderName, imageUrl, imageAlt }: {
  preheader?: string | null; senderName?: string | null; brandName: string; subject: string | null; body: string; imageUrl?: string; imageAlt?: string;
}) {
  return <article className="email-preview" aria-label="Email preview">
    <header className="email-envelope">
      
      {preheader && <span className="sr-only">{preheader}</span>}
      <h3>{subject?.trim() || '(No subject)'}</h3>
      <div className="email-sender-row">
        <span className="email-avatar" aria-hidden="true">{brandName.slice(0, 1).toUpperCase()}</span>
        <div><strong>{senderName || brandName}</strong><p>to Campaign audience ⌄</p></div>
      </div>
    </header>
    <div className="email-message">
      {imageUrl && <img className="email-header-image" src={imageUrl} alt={imageAlt || ''} />}
      <div className="email-copy"><MarkdownCopy body={body}/></div>
    </div>
    <footer className="email-reply-actions" aria-hidden="true"><span>↶ &nbsp; Reply</span><span>↪ &nbsp; Forward</span></footer>
  </article>;
}
