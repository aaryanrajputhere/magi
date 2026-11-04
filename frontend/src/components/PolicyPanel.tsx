import type { DocumentDetail } from "../api/client";

function scopeText(value: string[] | null) {
  return value === null
    ? "Not stated"
    : value.length
      ? value.join("; ")
      : "No entries returned";
}

export default function PolicyPanel({
  document,
  jump,
}: {
  document: DocumentDetail;
  jump: (start: number, end: number) => void;
}) {
  const batch = document.policy_extraction;
  return (
    <>
      <p className="source-notice">
        These are AI-extracted policy candidates. Matching quotations establish
        textual grounding only—not semantic accuracy, authority or approval.
        Unstated scope is unknown, not universal.
      </p>
      {document.policy_state === "stale" && (
        <p className="source-notice caution">
          This batch comes from an older source or extractor configuration.
          Source jumps are disabled.
        </p>
      )}
      {!batch ? (
        <p className="source-empty">
          Policies have not been extracted for this document. Extraction is run
          separately through the CLI.
        </p>
      ) : (
        <>
          <details className="source-technical">
            <summary>Extraction details</summary>
            <dl>
              <dt>Model</dt>
              <dd>{batch.model}</dd>
              <dt>Run ID</dt>
              <dd>{batch.run_id}</dd>
              <dt>Batch ID</dt>
              <dd>{batch.id}</dd>
              <dt>Extractor</dt>
              <dd>{batch.extractor_version}</dd>
              <dt>Prompt</dt>
              <dd>{batch.prompt_version}</dd>
              <dt>Source hash</dt>
              <dd>{batch.source_hash}</dd>
              <dt>Schema hash</dt>
              <dd>{batch.schema_hash}</dd>
            </dl>
          </details>
          {!batch.candidates.length && (
            <p className="source-empty">
              Extraction completed with no policy candidates. This does not
              establish that the source has no restrictions.
            </p>
          )}
          {batch.candidates.map((candidate) => (
            <article className="source-card policy-card" key={candidate.id}>
              <div className="source-card-heading">
                <span className="source-badge">Candidate</span>
                <span>{candidate.rule_type.replaceAll("_", " ")}</span>
              </div>
              <h3>{candidate.description}</h3>
              {candidate.exact_wording !== null && (
                <>
                  <h4>Exact wording</h4>
                  <pre>{candidate.exact_wording}</pre>
                </>
              )}
              <dl className="policy-scope">
                {Object.entries(candidate.scope).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>{scopeText(value)}</dd>
                  </div>
                ))}
                <div>
                  <dt>Conditions</dt>
                  <dd>{scopeText(candidate.conditions)}</dd>
                </div>
                <div>
                  <dt>Exceptions</dt>
                  <dd>{scopeText(candidate.exceptions)}</dd>
                </div>
              </dl>
              <h4>Supporting evidence</h4>
              {candidate.evidence.map((evidence, index) => (
                <div
                  className="policy-evidence"
                  key={`${evidence.start_offset}-${index}`}
                >
                  <p className="source-muted">
                    Lines {evidence.start_line}–{evidence.end_line} · Quotation
                    matched
                  </p>
                  <pre>{evidence.quotation}</pre>
                  {evidence.match_method === "boundary_ellipsis" && (
                    <div className="source-notice">
                      <p>
                        Boundary omission marks were removed; the displayed
                        quotation is exact source text. No internal text was
                        rewritten.
                      </p>
                      <details>
                        <summary>Original model quotation</summary>
                        <pre>{evidence.model_quotation}</pre>
                      </details>
                    </div>
                  )}
                  <button
                    className="source-button"
                    disabled={document.policy_state !== "current"}
                    onClick={() => jump(evidence.start_line, evidence.end_line)}
                  >
                    View in document
                  </button>
                </div>
              ))}
              <details className="source-technical">
                <summary>Candidate ID</summary>
                <p>{candidate.id}</p>
              </details>
            </article>
          ))}
        </>
      )}
    </>
  );
}
