import { useCallback, useEffect, useRef, useState } from "react";
import { useApi } from "../hooks/useApi";
import {
  requestJson,
  type SearchHit,
  type SearchResponse,
  type IndexStatus,
  type EvaluationReport,
  type SuiteSummary,
  type VectorStatus,
  type HybridStatus,
  type RetrievalMethod,
  type QueryEmbeddingUsage,
} from "../api/client";

type Task<T> =
  | { state: "idle" }
  | { state: "loading" }
  | { state: "error"; message: string }
  | { state: "ready"; data: T };

export function useTask<T>() {
  const [task, setTask] = useState<Task<T>>({ state: "idle" });
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function run(path: string, body?: unknown, timeoutMs = 30000) {
    controller.current?.abort();
    const current = new AbortController();
    controller.current = current;
    setTask({ state: "loading" });
    let timedOut = false;
    const timer = window.setTimeout(() => {
      timedOut = true;
      current.abort();
    }, timeoutMs);
    try {
      const data = await requestJson<T>(path, current.signal, body);
      if (!current.signal.aborted) {
        setTask({ state: "ready", data });
        return data;
      }
    } catch (error) {
      if (
        controller.current === current &&
        (timedOut || !current.signal.aborted)
      )
        setTask({
          state: "error",
          message: timedOut
            ? "The request timed out. Backend work may still be running; check before retrying."
            : error instanceof Error
              ? error.message
              : "Request failed",
        });
    } finally {
      window.clearTimeout(timer);
    }
  }
  const reset = useCallback(() => {
    controller.current?.abort();
    setTask({ state: "idle" });
  }, []);
  return { task, run, reset };
}

export function Hits({
  hits,
  onView,
}: {
  hits: SearchHit[];
  onView: (hit: SearchHit) => void;
}) {
  return (
    <div>
      {!hits.length && <p className="source-empty">No matching chunks.</p>}
      {hits.map((hit) => (
        <article className="source-card retrieval-hit" key={hit.chunk_id}>
          <div className="source-card-heading">
            <strong>
              #{hit.rank} · {hit.title || hit.relative_path}
            </strong>
            <button className="source-button" onClick={() => onView(hit)}>
              View in document
            </button>
          </div>
          <p className="source-path">
            {hit.relative_path} · Lines {hit.start_line}–{hit.end_line}
          </p>
          <p className="source-heading-path">
            {hit.heading_path.join(" / ") || "Document introduction"}
          </p>
          <pre>{hit.content}</pre>
          <details className="source-technical">
            <summary>Ranking details</summary>
            <p>
              {hit.score_type === "rrf"
                ? "RRF"
                : hit.score_type === "cosine"
                  ? "Cosine similarity"
                  : "BM25"}
              : {hit.score.toPrecision(6)} ·{" "}
              {hit.score_type !== "bm25"
                ? "Higher scores rank first. This is not a confidence percentage."
                : "Lower scores rank first."}
            </p>
            {hit.score_type === "rrf" && (
              <>
                <p>
                  Keyword rank: {hit.keyword_rank ?? "absent"} · BM25:{" "}
                  {hit.keyword_score?.toPrecision(6) ?? "absent"}
                </p>
                <p>
                  Semantic rank: {hit.semantic_rank ?? "absent"} · Cosine:{" "}
                  {hit.semantic_score?.toPrecision(6) ?? "absent"}
                </p>
              </>
            )}
            <p className="source-path">Chunk: {hit.chunk_id}</p>
          </details>
        </article>
      ))}
    </div>
  );
}

const percent = (value: number | null) =>
  value === null ? "—" : `${(value * 100).toFixed(1)}%`;
function download(report: EvaluationReport) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = `${report.suite_id}-${report.method}-results.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function RetrievalTest({
  brandId,
  onView,
}: {
  brandId: string;
  onView: (hit: SearchHit) => void;
}) {
  const base = `/api/brands/${encodeURIComponent(brandId)}`;
  const [method, setMethod] = useState<RetrievalMethod>("keyword");
  const [reports, setReports] = useState<
    Partial<Record<RetrievalMethod, EvaluationReport>>
  >({});
  const status = useApi<IndexStatus | VectorStatus | HybridStatus>(
    `${base}/retrieval/${method === "hybrid" ? "hybrid" : method === "semantic" ? "embeddings" : "index"}`,
  );
  const suites = useApi<SuiteSummary[]>("/api/retrieval/suites");
  const index = useTask<IndexStatus>();
  const search = useTask<SearchResponse>();
  const evaluation = useTask<EvaluationReport>();
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(10);
  const [suiteId, setSuiteId] = useState("");
  const indexBusy = index.task.state === "loading";
  const busy =
    indexBusy ||
    search.task.state === "loading" ||
    evaluation.task.state === "loading";
  const ready =
    status.resource.state === "ready" && status.resource.data.state === "ready";
  const report = reports[method];
  async function runEvaluation(id: string) {
    const data = await evaluation.run(
      `${base}/retrieval/evaluate`,
      { suite_id: id, method },
      300000,
    );
    if (data) setReports((previous) => ({ ...previous, [data.method]: data }));
  }
  const selectedSuite =
    suites.resource.state === "ready"
      ? (suites.resource.data.find((s) => s.id === suiteId) ??
        suites.resource.data[0])
      : undefined;
  async function prepare() {
    await index.run(`${base}/retrieval/index`, {});
    status.retry();
  }
  return (
    <section className="retrieval-workspace" aria-label="Retrieval test">
      <div className="retrieval-intro">
        <div>
          <h2>Test your brand context</h2>
          <p>
            Search stored chunks and inspect the evidence before connecting
            campaign generation.
          </p>
        </div>
        {method !== "semantic" && (
          <button className="source-button" disabled={busy} onClick={prepare}>
            {indexBusy ? "Preparing…" : "Prepare / rebuild index"}
          </button>
        )}
      </div>
      <div className="retrieval-form">
        <div>
          <label htmlFor="retrieval-method">Search method</label>
          <select
            id="retrieval-method"
            value={method}
            disabled={busy}
            onChange={(e) => {
              search.reset();
              evaluation.reset();
              setMethod(e.target.value as RetrievalMethod);
            }}
          >
            <option value="keyword">Keyword</option>
            <option value="semantic">Semantic</option>
            <option value="hybrid">Hybrid</option>
          </select>
        </div>
        <button
          className="source-button"
          disabled={busy}
          onClick={status.retry}
        >
          Refresh readiness
        </button>
      </div>
      <p className="source-notice">
        {method !== "keyword"
          ? "Semantic and hybrid search embeds each submitted question with OpenAI, then compares stored vectors locally. No query cache is enabled."
          : "Keyword search makes no model calls."}{" "}
        All methods search the imported snapshot, including historical and
        conflicting documents. Policy candidates do not filter results.
      </p>
      {status.resource.state === "loading" && (
        <p role="status">Checking readiness…</p>
      )}
      {status.resource.state === "error" && (
        <p role="alert">
          {status.resource.message}{" "}
          <button className="source-button" onClick={status.retry}>
            Retry
          </button>
        </p>
      )}
      {status.resource.state === "ready" && (
        <p role="status">
          {method === "hybrid"
            ? "Hybrid"
            : method === "semantic"
              ? "Embeddings"
              : "Index"}
          : <strong>{status.resource.data.state}</strong> ·{" "}
          {status.resource.data.eligible_chunks} eligible chunks ·{" "}
          {status.resource.data.excluded_chunks} stale chunks excluded.
          {status.resource.data.state !== "ready" &&
            (method !== "keyword"
              ? " Prepare embeddings and, for Hybrid, the keyword index before testing."
              : " Prepare the index before testing.")}
          {"keyword_state" in status.resource.data && (
            <> · Keyword index: {status.resource.data.keyword_state}</>
          )}
          {"current_vectors" in status.resource.data && (
            <>
              {" "}
              · {status.resource.data.current_vectors} current vectors ·{" "}
              {status.resource.data.missing_vectors} missing ·{" "}
              {status.resource.data.stale_vectors} stale ·{" "}
              {status.resource.data.invalid_vectors} invalid
            </>
          )}
          {!status.resource.data.eligible_chunks &&
            " Import and chunk documents first."}
        </p>
      )}
      {method !== "semantic" && index.task.state === "error" && (
        <p role="alert" className="source-error">
          {index.task.message}
        </p>
      )}
      <form
        className="retrieval-form"
        onSubmit={(event) => {
          event.preventDefault();
          void search.run(
            `${base}/search`,
            { query, limit, method },
            method !== "keyword" ? 90000 : 30000,
          );
        }}
      >
        <div>
          <label htmlFor="retrieval-query">Search the brand</label>
          <input
            id="retrieval-query"
            type="search"
            maxLength={512}
            required
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="What can we say about FedRAMP?"
          />
        </div>
        <div>
          <label htmlFor="retrieval-limit">Results</label>
          <select
            id="retrieval-limit"
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
          >
            {[5, 10, 20].map((n) => (
              <option key={n}>{n}</option>
            ))}
          </select>
        </div>
        <button className="source-button" disabled={busy || !ready}>
          {search.task.state === "loading" ? "Searching…" : "Search"}
        </button>
      </form>
      {search.task.state === "loading" && (
        <p role="status">Retrieving chunks…</p>
      )}
      {search.task.state === "error" && (
        <p role="alert" className="source-error">
          {search.task.message}
        </p>
      )}
      {search.task.state === "ready" && (
        <>
          <p role="status">
            “{search.task.data.query}” · {search.task.data.results.length}{" "}
            results · {search.task.data.latency_ms.toFixed(1)} ms ·{" "}
            {search.task.data.retrieved_characters.toLocaleString()} characters
          </p>
          <p className="source-muted">Method: {search.task.data.method}</p>
          <Usage usage={search.task.data.query_embedding} />
          <Hits hits={search.task.data.results} onView={onView} />
        </>
      )}
      <div className="retrieval-evaluation">
        <h2>Repeatable evaluation</h2>
        <p>
          Measures whether expected evidence appears in the first 5 or 10
          chunks. This does not measure answer accuracy or resolve source
          authority.
        </p>
        {suites.resource.state === "loading" && (
          <p role="status">Loading evaluation suites…</p>
        )}
        {suites.resource.state === "error" && (
          <p role="alert">
            {suites.resource.message}{" "}
            <button className="source-button" onClick={suites.retry}>
              Retry
            </button>
          </p>
        )}
        {suites.resource.state === "ready" && !suites.resource.data.length && (
          <p>No evaluation suites installed.</p>
        )}
        {suites.resource.state === "ready" && selectedSuite && (
          <div className="retrieval-form">
            <div>
              <label htmlFor="retrieval-suite">Evaluation suite</label>
              <select
                id="retrieval-suite"
                value={selectedSuite.id}
                onChange={(e) => setSuiteId(e.target.value)}
              >
                {suites.resource.data.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · {s.case_count} cases
                  </option>
                ))}
              </select>
              <p className="source-muted">{selectedSuite.description}</p>
            </div>
            <button
              className="source-button"
              disabled={busy || !ready}
              onClick={() => void runEvaluation(selectedSuite.id)}
            >
              {evaluation.task.state === "loading"
                ? "Evaluating…"
                : "Run evaluation"}
            </button>
          </div>
        )}
        {evaluation.task.state === "loading" && (
          <p role="status">Checking evidence references and running cases…</p>
        )}
        {evaluation.task.state === "error" && (
          <p role="alert" className="source-error">
            {evaluation.task.message}
          </p>
        )}
        {Object.keys(reports).length >= 2 && <Comparison reports={reports} />}
        {report && <Report report={report} onView={onView} />}
      </div>
    </section>
  );
}

function Report({
  report,
  onView,
}: {
  report: EvaluationReport;
  onView: (hit: SearchHit) => void;
}) {
  const s = report.summary;
  return (
    <>
      <div className="retrieval-intro">
        <h3>
          {report.suite_name} · {report.method}
        </h3>
        <button className="source-button" onClick={() => download(report)}>
          Download JSON
        </button>
      </div>
      <Usage usage={report.query_embedding} />
      {report.method !== "keyword" && (
        <p className="source-notice">
          Semantic and hybrid search can return nearest neighbours even for
          absent products. No relevance threshold is applied; a returned chunk
          does not establish that an answer exists.
        </p>
      )}
      <div className="retrieval-metrics">
        {[
          ["Evidence coverage @5", percent(s.coverage_at_5)],
          ["Evidence coverage @10", percent(s.coverage_at_10)],
          ["All evidence @5", `${s.all_evidence_at_5}/${s.positive_cases}`],
          ["All evidence @10", `${s.all_evidence_at_10}/${s.positive_cases}`],
          ["MRR @10", s.mean_reciprocal_rank_at_10?.toFixed(3) ?? "—"],
          [
            "Negative cases with no results",
            `${s.negative_zero_results}/${s.negative_cases}`,
          ],
        ].map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <p className="source-muted">
        {s.evidence_groups} evidence groups ·{" "}
        {report.method !== "keyword"
          ? "Average local ranking"
          : "Average query"}{" "}
        {s.mean_query_latency_ms.toFixed(1)} ms · Total{" "}
        {s.duration_ms.toFixed(0)} ms. Negative cases use distinctive absent
        names; they do not establish general no-answer detection.
      </p>
      <details className="source-technical">
        <summary>Snapshot details</summary>
        <p className="source-path">Corpus: {report.corpus_fingerprint}</p>
        <p className="source-path">
          Vector snapshot: {report.vector_fingerprint ?? "not applicable"}
        </p>
        <p className="source-path">Suite: {report.suite_hash}</p>
      </details>
      <div className="retrieval-table">
        <table>
          <caption>Evidence coverage by question category</caption>
          <thead>
            <tr>
              <th>Category</th>
              <th>Cases</th>
              <th>Coverage @5</th>
              <th>Coverage @10</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(report.categories).map(([name, category]) => (
              <tr key={name}>
                <th>{name}</th>
                <td>{category.cases}</td>
                <td>{percent(category.coverage_at_5)}</td>
                <td>{percent(category.coverage_at_10)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {report.cases.map((c) => (
        <details key={c.id} className="source-card">
          <summary>
            {c.id} · {c.query} ·{" "}
            {c.category === "negative"
              ? `${c.results.length} results`
              : `${percent(c.coverage_at_10)} evidence @10`}
          </summary>
          <p>
            First relevant rank: {c.first_relevant_rank ?? "none"} ·{" "}
            {c.latency_ms.toFixed(1)} ms
          </p>
          <p>
            Missing @5: {c.missing_at_5.join(", ") || "none"} · Missing @10:{" "}
            {c.missing_at_10.join(", ") || "none"}
          </p>
          {c.expected_groups.map((g) => (
            <div key={g.id} className="retrieval-expected">
              <strong>Expected: {g.id}</strong>
              {g.alternatives.map((a, i) => (
                <blockquote key={i}>
                  <p className="source-path">{a.document_path}</p>
                  {a.quotation}
                </blockquote>
              ))}
            </div>
          ))}
          <Hits hits={c.results} onView={onView} />
        </details>
      ))}
    </>
  );
}

function Usage({ usage }: { usage: QueryEmbeddingUsage | null }) {
  if (!usage) return null;
  return (
    <details className="source-technical">
      <summary>
        Query embedding · {usage.input_tokens ?? "unknown"} tokens ·{" "}
        {usage.estimated_cost_usd === null
          ? "cost unknown"
          : `$${usage.estimated_cost_usd} estimated`}
      </summary>
      <p>Embedding calls: {usage.duration_ms.toFixed(1)} ms</p>
      {usage.run_ids.map((id) => (
        <p className="source-path" key={id}>
          Run: {id}
        </p>
      ))}
    </details>
  );
}

function Comparison({
  reports,
}: {
  reports: Partial<Record<RetrievalMethod, EvaluationReport>>;
}) {
  const available = (["keyword", "semantic", "hybrid"] as const).flatMap(
    (method) => (reports[method] ? [reports[method]!] : []),
  );
  const first = available[0];
  const vectorReports = available.filter(
    (report) => report.method !== "keyword",
  );
  if (
    available.some(
      (report) =>
        !report.corpus_fingerprint ||
        report.suite_hash !== first.suite_hash ||
        report.corpus_fingerprint !== first.corpus_fingerprint,
    ) ||
    vectorReports.some(
      (report) =>
        report.vector_fingerprint !== vectorReports[0].vector_fingerprint,
    )
  )
    return (
      <p className="source-notice">
        These reports use different suites, corpus or vector snapshots. Rerun
        the methods against matching snapshots to compare them.
      </p>
    );
  const metrics: [string, (report: EvaluationReport) => string][] = [
    ["Coverage @5", (r) => percent(r.summary.coverage_at_5)],
    ["Coverage @10", (r) => percent(r.summary.coverage_at_10)],
    [
      "Complete evidence @10",
      (r) => `${r.summary.all_evidence_at_10}/${r.summary.positive_cases}`,
    ],
    ["MRR @10", (r) => r.summary.mean_reciprocal_rank_at_10?.toFixed(3) ?? "—"],
    ["Total duration", (r) => `${r.summary.duration_ms.toFixed(0)} ms`],
    [
      "Query embedding cost",
      (r) =>
        r.query_embedding
          ? r.query_embedding.estimated_cost_usd === null
            ? "unknown"
            : `$${r.query_embedding.estimated_cost_usd}`
          : "$0",
    ],
  ];
  return (
    <div className="retrieval-table">
      <table>
        <caption>Same-suite, same-corpus comparison</caption>
        <thead>
          <tr>
            <th>Metric</th>
            {available.map((report) => (
              <th key={report.method}>{report.method}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {metrics.map(([label, value]) => (
            <tr key={label}>
              <th>{label}</th>
              {available.map((report) => (
                <td key={report.method}>{value(report)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
