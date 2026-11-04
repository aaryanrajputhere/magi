export type Health = {
  status: "ok" | "unavailable";
  service: string;
  version: string;
  database: "connected" | "unavailable";
};

export async function getHealth(signal?: AbortSignal): Promise<Health> {
  const response = await fetch("/api/health", { signal });
  if (!response.ok) throw new Error(`Backend returned ${response.status}`);
  const data: Health = await response.json();
  if (data.status !== "ok" || data.database !== "connected") {
    throw new Error("The backend is not ready");
  }
  return data;
}

export type DerivedState = "missing" | "current" | "stale";
export type BrandSummary = { id: string; name: string; document_count: number };
export type DocumentSummary = {
  id: string;
  title: string | null;
  relative_path: string;
  chunk_count: number;
  chunk_state: DerivedState;
  metadata_state: DerivedState;
};
export type Chunk = {
  id: string;
  position: number;
  heading_path: string[];
  content: string;
  start_line: number;
  end_line: number;
  start_offset: number;
  end_offset: number;
  source_hash: string;
  chunker_version: string;
  target_chars: number;
  oversized: boolean;
  state: DerivedState;
};
export type Candidate = {
  category: "owner" | "date" | "authority" | "supersession";
  statement: string;
  start_offset: number;
  end_offset: number;
  start_line: number;
  end_line: number;
  rule: string;
  verification_status: "candidate";
};
export type DocumentDetail = DocumentSummary & {
  brand_id: string;
  raw_text: string;
  content_hash: string;
  chunks: Chunk[];
  policy_state: DerivedState;
  policy_extraction: PolicyExtraction | null;
  annotation: null | {
    id: string;
    source_hash: string;
    extractor_version: string;
    candidates: Candidate[];
  };
};

export async function getJson<T>(
  path: string,
  signal: AbortSignal,
): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "This source is no longer available."
        : `Request failed (${response.status}).`,
    );
  return response.json() as Promise<T>;
}

export type PolicyCandidate = {
  id: string;
  rule_type:
    | "prohibition"
    | "permission"
    | "required_wording"
    | "qualifier"
    | "approval_requirement";
  description: string;
  exact_wording: string | null;
  scope: {
    subjects: string[] | null;
    channels: string[] | null;
    audiences: string[] | null;
    regions: string[] | null;
    applicable_dates: string[] | null;
  };
  conditions: string[] | null;
  exceptions: string[] | null;
  status: "candidate";
  evidence: {
    quotation: string;
    match_method?: "exact" | "boundary_ellipsis";
    model_quotation?: string | null;
    start_line: number;
    end_line: number;
    start_offset: number;
    end_offset: number;
  }[];
};
export type PolicyExtraction = {
  id: string;
  model: string;
  run_id: string;
  source_hash: string;
  extractor_version: string;
  prompt_version: string;
  schema_hash: string;
  output_limit: number;
  candidates: PolicyCandidate[];
};

export type IndexStatus = {
  state: string;
  eligible_chunks: number;
  excluded_chunks: number;
  fingerprint: string;
};
export type RetrievalMethod = "keyword" | "semantic" | "hybrid";
export type QueryEmbeddingUsage = {
  run_ids: string[];
  input_tokens: number | null;
  estimated_cost_usd: string | null;
  cost_status: string;
  duration_ms: number;
};
export type VectorStatus = Omit<IndexStatus, "fingerprint"> & {
  current_vectors: number;
  missing_vectors: number;
  stale_vectors: number;
  invalid_vectors: number;
  corpus_fingerprint: string;
  vector_fingerprint: string;
};
export type HybridStatus = VectorStatus & {
  keyword_state: string;
  keyword_fingerprint: string;
};
export type SearchHit = {
  rank: number;
  score: number;
  score_type: "bm25" | "cosine" | "rrf";
  keyword_rank: number | null;
  keyword_score: number | null;
  semantic_rank: number | null;
  semantic_score: number | null;
  chunk_id: string;
  document_id: string;
  title: string | null;
  relative_path: string;
  heading_path: string[];
  start_line: number;
  end_line: number;
  content: string;
};
export type SearchResponse = {
  query: string;
  method: RetrievalMethod;
  query_embedding: QueryEmbeddingUsage | null;
  terms: string[];
  fingerprint: string;
  latency_ms: number;
  retrieved_characters: number;
  results: SearchHit[];
};
export type SuiteSummary = {
  id: string;
  name: string;
  description: string;
  case_count: number;
};
export type EvaluationReport = {
  method: RetrievalMethod;
  corpus_fingerprint: string;
  vector_fingerprint: string | null;
  query_embedding: QueryEmbeddingUsage | null;
  categories: Record<
    string,
    {
      cases: number;
      coverage_at_5: number | null;
      coverage_at_10: number | null;
    }
  >;
  suite_id: string;
  suite_name: string;
  suite_hash: string;
  fingerprint: string;
  summary: {
    cases: number;
    positive_cases: number;
    evidence_groups: number;
    coverage_at_5: number | null;
    coverage_at_10: number | null;
    all_evidence_at_5: number;
    all_evidence_at_10: number;
    mean_reciprocal_rank_at_10: number | null;
    negative_cases: number;
    negative_zero_results: number;
    mean_query_latency_ms: number;
    duration_ms: number;
  };
  cases: (Omit<SearchResponse, "fingerprint"> & {
    id: string;
    category: string;
    coverage_at_10: number | null;
    first_relevant_rank: number | null;
    missing_at_5: string[];
    missing_at_10: string[];
    expected_groups: {
      id: string;
      alternatives: { document_path: string; quotation: string }[];
    }[];
  })[];
};
export async function requestJson<T>(
  path: string,
  signal: AbortSignal,
  body?: unknown,
): Promise<T> {
  const response = await fetch(path, {
    signal,
    ...(body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(
      typeof data?.detail?.message === "string"
        ? data.detail.message
        : Array.isArray(data?.detail)
          ? data.detail
              .slice(0, 5)
              .map(
                (error: { loc?: string[]; msg?: string }) =>
                  `${error.loc?.join(".") ?? "Input"}: ${error.msg ?? "Invalid value"}`,
              )
              .join("; ")
          : `Request failed (${response.status}).`,
    );
  }
  return response.json() as Promise<T>;
}
