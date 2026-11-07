export default function ImageSkeleton() {
  return <div className="image-skeleton" role="status" aria-label="Selecting a campaign image">
    <svg viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <rect x="3" y="5" width="26" height="22" rx="3" />
      <circle cx="11" cy="12" r="2.5" />
      <path d="m4 24 8-8 6 6 4-4 7 7" />
    </svg>
    <span>Selecting an image…</span>
  </div>;
}
