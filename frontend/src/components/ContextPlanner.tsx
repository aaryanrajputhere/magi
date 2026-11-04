import { useEffect, useState } from "react";
import type { SearchHit } from "../api/client";
import { Hits, useTask } from "./RetrievalTest";

type PlannedQuery = {
  id: string;
  purpose: string;
  query: string;
  reason: string;
};
type Plan = {
  id: string;
  run_id: string;
  brief: string;
  corpus_fingerprint: string;
  usage: unknown;
  plan: {
    analysis: {
      topic: string | null;
      products: string[];
      audience: string | null;
      channels: string[];
      requested_assets: {
        kind: string;
        channel: string | null;
        count: number | null;
      }[];
      unknowns: string[];
    };
    queries: PlannedQuery[];
  };
};
type Retrieved = {
  id: string;
  parent_id: string;
  status: string;
  usage: unknown;
  groups: {
    query: PlannedQuery;
    status: string;
    error: string | null;
    results: SearchHit[];
  }[];
};
type AssessedStatement = {
  id: string;
  subject: string;
  attribute: string;
  value: string;
  kind: string;
  essential: boolean;
  rejected: boolean;
  scope: unknown;
  evidence: {
    document_id: string;
    relative_path: string;
    quotation: string;
    start_line: number;
    end_line: number;
  }[];
};
type EvidencePassage = Pick<SearchHit,
  "chunk_id" | "document_id" | "title" | "relative_path" | "heading_path" |
  "start_line" | "end_line" | "content"
>;
type EvidenceSelection = {
  id: string;
  brief: string;
  authority_version_id: string;
  original_characters: number;
  input_characters: number;
  coverage_complete: boolean;
  limitations: string[];
  coverage_gaps: string[];
  warnings: string[];
  usage: unknown;
  retrieved_passages: EvidencePassage[];
  documents: {
    document_id: string;
    relative_path: string;
    included: boolean;
    reason: string;
  }[];
  sections: {
    id: string;
    document_id: string;
    relative_path: string;
    heading_path: string[];
    decision: "include" | "exclude" | "uncertain";
    reason: string;
    dependencies: string[];
    included: boolean;
    passages: EvidencePassage[];
    evidence: {
      document_id: string;
      quotation: string;
      start_line: number;
      end_line: number;
    }[];
  }[];
};
type Assessment = {
  id: string;
  authority_version_id: string;
  authority_version: number;
  coverage_complete: boolean;
  limitations: string[];
  warnings: string[];
  governing_documents_added: string[];
  usage: unknown;
  statements: AssessedStatement[];
  resolutions: {
    statement_ids: string[];
    reason: string;
    status: string;
    selected_ids: string[];
    rejected_ids: string[];
    basis: string[];
    authority_evidence: {
      document_id: string;
      quotation: string;
      start_line: number;
      end_line: number;
    }[];
  }[];
};
type Compact = {
  id: string;
  context_text: string;
  context_tokens: number;
  max_tokens: number;
  warnings: string[];
  uncovered_query_ids: string[];
  retrieved_occurrences: number;
  unique_chunks: number;
  duplicates_removed: number;
  excluded: {
    chunk_id?: string;
    statement_id?: string;
    reason: string;
    query_ids?: string[];
  }[];
  statements?: AssessedStatement[];
  rejected_statements?: AssessedStatement[];
  assessment_status?: string;
  evidence: { source: SearchHit; query_ids: string[]; purposes: string[] }[];
};

// Mirror Python's default JSON separators and Unicode character count for the
// exact compact assessment payload. The backend always enforces the limit too.
function serializedJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(serializedJson).join(", ")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.entries(value).map(([key, item]) => `${JSON.stringify(key)}: ${serializedJson(item)}`).join(", ")}}`;
  }
  return JSON.stringify(value);
}

function previewSelection(selection: EvidenceSelection, additional: string[]) {
  const included = new Set([
    ...selection.sections.filter((s) => s.decision !== "exclude").map((s) => s.id),
    ...additional,
  ]);
  let changed = true;
  while (changed) {
    const previousSize = included.size;
    for (const section of selection.sections) {
      if (!included.has(section.id)) continue;
      for (const id of section.dependencies) included.add(id);
      for (const ancestor of selection.sections) {
        if (ancestor.document_id === section.document_id &&
          ancestor.heading_path.length < section.heading_path.length &&
          ancestor.heading_path.every((part, i) => part === section.heading_path[i])) {
          included.add(ancestor.id);
        }
      }
    }
    changed = included.size !== previousSize;
  }
  const passages = new Map(selection.retrieved_passages.map((p) => [p.chunk_id, p]));
  for (const section of selection.sections) {
    if (!included.has(section.id)) continue;
    for (const passage of section.passages) {
      if (!passages.has(passage.chunk_id)) passages.set(passage.chunk_id, passage);
    }
  }
  const payload = {
    brief: selection.brief,
    authority_version: selection.authority_version_id,
    passages: [...passages.values()],
  };
  return { included, characters: Array.from(serializedJson(payload)).length };
}

function Usage({ data }: { data: unknown }) {
  return (
    <details className="source-technical">
      <summary>Usage and cost</summary>
      <pre>
        {JSON.stringify(
          data ?? {
            note: "Usage unavailable. Inspect failed query run IDs before retrying.",
          },
          null,
          2,
        )}
      </pre>
    </details>
  );
}

export default function ContextPlanner({
  brandId,
  onView,
  authorityRevision = 0,
  onOpenAuthority,
}: {
  brandId: string;
  authorityRevision?: number;
  onOpenAuthority: () => void;
  onView: (
    hit: Pick<SearchHit, "document_id" | "start_line" | "end_line">,
  ) => void;
}) {
  const base = `/api/brands/${encodeURIComponent(brandId)}/context`;
  const [brief, setBrief] = useState("");
  const [budget, setBudget] = useState(4000);
  const planner = useTask<Plan>();
  const retriever = useTask<Retrieved>();
  const assembler = useTask<Compact>();
  const assessor = useTask<Assessment>();
  const selector = useTask<EvidenceSelection>();
  const [includeSections, setIncludeSections] = useState<string[]>([]);
  const [selectionReviewed, setSelectionReviewed] = useState(false);
  useEffect(() => {
    selector.reset();
    setIncludeSections([]);
    setSelectionReviewed(false);
    assessor.reset();
    assembler.reset();
  }, [authorityRevision, assessor.reset, assembler.reset, selector.reset]);
  const selection = selector.task.state === "ready" ? selector.task.data : null;
  const selectionPreview = selection ? previewSelection(selection, includeSections) : null;
  const selectionSize = selectionPreview?.characters ?? 0;
  const assessed = assessor.task.state === "ready" ? assessor.task.data : null;
  const busy = [
    planner.task,
    retriever.task,
    selector.task,
    assessor.task,
    assembler.task,
  ].some((task) => task.state === "loading");
  const plan = planner.task.state === "ready" ? planner.task.data : null;
  const retrieved =
    retriever.task.state === "ready" ? retriever.task.data : null;
  const compact = assembler.task.state === "ready" ? assembler.task.data : null;
  function clearDownstream() {
    selector.reset();
    setIncludeSections([]);
    setSelectionReviewed(false);
    retriever.reset();
    assessor.reset();
    assembler.reset();
  }
  return (
    <section className="retrieval-workspace" aria-label="Brief planner">
      <h2>Build the campaign context</h2>
      <p>
        Inspect each stage before continuing: plan searches, retrieve all, then
        review assessment evidence, check source authority and conflicts, then assemble reviewed context.
        Planning, retrieval, oversized evidence selection and assessment use OpenAI; assembly selects quoted
        evidence locally.
      </p>
      <p className="source-notice">
        Confirm the brand’s authority map once before assessing evidence.{" "}
        <button className="source-button" onClick={onOpenAuthority}>
          Review Source authority
        </button>
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          clearDownstream();
          void planner.run(`${base}/plan`, { brief }, 90000);
        }}
      >
        <label htmlFor="context-brief">1. Campaign brief</label>
        <textarea
          className="context-brief"
          id="context-brief"
          maxLength={6000}
          required
          disabled={busy}
          value={brief}
          placeholder="Create five LinkedIn posts about FedRAMP"
          onChange={(event) => {
            setBrief(event.target.value);
            planner.reset();
            clearDownstream();
          }}
        />
        <button className="source-button" disabled={busy || !brief.trim()}>
          {planner.task.state === "loading" ? "Planning…" : "Plan searches"}
        </button>
      </form>
      {planner.task.state === "error" && (
        <p className="source-error" role="alert">
          {planner.task.message}
        </p>
      )}
      {plan && (
        <>
          <h3>Interpreted brief</h3>
          <dl className="policy-scope">
            <dt>Topic</dt>
            <dd>{plan.plan.analysis.topic ?? "Unknown"}</dd>
            <dt>Products</dt>
            <dd>{plan.plan.analysis.products.join(", ") || "Unspecified"}</dd>
            <dt>Audience</dt>
            <dd>{plan.plan.analysis.audience ?? "Unspecified"}</dd>
            <dt>Channels</dt>
            <dd>{plan.plan.analysis.channels.join(", ") || "Unspecified"}</dd>
            <dt>Assets</dt>
            <dd>
              {plan.plan.analysis.requested_assets.map((asset, i) => (
                <p key={i}>
                  {asset.count ?? "Unspecified count"} · {asset.kind} ·{" "}
                  {asset.channel ?? "Unspecified channel"}
                </p>
              ))}
            </dd>
          </dl>
          {plan.plan.analysis.unknowns.map((unknown, i) => (
            <p key={i} className="source-notice">
              {unknown}
            </p>
          ))}
          <Usage data={plan.usage} />
          <h3>Proposed searches</h3>
          {plan.plan.queries.map((query) => (
            <article className="source-card" key={query.id}>
              <strong>
                {query.id} · {query.purpose}
              </strong>
              <p>{query.query}</p>
              <p className="source-muted">{query.reason}</p>
            </article>
          ))}
          <button
            className="source-button"
            disabled={busy}
            onClick={() => {
              clearDownstream();
              void retriever.run(
                `${base}/retrieve`,
                { plan_id: plan.id },
                300000,
              );
            }}
          >
            {retriever.task.state === "loading"
              ? "Retrieving…"
              : "2. Retrieve all"}
          </button>
        </>
      )}
      {retriever.task.state === "error" && (
        <p className="source-error" role="alert">
          {retriever.task.message}
        </p>
      )}
      {retrieved && (
        <>
          <h3>Retrieved evidence · {retrieved.status}</h3>
          <Usage data={retrieved.usage} />
          {retrieved.status !== "success" && (
            <p role="alert" className="source-notice">
              Some searches failed. Inspect their errors; assembly will flag
              gaps in the available evidence.
            </p>
          )}
          {retrieved.groups.map((group) => (
            <details className="source-card" key={group.query.id}>
              <summary>
                {group.query.id} · {group.query.purpose} ·{" "}
                {group.results.length} chunks · {group.status}
              </summary>
              <p>{group.query.query}</p>
              {group.error && <p className="source-error">{group.error}</p>}
              <Hits hits={group.results} onView={onView} />
            </details>
          ))}
          <button
            className="source-button"
            disabled={
              busy ||
              !retrieved.groups.some(
                (group) => group.status === "success" && group.results.length,
              )
            }
            onClick={() => {
              assembler.reset();
              assessor.reset();
              selector.reset();
              setIncludeSections([]);
              setSelectionReviewed(false);
              void selector.run(
                `${base}/select`,
                { retrieval_id: retrieved.id },
                300000,
              );
            }}
          >
            {selector.task.state === "loading"
              ? "Selecting evidence…"
              : "3. Select assessment evidence"}
          </button>
          {selector.task.state === "error" && (
            <p role="alert" className="source-error">{selector.task.message}</p>
          )}
          {selection && (
            <>
              <h3>Review assessment evidence</h3>
              <p role="status">
                {selection.original_characters.toLocaleString()} characters before selection ·{" "}
                {selectionSize.toLocaleString()} characters selected
              </p>
              <p>All retrieved evidence is retained. Review excluded sections and restore any that may apply.</p>
              <Usage data={selection.usage} />
              {selection.warnings.concat(selection.limitations, selection.coverage_gaps).map((message, i) => (
                <p className="source-notice" key={i}>{message}</p>
              ))}
              {!selection.coverage_complete && (
                <p role="alert" className="source-error">Selection coverage is incomplete. Assessment is blocked.</p>
              )}
              <details className="source-card">
                <summary>Governing document applicability</summary>
                {selection.documents.map((document) => (
                  <p key={document.document_id}>
                    {document.relative_path} · {document.included ? "considered" : "excluded from governing expansion"} · {document.reason}
                  </p>
                ))}
              </details>
              <details className="source-card">
                <summary>Governing sections and selection reasons</summary>
                {selection.sections.map((section) => (
                  <article className="source-card" key={section.id}>
                    <strong>{section.heading_path.join(" / ") || "Document introduction"}</strong>
                    <p>{section.relative_path} · {selectionPreview?.included.has(section.id) ? "included" : "excluded"} · {section.reason}</p>
                    {section.decision === "uncertain" && <p className="source-notice">Uncertain applicability; retained.</p>}
                    {!section.included && (
                      <label>
                        <input type="checkbox" disabled={busy} checked={includeSections.includes(section.id)}
                          onChange={(event) => {
                            setIncludeSections((previous) => event.target.checked ? [...previous, section.id] : previous.filter((id) => id !== section.id));
                            setSelectionReviewed(false);
                            assessor.reset();
                            assembler.reset();
                          }} /> Restore section and its dependencies
                      </label>
                    )}
                    {section.evidence.map((citation, i) => <blockquote key={i}>{citation.quotation}</blockquote>)}
                    {section.passages.map((passage) => (
                      <details key={passage.chunk_id}>
                        <summary>Source lines {passage.start_line}–{passage.end_line}</summary>
                        <pre>{passage.content}</pre>
                        <button className="source-button" onClick={() => onView(passage)}>View in document</button>
                      </details>
                    ))}
                  </article>
                ))}
              </details>
              <label>
                <input type="checkbox" disabled={busy} checked={selectionReviewed}
                  onChange={(event) => {
                    setSelectionReviewed(event.target.checked);
                    assessor.reset();
                    assembler.reset();
                  }} />
                I reviewed the evidence selection and exclusions.
              </label>
              <button className="source-button"
                disabled={busy || !selectionReviewed || !selection.coverage_complete}
                onClick={() => {
                  assembler.reset();
                  assessor.reset();
                  void assessor.run(`${base}/assess`, {
                    retrieval_id: retrieved.id,
                    selection_id: selection.id,
                    include_section_ids: includeSections,
                  }, 120000);
                }}>
                {assessor.task.state === "loading" ? "Assessing…" : "4. Check authority and conflicts"}
              </button>
            </>
          )}
          {assessor.task.state === "error" && (
            <p role="alert" className="source-error">
              {assessor.task.message}
            </p>
          )}
          {assessed && (
            <>
              <h3>
                Evidence assessment · authority version{" "}
                {assessed.authority_version}
              </h3>
              <Usage data={assessed.usage} />
              <p>
                {assessed.governing_documents_added.length} governing documents
                added beyond retrieval.
              </p>
              {assessed.warnings
                .concat(assessed.limitations)
                .map((warning, i) => (
                  <p key={i} className="source-notice">
                    {warning}
                  </p>
                ))}
              {!assessed.coverage_complete && (
                <p className="source-error" role="alert">
                  The assessment reports incomplete evidence coverage. Reviewed
                  assembly is blocked.
                </p>
              )}
              {assessed.resolutions.map((resolution, i) => (
                <article className="source-card" key={i}>
                  <strong>{resolution.status}</strong>
                  <p>{resolution.reason}</p>
                  <p>
                    Statements: {resolution.statement_ids.join(", ")} ·
                    Selected: {resolution.selected_ids.join(", ") || "none"} ·
                    Rejected: {resolution.rejected_ids.join(", ") || "none"}
                  </p>
                  <p>Basis: {resolution.basis.join("; ")}</p>
                  {(resolution.authority_evidence ?? []).map((citation, j) => (
                    <blockquote key={j}>
                      <p>{citation.quotation}</p>
                      <button
                        className="source-button"
                        onClick={() => onView(citation)}
                      >
                        View authority evidence · lines {citation.start_line}–
                        {citation.end_line}
                      </button>
                    </blockquote>
                  ))}
                </article>
              ))}
              <StatementList
                statements={assessed.statements}
                onView={onView}
                label="All statements and supporting sources"
              />
            </>
          )}
          <div className="retrieval-form">
            <div>
              <label htmlFor="context-budget">Context budget (tokens)</label>
              <select
                id="context-budget"
                disabled={busy}
                value={budget}
                onChange={(event) => {
                  setBudget(Number(event.target.value));
                  assembler.reset();
                }}
              >
                {[1000, 2000, 4000, 8000, 12000].map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </div>
            <button
              className="source-button"
              disabled={
                busy ||
                !assessed?.coverage_complete ||
                !retrieved.groups.some(
                  (group) => group.status === "success" && group.results.length,
                )
              }
              onClick={() => {
                assembler.reset();
                void assembler.run(
                  `${base}/assemble`,
                  {
                    retrieval_id: retrieved.id,
                    max_tokens: budget,
                    assessment_id: assessed?.id,
                  },
                  30000,
                );
              }}
            >
              {assembler.task.state === "loading"
                ? "Assembling…"
                : "5. Assemble reviewed context"}
            </button>
          </div>
        </>
      )}
      {assembler.task.state === "error" && (
        <p className="source-error" role="alert">
          {assembler.task.message}
        </p>
      )}
      {compact && (
        <>
          <h3>Compact shared context</h3>
          <p role="status">
            {compact.context_tokens} / {compact.max_tokens} tokens ·{" "}
            {compact.unique_chunks} unique chunks from{" "}
            {compact.retrieved_occurrences} results ·{" "}
            {compact.duplicates_removed} duplicate occurrences removed ·{" "}
            {compact.statements?.length ?? compact.evidence.length}{" "}
            source-backed statements/passages selected ·{" "}
            {compact.assessment_status ?? "unreviewed"}
          </p>
          {compact.warnings.map((warning, i) => (
            <p className="source-notice" key={i}>
              {warning}
            </p>
          ))}
          {!!compact.uncovered_query_ids.length && (
            <p className="source-notice">
              Uncovered searches: {compact.uncovered_query_ids.join(", ")}
            </p>
          )}
          <article className="source-card">
            <pre>{compact.context_text}</pre>
          </article>
          {compact.statements && (
            <StatementList
              statements={compact.statements}
              onView={onView}
              label="Selected source-backed statements"
            />
          )}
          {compact.rejected_statements && (
            <StatementList
              statements={compact.rejected_statements}
              onView={onView}
              label="Rejected alternatives (audit only)"
            />
          )}
          <details className="source-card">
            <summary>Original retrieved source attribution</summary>
            {compact.evidence.map((entry) => (
              <div key={entry.source.chunk_id}>
                <p>
                  Searches: {entry.query_ids.join(", ")} · Purposes:{" "}
                  {entry.purposes.join(", ")}
                </p>
                <Hits hits={[entry.source]} onView={onView} />
              </div>
            ))}
          </details>
          <details className="source-card">
            <summary>Excluded evidence ({compact.excluded.length})</summary>
            {compact.excluded.map((item) => (
              <p key={item.chunk_id ?? item.statement_id}>
                {item.chunk_id ?? item.statement_id} · {item.reason} ·{" "}
                {item.query_ids?.join(", ")}
              </p>
            ))}
          </details>
          <details className="source-technical">
            <summary>Stage IDs</summary>
            <p>Plan: {plan?.id}</p>
            <p>Retrieval: {retrieved?.id}</p>
            <p>Context: {compact.id}</p>
          </details>
        </>
      )}
    </section>
  );
}

function StatementList({
  statements,
  onView,
  label,
}: {
  statements: AssessedStatement[];
  onView: (
    hit: Pick<SearchHit, "document_id" | "start_line" | "end_line">,
  ) => void;
  label: string;
}) {
  return (
    <details className="source-card">
      <summary>
        {label} ({statements.length})
      </summary>
      {statements.map((statement) => (
        <article className="source-card" key={statement.id}>
          <strong>
            {statement.id} · {statement.kind} ·{" "}
            {statement.rejected
              ? "rejected"
              : statement.essential
                ? "essential / unresolved"
                : "available evidence"}
          </strong>
          <p>
            {statement.subject} · {statement.attribute}: {statement.value}
          </p>
          <p className="source-muted">
            Interpretation, not approved copy. Scope:{" "}
            {JSON.stringify(statement.scope)}
          </p>
          {statement.evidence.map((source, i) => (
            <blockquote key={i}>
              <p>{source.quotation}</p>
              <button className="source-button" onClick={() => onView(source)}>
                View {source.relative_path} · lines {source.start_line}–
                {source.end_line}
              </button>
            </blockquote>
          ))}
        </article>
      ))}
    </details>
  );
}
