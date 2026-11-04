import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import type {
  BrandSummary,
  SearchHit,
  Candidate,
  DerivedState,
  DocumentDetail,
  DocumentSummary,
} from "../api/client";
import AuthorityManager from "./AuthorityManager";
import ContextPlanner from "./ContextPlanner";
import RetrievalTest from "./RetrievalTest";
import PolicyPanel from "./PolicyPanel";
import { useApi } from "../hooks/useApi";

function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return (
    <div className="source-error" role="alert">
      <p>{message}</p>
      <button className="source-button" onClick={retry}>
        Try again
      </button>
    </div>
  );
}

function StateBadge({ state, label }: { state: DerivedState; label: string }) {
  return (
    <span className={`source-badge ${state}`}>
      {label}: {state === "missing" ? "not prepared" : state}
    </span>
  );
}

export default function SourceBrowser() {
  const { resource, retry } = useApi<BrandSummary[]>("/api/brands");
  const [selectedBrand, setSelectedBrand] = useState("");
  if (resource.state === "loading")
    return (
      <p role="status" className="source-empty">
        Loading brands…
      </p>
    );
  if (resource.state === "error")
    return <LoadError message={resource.message} retry={retry} />;
  const brands = resource.data;
  if (!brands.length)
    return (
      <div className="source-empty">
        <h2>No brand sources yet.</h2>
        <p>
          Import a brand to inspect its documents, chunks and source evidence
          here.
        </p>
      </div>
    );
  const brandId = brands.some((brand) => brand.id === selectedBrand)
    ? selectedBrand
    : brands[0].id;
  return (
    <>
      <div className="source-toolbar">
        <div>
          <label htmlFor="source-brand">BRAND</label>
          <select
            id="source-brand"
            value={brandId}
            onChange={(event) => setSelectedBrand(event.target.value)}
          >
            {brands.map((brand) => (
              <option key={brand.id} value={brand.id}>
                {brand.name} · {brand.document_count} documents
              </option>
            ))}
          </select>
        </div>
      </div>
      <BrandSources key={brandId} brandId={brandId} />
    </>
  );
}

type SourceTarget = Range & { documentId: string };
function BrandSources({ brandId }: { brandId: string }) {
  const [mode, setMode] = useState("browse");
  const [authorityRevision, setAuthorityRevision] = useState(0);
  const [target, setTarget] = useState<SourceTarget | null>(null);
  function view(
    hit: Pick<SearchHit, "document_id" | "start_line" | "end_line">,
  ) {
    setTarget((previous) => ({
      documentId: hit.document_id,
      start: hit.start_line,
      end: hit.end_line,
      tick: (previous?.tick ?? 0) + 1,
    }));
    setMode("browse");
  }
  return (
    <>
      <nav className="source-tabs" aria-label="Sources tools">
        <button
          aria-pressed={mode === "browse"}
          onClick={() => setMode("browse")}
        >
          Browse sources
        </button>
        <button
          aria-pressed={mode === "retrieval"}
          onClick={() => setMode("retrieval")}
        >
          Retrieval test
        </button>
        <button
          aria-pressed={mode === "context"}
          onClick={() => setMode("context")}
        >
          Brief planner
        </button>
        <button
          aria-pressed={mode === "authority"}
          onClick={() => setMode("authority")}
        >
          Source authority
        </button>
      </nav>
      <div className="sources-pane" hidden={mode !== "browse"}>
        <DocumentBrowser
          brandId={brandId}
          target={target}
          clearTarget={() => setTarget(null)}
        />
      </div>
      <div className="sources-pane" hidden={mode !== "retrieval"}>
        <RetrievalTest brandId={brandId} onView={view} />
      </div>
      <div className="sources-pane" hidden={mode !== "context"}>
        <ContextPlanner
          brandId={brandId}
          onView={view}
          authorityRevision={authorityRevision}
          onOpenAuthority={() => setMode("authority")}
        />
      </div>
      <div className="sources-pane" hidden={mode !== "authority"}>
        <AuthorityManager
          brandId={brandId}
          onView={view}
          onConfirm={() => setAuthorityRevision((previous) => previous + 1)}
        />
      </div>
    </>
  );
}
function DocumentBrowser({
  brandId,
  target,
  clearTarget,
}: {
  brandId: string;
  target: SourceTarget | null;
  clearTarget: () => void;
}) {
  const { resource, retry } = useApi<DocumentSummary[]>(
    `/api/brands/${encodeURIComponent(brandId)}/documents`,
  );
  const [query, setQuery] = useState("");
  const [selectedDocument, setSelectedDocument] = useState("");
  if (resource.state === "loading")
    return (
      <p role="status" className="source-empty">
        Loading documents…
      </p>
    );
  if (resource.state === "error")
    return <LoadError message={resource.message} retry={retry} />;
  const documents = resource.data;
  if (!documents.length)
    return (
      <div className="source-empty">
        <h2>No documents imported.</h2>
        <p>This brand has no stored documents to inspect.</p>
      </div>
    );
  const requestedDocument = target?.documentId ?? selectedDocument;
  const documentId = documents.some((doc) => doc.id === requestedDocument)
    ? requestedDocument
    : documents[0].id;
  const needle = query.trim().toLocaleLowerCase();
  const visible = documents.filter((doc) =>
    `${doc.title ?? ""} ${doc.relative_path}`
      .toLocaleLowerCase()
      .includes(needle),
  );
  return (
    <div className="source-layout">
      <aside className="source-list" aria-label="Brand documents">
        <div className="source-list-heading">
          <label htmlFor="source-search">Find a document</label>
          <input
            id="source-search"
            type="search"
            placeholder="Filter by title or path"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <span role="status">
            {visible.length} of {documents.length} documents
          </span>
        </div>
        <div className="source-document-list">
          {visible.map((doc) => (
            <button
              key={doc.id}
              className={`source-document ${doc.id === documentId ? "selected" : ""}`}
              aria-current={doc.id === documentId ? "true" : undefined}
              onClick={() => {
                clearTarget();
                setSelectedDocument(doc.id);
              }}
            >
              <strong>{doc.title || doc.relative_path}</strong>
            </button>
          ))}
          {!visible.length && (
            <p className="source-empty">
              No matching documents. Try another title or path.
            </p>
          )}
        </div>
      </aside>
      <DocumentInspector
        key={`${documentId}-${target?.tick ?? 0}`}
        initialRange={target}
        brandId={brandId}
        documentId={documentId}
      />
    </div>
  );
}

function DocumentInspector({
  brandId,
  documentId,
  initialRange,
}: {
  brandId: string;
  documentId: string;
  initialRange: Range | null;
}) {
  const { resource, retry } = useApi<DocumentDetail>(
    `/api/brands/${encodeURIComponent(brandId)}/documents/${encodeURIComponent(documentId)}`,
  );
  if (resource.state === "loading")
    return (
      <section className="source-inspector">
        <p role="status" className="source-empty">
          Loading document…
        </p>
      </section>
    );
  if (resource.state === "error")
    return (
      <section className="source-inspector">
        <LoadError message={resource.message} retry={retry} />
      </section>
    );
  return (
    <SourceInspector document={resource.data} initialRange={initialRange} />
  );
}

const tabs = ["document", "chunks", "metadata", "policies"] as const;
type Tab = (typeof tabs)[number];
type Range = { start: number; end: number; tick: number };

function SourceInspector({
  document,
  initialRange,
}: {
  document: DocumentDetail;
  initialRange: Range | null;
}) {
  const [tab, setTab] = useState<Tab>("document");
  const [highlight, setHighlight] = useState<Range | null>(initialRange);
  const selectedLine = useRef<HTMLDivElement>(null);
  const tabButtons = useRef<Partial<Record<Tab, HTMLButtonElement | null>>>({});
  useEffect(() => {
    if (tab === "document" && highlight) {
      selectedLine.current?.scrollIntoView({
        block: "center",
        behavior: "auto",
      });
      selectedLine.current?.focus({ preventScroll: true });
    }
  }, [tab, highlight]);
  function jump(start: number, end: number) {
    setHighlight((previous) => ({
      start,
      end,
      tick: (previous?.tick ?? 0) + 1,
    }));
    setTab("document");
  }
  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, current: Tab) {
    const index = tabs.indexOf(current);
    const next =
      event.key === "ArrowRight"
        ? tabs[(index + 1) % tabs.length]
        : event.key === "ArrowLeft"
          ? tabs[(index + tabs.length - 1) % tabs.length]
          : event.key === "Home"
            ? tabs[0]
            : event.key === "End"
              ? tabs[tabs.length - 1]
              : null;
    if (next) {
      event.preventDefault();
      setTab(next);
      tabButtons.current[next]?.focus();
    }
  }
  const candidates = document.annotation?.candidates ?? [];
  return (
    <section className="source-inspector" aria-label="Document inspector">
      <div className="source-inspector-heading">
        <h2>{document.title || "Untitled document"}</h2>
        <details className="source-technical">
          <summary>Details</summary>
          <p className="source-path">{document.relative_path}</p>
          <div className="source-statuses">
            <StateBadge state={document.chunk_state} label="Chunks" />
            <StateBadge state={document.metadata_state} label="Metadata" />
            <StateBadge state={document.policy_state} label="Policies" />
          </div>
          <dl>
            <dt>Document ID</dt>
            <dd>{document.id}</dd>
            <dt>Source hash</dt>
            <dd>{document.content_hash}</dd>
            {document.annotation && (
              <>
                <dt>Metadata extractor</dt>
                <dd>{document.annotation.extractor_version}</dd>
              </>
            )}
          </dl>
        </details>
      </div>
      <div className="source-tabs" role="tablist" aria-label="Source views">
        {tabs.map((name) => (
          <button
            key={name}
            ref={(node) => {
              tabButtons.current[name] = node;
            }}
            id={`source-tab-${name}`}
            role="tab"
            aria-selected={tab === name}
            aria-controls={`source-panel-${name}`}
            tabIndex={tab === name ? 0 : -1}
            onClick={() => setTab(name)}
            onKeyDown={(event) => navigateTabs(event, name)}
          >
            {name === "chunks"
              ? `Chunks (${document.chunks.length})`
              : name === "metadata"
                ? `Metadata (${candidates.length})`
                : name === "policies"
                  ? `Policies (${document.policy_extraction?.candidates.length ?? 0})`
                  : "Document"}
          </button>
        ))}
      </div>
      <div
        className="source-tab-content"
        role="tabpanel"
        id={`source-panel-${tab}`}
        aria-labelledby={`source-tab-${tab}`}
        tabIndex={0}
      >
        {tab === "document" && (
          <>
            {highlight && (
              <div className="source-highlight-note" role="status">
                Showing source lines {highlight.start}–{highlight.end}
                <button onClick={() => setHighlight(null)}>
                  Clear highlight
                </button>
              </div>
            )}
            {!document.raw_text ? (
              <p className="source-empty">This document is empty.</p>
            ) : (
              <div className="source-lines" aria-label="Original Markdown">
                {document.raw_text.split(/\r\n|\r|\n/).map((line, index) => {
                  const number = index + 1;
                  const active =
                    highlight &&
                    number >= highlight.start &&
                    number <= highlight.end;
                  return (
                    <div
                      key={number}
                      ref={
                        highlight?.start === number ? selectedLine : undefined
                      }
                      className={`source-line ${active ? "highlighted" : ""}`}
                      tabIndex={highlight?.start === number ? -1 : undefined}
                    >
                      <span className="source-line-number" aria-hidden="true">
                        {number}
                      </span>
                      <code>{line || "\u200b"}</code>
                    </div>
                  );
                })}
              </div>
            )}
          </>
        )}
        {tab === "chunks" && (
          <>
            {document.chunk_state === "stale" && (
              <p className="source-notice">
                Some chunks were built from an older source or chunker version.
                Source jumps are disabled for stale chunks.
              </p>
            )}
            {!document.chunks.length && (
              <p className="source-empty">
                No chunks stored for this document. It may be empty or awaiting
                chunking.
              </p>
            )}
            {document.chunks.map((chunk) => (
              <article className="source-card" key={chunk.id}>
                <div className="source-card-heading">
                  <strong>Chunk {chunk.position + 1}</strong>
                  <span>
                    Lines {chunk.start_line}–{chunk.end_line} ·{" "}
                    {Array.from(chunk.content).length.toLocaleString()}{" "}
                    characters
                  </span>
                </div>
                <p className="source-heading-path">
                  {chunk.heading_path.join(" / ") || "Document introduction"}
                </p>
                <div className="source-statuses">
                  {chunk.oversized && (
                    <span className="source-badge caution">
                      Oversized · preserved intact
                    </span>
                  )}
                  {chunk.state === "stale" && (
                    <span className="source-badge stale">Stale</span>
                  )}
                </div>
                <pre>{chunk.content}</pre>
                <button
                  className="source-button"
                  disabled={chunk.state === "stale"}
                  onClick={() => jump(chunk.start_line, chunk.end_line)}
                >
                  View in document
                </button>
                <details className="source-technical">
                  <summary>Chunk details</summary>
                  <dl>
                    <dt>ID</dt>
                    <dd>{chunk.id}</dd>
                    <dt>Source hash</dt>
                    <dd>{chunk.source_hash}</dd>
                    <dt>Chunker</dt>
                    <dd>{chunk.chunker_version}</dd>
                    <dt>Target</dt>
                    <dd>{chunk.target_chars} characters</dd>
                  </dl>
                </details>
              </article>
            ))}
          </>
        )}
        {tab === "policies" && <PolicyPanel document={document} jump={jump} />}
        {tab === "metadata" && (
          <>
            <p className="source-notice">
              Document-level evidence, shared by its chunks. These are
              candidates, not approval or authority decisions.
            </p>
            {document.metadata_state === "stale" && (
              <p className="source-notice caution">
                These annotations are stale. Their source locations may not
                match this document; jumps are disabled.
              </p>
            )}
            {!document.annotation ? (
              <p className="source-empty">
                Metadata not extracted for this document.
              </p>
            ) : !candidates.length ? (
              <p className="source-empty">
                No explicit candidates found. This does not mean the document
                has no restrictions.
              </p>
            ) : (
              (
                [
                  "owner",
                  "date",
                  "authority",
                  "supersession",
                ] as Candidate["category"][]
              ).map((category) => {
                const grouped = candidates.filter(
                  (candidate) => candidate.category === category,
                );
                return (
                  <section key={category} className="source-metadata-group">
                    <h3>{category}</h3>
                    {!grouped.length && (
                      <p className="source-muted">
                        No explicit candidate found.
                      </p>
                    )}
                    {grouped.map((candidate, index) => (
                      <article
                        key={`${candidate.start_offset}-${index}`}
                        className="source-card"
                      >
                        <div className="source-card-heading">
                          <span className="source-badge">Candidate</span>
                          <span>
                            Lines {candidate.start_line}–{candidate.end_line}
                          </span>
                        </div>
                        <pre>{candidate.statement}</pre>
                        <p className="source-muted">
                          Extraction rule: {candidate.rule}
                        </p>
                        <button
                          className="source-button"
                          disabled={document.metadata_state === "stale"}
                          onClick={() =>
                            jump(candidate.start_line, candidate.end_line)
                          }
                        >
                          View in document
                        </button>
                      </article>
                    ))}
                  </section>
                );
              })
            )}
          </>
        )}
      </div>
    </section>
  );
}
