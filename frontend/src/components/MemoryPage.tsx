import { useEffect, useState } from 'react';
import MemoryPanel from './MemoryPanel';

type Brand = { id: string; name: string };

export default function MemoryPage() {
  const [brands, setBrands] = useState<Brand[]>([]);
  const [brandId, setBrandId] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const response = await fetch('/api/brands', { signal: controller.signal });
        if (!response.ok) throw new Error('Unable to load brands.');
        const data: Brand[] = await response.json();
        if (controller.signal.aborted) return;
        setBrands(data);
        setBrandId(data[0]?.id || '');
      } catch (cause) {
        if (!controller.signal.aborted) setError(String(cause));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, []);

  return <section className="memory-page" aria-label="Brand memories">
    <h1>Memories</h1>
    <p>Keep the details that make every campaign feel like your brand.</p>
    {loading && <p role="status">Loading brands…</p>}
    {error && <p role="alert">{error}</p>}
    {!loading && !error && !brands.length && <p>Import a brand to start saving memories.</p>}
    {brands.length > 0 && <>
      <MemoryPanel key={brandId} brandId={brandId} />
    </>}
  </section>;
}
