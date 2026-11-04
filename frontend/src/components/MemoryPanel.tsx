import { useEffect, useState } from 'react';
type Memory = { id: string; text: string; revision: number };
export default function MemoryPanel({ brandId }: { brandId: string }) {
  const [items, setItems] = useState<Memory[]>([]);
  const [text, setText] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [editText, setEditText] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const base = `/api/brands/${brandId}/memories`;

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch(base, { signal: controller.signal });
        if (!response.ok) throw new Error('Unable to load memories.');
        const data: Memory[] = await response.json();
        if (!controller.signal.aborted) setItems(data);
      } catch (cause) {
        if (!controller.signal.aborted) setError(String(cause));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [base]);

  async function save(url: string, method: string, body?: unknown) {
    setBusy(true); setError(''); setNotice('');
    try {
      const response = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: body ? JSON.stringify(body) : undefined });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Unable to update memory.');
      setItems(data); setEditing(null);
      setNotice(method === 'DELETE' ? 'Memory forgotten.' : 'Memory saved.');
      return true;
    } catch (cause) { setError(String(cause)); return false; }
    finally { setBusy(false); }
  }

  return <div className="memory-content">
    <div className="memory-section-heading"><h2>Saved preferences <span>{items.length}</span></h2></div>
    {error && <p className="memory-error" role="alert">{error}</p>}
    {notice && <p className="memory-notice" role="status">{notice}</p>}
    {loading && <p role="status">Loading memories…</p>}
    {!loading && !items.length && <div className="memory-empty"><span aria-hidden="true">✧</span><h3>A little context goes a long way.</h3><p>Save a preference below, or ask Magi to remember it in chat.</p></div>}
    <div className="memory-list">{items.map((item, index) => <article className="memory-card" key={item.id}>
      <div className="memory-card-top"><span className="memory-card-label"><span aria-hidden="true">✧</span> Preference {String(index + 1).padStart(2, '0')}</span></div>
      {editing === item.id ? <><textarea autoFocus aria-label="Edit remembered preference" maxLength={500} value={editText} onChange={event => setEditText(event.target.value)} /><div className="memory-card-actions"><button className="memory-primary" disabled={busy || !editText.trim()} onClick={() => void save(`${base}/${item.id}`, 'PUT', { text: editText, revision: item.revision })}>Save changes</button><button disabled={busy} onClick={() => setEditing(null)}>Cancel</button></div></> : <><p className="memory-copy">{item.text}</p><div className="memory-card-actions"><button disabled={busy} onClick={() => { setEditing(item.id); setEditText(item.text); }}>Edit preference</button><button className="memory-forget" disabled={busy} onClick={() => void save(`${base}/${item.id}?revision=${item.revision}`, 'DELETE')}>Forget</button></div></>}
    </article>)}</div>
    <form className="memory-composer" onSubmit={async event => { event.preventDefault(); if (await save(base, 'POST', { text })) setText(''); }}>
      <label htmlFor="new-memory">Add a memory</label>
      <textarea id="new-memory" placeholder="e.g. Keep LinkedIn posts concise and avoid hashtags." maxLength={500} required value={text} onChange={event => setText(event.target.value)} />
      <div className="memory-composer-footer"><span>{text.length}/500</span><button className="memory-primary" disabled={busy || loading || !text.trim() || items.length >= 50}>{busy ? 'Saving…' : '＋ Save memory'}</button></div>
    </form>
    
  </div>;
}
