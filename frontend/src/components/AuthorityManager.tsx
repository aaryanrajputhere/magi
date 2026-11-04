import { useState } from "react";
import type { SearchHit } from "../api/client";
import { useApi } from "../hooks/useApi";
import { useTask } from "./RetrievalTest";

type Citation = {
  document_id: string;
  quotation: string;
  start_line: number;
  end_line: number;
};
type AuthorityMap = {
  groups: {
    id: string;
    label: string;
    priority: number;
    document_ids: string[];
    topics: string[];
    channels: string[] | null;
    governing: boolean;
    evidence: Citation[];
  }[];
  supersessions: {
    newer_id: string;
    older_id: string;
    topics: string[];
    channels: string[] | null;
    evidence: Citation[];
  }[];
  notes: string[];
};
type Version = {
  id: string;
  version: number;
  status: string;
  current: boolean;
  map: AuthorityMap;
  run_id: string | null;
  unmapped_document_ids: string[];
};
type State = {
  active: Version | null;
  versions: Version[];
  documents: { id: string; title: string | null; relative_path: string }[];
};

export default function AuthorityManager({
  brandId,
  onConfirm,
  onView,
}: {
  brandId: string;
  onConfirm: () => void;
  onView: (
    hit: Pick<SearchHit, "document_id" | "start_line" | "end_line">,
  ) => void;
}) {
  const base = `/api/brands/${encodeURIComponent(brandId)}/authority`;
  const status = useApi<State>(base);
  const action = useTask<Version>();
  const [selectedId, setSelectedId] = useState("");
  const [editor, setEditor] = useState<string | null>(null);
  const [localError, setLocalError] = useState("");
  const data = status.resource.state === "ready" ? status.resource.data : null;
  const selected =
    action.task.state === "ready" && action.task.data.id === selectedId
      ? action.task.data
      : (data?.versions.find((version) => version.id === selectedId) ??
        data?.versions[0]);
  const text =
    editor ??
    (selected
      ? JSON.stringify(selected.map, null, 2)
      : '{\n  "groups": [],\n  "supersessions": [],\n  "notes": []\n}');
  const busy = action.task.state === "loading";
  const label = (id: string) =>
    data?.documents.find((doc) => doc.id === id)?.relative_path ?? id;
  let dirty = true;
  try {
    dirty =
      !selected ||
      JSON.stringify(JSON.parse(text)) !== JSON.stringify(selected.map);
  } catch {
    /* Editing invalid JSON disables confirmation. */
  }
  async function perform(path: string, body: unknown, confirmed = false) {
    setLocalError("");
    const result = await action.run(path, body, 120000);
    if (result) {
      setSelectedId(result.id);
      setEditor(JSON.stringify(result.map, null, 2));
      status.retry();
      if (confirmed) onConfirm();
    }
  }
  return (
    <section className="retrieval-workspace" aria-label="Source authority">
      <h2>Source authority</h2>
      <p>
        Review the source hierarchy once per brand. Proposals remain drafts
        until you confirm them. Confirmation establishes precedence, not the
        truth or approval of every extracted statement.
      </p>
      <p className="source-notice">
        Lower priority numbers take precedence only for overlapping topics and
        scope. Null channels are unknown; ["*"] explicitly covers all channels.
        A lower-level rule remains usable when nothing above contradicts it.
        Filenames and dates alone do not establish authority.
      </p>
      {status.resource.state === "loading" && (
        <p role="status">Loading authority maps…</p>
      )}
      {status.resource.state === "error" && (
        <p role="alert">{status.resource.message}</p>
      )}
      <p role="status">
        Active map:{" "}
        {data?.active
          ? `version ${data.active.version} · ${data.active.current ? "current" : "stale—review again"}`
          : "none confirmed"}
      </p>
      <div className="retrieval-form">
        <button
          className="source-button"
          disabled={busy}
          onClick={() => void perform(`${base}/propose`, {})}
        >
          {busy ? "Working…" : "Propose authority map"}
        </button>
        <button
          className="source-button"
          disabled={busy}
          onClick={status.retry}
        >
          Refresh
        </button>
      </div>
      <p className="source-muted">
        Proposing a map uses a tracked OpenAI call. Editing, saving and
        confirming a draft make no model calls. All sources are version-pinned;
        document changes require a fresh review.
      </p>
      {data && (
        <>
          <label htmlFor="authority-version">Inspect version</label>
          <select
            id="authority-version"
            disabled={busy}
            value={selected?.id ?? ""}
            onChange={(event) => {
              setSelectedId(event.target.value);
              const version = data.versions.find(
                (v) => v.id === event.target.value,
              );
              setEditor(version ? JSON.stringify(version.map, null, 2) : "");
              action.reset();
            }}
          >
            {data.versions.map((version) => (
              <option value={version.id} key={version.id}>
                Version {version.version} · {version.status} ·{" "}
                {version.current ? "current" : "stale"}
              </option>
            ))}
          </select>
          {selected && (
            <>
              {selected.map.groups.map((group) => (
                <article className="source-card" key={group.id}>
                  <strong>
                    {group.label} · priority {group.priority}
                  </strong>
                  <p>
                    Topics: {group.topics.join(", ")} · Channels:{" "}
                    {group.channels?.join(", ") ?? "unknown"} ·{" "}
                    {group.governing
                      ? "Governing evidence checked beyond retrieval"
                      : "Retrieved evidence"}
                  </p>
                  <ul>
                    {group.document_ids.map((id) => (
                      <li key={id}>{label(id)}</li>
                    ))}
                  </ul>
                  {group.evidence.map((citation, i) => (
                    <blockquote key={i}>
                      <p>{citation.quotation}</p>
                      <button
                        className="source-button"
                        onClick={() => onView(citation)}
                      >
                        View {label(citation.document_id)} · lines{" "}
                        {citation.start_line}–{citation.end_line}
                      </button>
                    </blockquote>
                  ))}
                </article>
              ))}
              {selected.map.supersessions.map((link, i) => (
                <article className="source-card" key={i}>
                  <strong>
                    {label(link.newer_id)} supersedes {label(link.older_id)}
                  </strong>
                  <p>
                    Topics: {link.topics.join(", ")} · Channels:{" "}
                    {link.channels?.join(", ") ?? "unknown"}
                  </p>
                  {link.evidence.map((citation, j) => (
                    <blockquote key={j}>
                      {citation.quotation}
                      <button
                        className="source-button"
                        onClick={() => onView(citation)}
                      >
                        View source
                      </button>
                    </blockquote>
                  ))}
                </article>
              ))}
              <details className="source-card">
                <summary>
                  Unmapped sources ({selected.unmapped_document_ids.length})
                </summary>
                {selected.unmapped_document_ids.map((id) => (
                  <p key={id}>{label(id)}</p>
                ))}
              </details>
              {selected.map.notes.map((note, i) => (
                <p className="source-notice" key={i}>
                  {note}
                </p>
              ))}
              {selected.run_id && (
                <p className="source-path">
                  Proposal run: {selected.run_id} · inspect usage with
                  app.llm.cli show
                </p>
              )}
            </>
          )}
          <details className="source-card" open={!selected}>
            <summary>Edit or create a source-backed map</summary>
            <p>
              Use document IDs from the list below. Each group and supersession
              needs exact quoted source lines. Saving creates a new draft.
              Governing sources are added in full during assessment; keep their
              scope focused.
            </p>
            <textarea
              className="context-brief authority-editor"
              aria-label="Authority map JSON"
              value={text}
              disabled={busy}
              onChange={(event) => setEditor(event.target.value)}
            />
            <button
              className="source-button"
              disabled={busy}
              onClick={() => {
                try {
                  void perform(`${base}/drafts`, JSON.parse(text));
                } catch {
                  setLocalError("Enter valid JSON before saving.");
                }
              }}
            >
              Save new draft
            </button>
            <details>
              <summary>Available document IDs</summary>
              {data.documents.map((doc) => (
                <p className="source-path" key={doc.id}>
                  {doc.id} · {doc.relative_path}
                </p>
              ))}
            </details>
          </details>
          <button
            className="source-button"
            disabled={
              busy ||
              !selected ||
              selected.status !== "draft" ||
              !selected.current ||
              dirty
            }
            onClick={() =>
              selected &&
              void perform(`${base}/confirm`, { version_id: selected.id }, true)
            }
          >
            Confirm reviewed authority map
          </button>
        </>
      )}
      {dirty && selected && (
        <p className="source-muted">
          Save edits as a new draft before confirming.
        </p>
      )}
      {localError && (
        <p role="alert" className="source-error">
          {localError}
        </p>
      )}
      {action.task.state === "error" && (
        <p role="alert" className="source-error">
          {action.task.message}
        </p>
      )}
    </section>
  );
}
