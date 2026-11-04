# Magi

A local brand-aware campaign workspace. This first milestone provides the React workspace shell, FastAPI backend, SQLite connection, environment configuration, and Docker Compose startup.

Brand discovery, Markdown import, chunking, source metadata, an in-app source browser tracked OpenAI calls and explicit candidate policy extraction are available. Campaign planning, asset generation and editing, memory, and the in-app run debug view are subsequent milestones. Campaign controls remain disabled. App startup makes no AI calls; a model request occurs only through an explicit client or smoke-test invocation.

## Run with one command

Requires Docker Engine/Desktop with Docker Compose and network access for the first build.

```bash
mkdir -p data && docker compose up --build
```

Open http://localhost:5173. API documentation: http://localhost:8000/docs. The workspace connection indicator checks `/api/health` through the frontend proxy, including a real SQLite query.

API keys are optional for this milestone. Before later AI features, copy `.env.example` to `.env` and fill in your own keys. Never commit `.env`.

Stop with `Ctrl+C`, or `docker compose down`. SQLite persists in `./data/magi.db`. Source brand materials are mounted read-only. Both host ports bind to localhost. Set `FRONTEND_PORT` or `BACKEND_PORT` in `.env` if the defaults are occupied.

## Develop without Docker

Requires Python 3.12+ and Node.js 22+.

From the project root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

The frontend proxies `/api` to `http://127.0.0.1:8000`. For a different backend address, set `API_PROXY_TARGET` in the frontend process environment. Relative database paths resolve from the project root regardless of the working directory.

## Checks

```bash
.venv/bin/python -m pytest backend/tests
.venv/bin/python -m ruff check backend
npm --prefix frontend run build
docker compose config --quiet
```

## Layout

- `backend/app/api`: HTTP routes.
- `backend/app/config.py`: environment configuration; keys use secret types.
- `backend/app/db.py`: SQLite engine and session lifecycle, WAL and foreign keys.
- `frontend/src`: React workspace, API client, and responsive styles.
- `data/`: generated local storage, ignored by Git.
- `wickhold-candidate-package/`: supplied brand source materials.

Magi's application styling is separate from the brands whose campaign assets it will render. There are no Wickhold brand details hardcoded into the app.

## Brand storage foundation

Startup creates missing `brands` and `documents` tables using SQLAlchemy
`Base.metadata.create_all()`. Existing records are preserved. This does not
alter existing table definitions; introduce versioned migrations before changing
an established schema. No brand files are imported automatically at this stage.

Documents retain their brand, relative path, SHA-256 content hash, original text,
optional title and timestamps generated in UTC. SQLite stores these timestamps
without a timezone offset; interpret them as UTC. A relative path is unique within
a brand, and foreign keys reject documents without an existing brand. Document
approval, authority and effective dates are intentionally not inferred here.

## Discover brand source files

After installing the backend, run from the project root:

```bash
.venv/bin/python -m app.brand.discovery
```

This read-only command prints a JSON inventory with relative paths, categories,
file sizes, UTC modification times, counts and warnings. It reads no document
contents and writes no database records. The configured source root is
`MAGI_BRAND_SOURCE_DIR`; relative roots resolve against the project directory.
Set `MAGI_DOCUMENT_DIRECTORY`, `MAGI_IMAGE_DIRECTORY` and `MAGI_BRIEF_DIRECTORY`
to change the default `brand`, `images` and `briefs` boundaries. These must be
visible, relative, distinct, non-overlapping directories within the source root.

All Markdown under the document boundary is inventoried, including archives,
internal notes and drafts. Discovery does not establish authority or approval.
Briefs remain campaign inputs, separate from brand knowledge. Images are found
independently of metadata, including SVG files without metadata records.

The supplied package yields 83 brand documents, 133 image candidates, one image
metadata file, three briefs and one other file (221 total). Unsupported files
and the package README are listed as `other`. Hidden entries and symbolic links
are skipped with warnings. Missing source/document directories are fatal;
missing optional image/brief directories generate warnings. Filesystem scan
errors set `complete` to false. The command exits with status 1 for a fatal or
incomplete scan, and status 0 otherwise. Use the explicit import command below for parsing and persistence.

## Import brand Markdown

Install the updated backend dependencies, then run from the project root:

```bash
.venv/bin/python -m pip install -e './backend[dev]'
.venv/bin/python -m app.brand.ingestion --brand-name "Wickhold"
```

The command discovers sources using the configured folder boundaries and imports
only `brand_document` records. Markdown is decoded strictly as UTF-8 and retained
without rewriting whitespace, line endings or tables. SHA-256 hashes use original
file bytes. Titles use the first top-level Markdown heading (outside code fences
and leading YAML front matter), falling back to the filename stem.

A brand is identified by its resolved source-root path. Its display name is set
on the first import; subsequent runs reuse it without renaming. Documents are
matched by brand and relative path. Unchanged hashes preserve records and
timestamps; changed files update in place. Missing source files are reported but
retained, not deleted. An unreadable or invalid UTF-8 source leaves its old
content intact. Other valid files can still be imported.

The JSON report contains `brand_id`, `source_root`, `status`, lists for `added`,
`updated`, `unchanged`, `failed`, `missing`, category counts, discovery warnings,
and any database error. File failures yield `partial` status and exit code 1.
Database failures roll back all changes and yield `failed` status. A complete
successful import exits 0, including when missing files are reported. Incomplete
discovery cancels the import before database writes. Initial schema creation is
separate from the single transaction for brand/document records.

For the supplied package, a fresh import adds 83 documents; a second unchanged
import reports 83 unchanged documents. Browse them in `data/magi.db`. Images,
briefs and the package README are not imported as brand knowledge. No AI calls,
search indexing, policy extraction or authority decisions occur yet. Chunking is an explicit next command described below.
Missing-source lifecycle handling must be added before retrieval uses retained
records. Run imports serially for a source root; concurrent ingestion is not
supported in this milestone.


## SQLite directory permissions

The backend container runs as `MAGI_UID:MAGI_GID` (defaults `1000:1000`) so files
in the shared data directory remain writable by the local developer. Set these
values in `.env` to match `id -u` and `id -g` when different. Create `data/` before
starting Compose, as shown in the startup command, so Docker does not create a
root-owned bind-mount directory.

If an earlier container created files under another account, stop the app and
repair ownership from your host terminal, then rerun the import:

```bash
docker compose down
sudo chown -R "$(id -u):$(id -g)" data
.venv/bin/python -m app.brand.ingestion --brand-name "Wickhold"
```

SQLite needs write access to both the database file and its directory for journal
files. Keep the existing database; deleting it or opening permissions to everyone
is unnecessary. After changing the Compose user, recreate containers with
`docker compose up --build`.

## Build section-aware chunks

After importing documents, use the `brand_id` from the import report:

```bash
.venv/bin/python -m app.brand.chunking --brand-id "<brand-id>"
```

This creates the additive `document_chunks` table if needed and chunks stored
Markdown without rereading source files. No embeddings or model calls are made.
Use `--target-chars 2400` to change the default positive character target. This is
a soft sizing heuristic, not a model token count. Tables, lists, code and long
paragraphs remain intact even when oversized; the JSON report lists oversized
chunk IDs, source paths, positions and character counts.

Chunks follow heading sections. Each stores its heading path, exact original
Markdown slice, zero-based position, start/end character offsets (end exclusive),
one-based inclusive start/end line numbers, document hash, chunker version,
target size and oversized flag. Offsets are Unicode character positions in stored
`raw_text`, not byte offsets. Whitespace-only chunks are omitted; all other source
content, including preambles, remains represented. Heading-looking text inside
fences or leading YAML front matter does not create a section. Chunks do not
establish that a claim is approved or that a qualifier is sufficient; that is the
later policy/context layer's responsibility.

Re-running with matching document hashes, chunker version and target preserves
existing chunk IDs. Changing any of these rebuilds affected documents. An import
that updates a document invalidates its old chunks in the same transaction;
run chunking again after imports. Empty documents create no chunks and are
reported explicitly. All replacements in a chunking run commit together; database
failures roll back the run and return a failed report with exit status 1.
Unknown brand IDs and invalid targets also fail clearly.

Inspect `document_chunks` in a SQLite viewer, or join sources for review:

```sql
SELECT d.relative_path, c.position, c.heading_path,
       c.start_line, c.end_line, c.oversized, c.content
FROM document_chunks AS c
JOIN documents AS d ON d.id = c.document_id
WHERE d.brand_id = '<brand-id>'
ORDER BY d.relative_path, c.position;
```

The report's `processed`, `rebuilt`, `unchanged` and `empty` counts describe
documents; `empty` is a subset of processed documents, and `total_chunks` is the
number of chunks after a successful run. Missing-source records remain retained
as agreed; source lifecycle handling must be completed before retrieval serves
their chunks. Run imports and chunking serially for this local milestone.

## Collect source metadata candidates

After importing documents, run:

```bash
.venv/bin/python -m app.brand.source_metadata --brand-id "<brand-id>"
```

The deterministic extractor collects explicit ownership, revision/check/review
date, authority and supersession statements from stored Markdown. It uses generic
English marker patterns, never brand names, filenames, file modification times or
a rule that the newest year wins. Dates remain exactly as written, including
partial dates. Unmarked authorship and implied authority remain unknown. Code,
blockquotes, quoted paragraphs and explicitly labelled example sections are
excluded; arbitrary YAML front matter is not interpreted. This conservative
pass is not exhaustive semantic extraction, and absence of a candidate is not
evidence that no restrictions exist.

Every candidate stores its original source line, character offsets, inclusive
line range, matching rule and `verification_status: candidate`. It does not approve
policies, normalize dates, rank documents or resolve conflicts. Lines containing
multiple signals may produce a candidate in more than one category. Supported
categories are `owner`, `date`, `authority` and `supersession`.

`source_annotations` contains one row per document, including an empty candidate
list when nothing is recognized. The source hash and extractor version make
repeat runs reusable. Source changes invalidate annotations in the same import
transaction; rerun extraction after imports. Database failures roll back the
entire metadata pass. Unknown brand IDs fail with exit status 1. Successful runs
exit 0 and print JSON with counts, grouped document candidates, per-document
`unknown_categories`, and an `unknown` list of paths with no candidates at all.

Chunks access annotations through their document IDs, rather than duplicating
metadata. `get_chunk_source_metadata(session, chunk_id)` returns validated current
candidates; missing or stale metadata returns an empty list, while unknown chunk
IDs raise an error. Rebuilding chunks does not invalidate document annotations.
Stored sources that have disappeared from disk remain subject to the previously
noted source-lifecycle limitation before retrieval is enabled.

Inspect the links without duplicating stored metadata:

```sql
SELECT d.relative_path, a.source_hash, a.extractor_version, a.candidates
FROM source_annotations AS a
JOIN documents AS d ON d.id = a.document_id
WHERE d.brand_id = '<brand-id>';

SELECT c.id AS chunk_id, c.heading_path, a.candidates
FROM document_chunks AS c
JOIN documents AS d ON d.id = c.document_id
JOIN source_annotations AS a ON a.document_id = d.id
WHERE c.source_hash = d.content_hash AND a.source_hash = d.content_hash;
```

No OpenAI calls or costs are introduced by this milestone. Full policy extraction
and source-authority validation remain separate later work.

## Inspect sources in the app

Start the app, then select **Sources** in the sidebar (or mobile navigation), or
open http://localhost:5173/#sources. Choose a brand and filter documents by title
or relative path. The inspector has Document, Chunks, Metadata and Policies tabs.

Document shows escaped original Markdown with line numbers. Chunk cards show
heading paths, source lines, character counts and oversized flags. Metadata shows
document-level candidates shared by its chunks, never approval decisions. Use
**View in document** to jump to and highlight evidence lines. Arrow keys, Home
and End move between inspector tabs. Technical IDs and hashes are collapsed by
default.

Sources is read-only: there are no import, extraction, edit or deletion controls.
Run existing CLI commands to update stored sources, then reload the page. Missing
chunks and metadata are distinguished from a completed extraction that found no
candidates. Hash/version mismatches are labelled stale; stale evidence remains
inspectable, but its source jumps are disabled. Current means derived from the
stored document and current processor version, not approved or verified against
a live filesystem scan. Missing-source lifecycle limitations still apply.

Read endpoints (also listed in http://localhost:8000/docs):

- `GET /api/brands`: IDs, names and document counts.
- `GET /api/brands/{brand_id}/documents`: sorted document summaries.
- `GET /api/brands/{brand_id}/documents/{document_id}`: original text, chunks and annotations.

The API checks document ownership against the selected brand and returns 404 for
unknown resources. It reads SQLite only; it does not accept filesystem paths.

## Tracked OpenAI calls

Install the updated dependencies:

```bash
.venv/bin/python -m pip install -e './backend[dev]'
```
 All future text-model operations should go through
`app.llm.client.TrackedOpenAI`. Its `run(ModelRequest(...), output_model=...)`
method requires an operation, model, instructions, text input and explicit
`max_output_tokens`. Optional context links brand, document, campaign, asset and
source references to the run. It returns the local run ID, output text, optional
validated Pydantic output and provider response. Call `close()` on owned clients.
Initialize tables using the existing `initialize_database(engine)` before use.

The client lazily reads `OPENAI_API_KEY`. Existing app startup and source commands
do not require it. Calls use Responses API text requests, `store=false`, standard
service tier, non-streaming output, `MAGI_OPENAI_TIMEOUT_SECONDS` (default 60), and
zero SDK retries. An optional `prompt_cache_key` is forwarded and logged, but does
not guarantee a cache hit. Embeddings, provider tools, streaming and background
requests are outside this first wrapper.

For structured JSON output, pass a Pydantic object model whose object schemas use
`ConfigDict(extra="forbid")` and whose fields are all required. Express optional
values as required nullable fields, rather than defaults. Unsupported schemas
fail before any API call. The exact JSON schema is logged and returned text is
validated strictly against the model after receipt. Request-format validation
failures create no run because nothing was sent; returned output validation
failures do retain their response and usage.

### Inspect runs without calling OpenAI

```bash
.venv/bin/python -m app.llm.cli list --limit 20
.venv/bin/python -m app.llm.cli show '<run-id>'
```

These commands work without an API key. Full records include the request body,
response body, source references, provider IDs, timestamps, duration, configured
timeout, raw usage, token breakdown, cache-read ratio, estimated cost and pricing
snapshot. Credentials/authorization material is redacted; other prompt content
is retained locally in the ignored database. Logs contain no transport headers.
Run context is audit information rather than cascading foreign keys, so it can
outlive rebuilt chunks and later campaign records.

### Explicit, billable smoke test

```bash
.venv/bin/python -m app.llm.cli smoke --model gpt-4.1-mini
```

Only this explicitly invoked command sends a small prompt with a 64-token output
cap. It is never run by startup or tests. The default preserves our agreed smoke
model; the official catalog currently labels GPT-4.1 mini deprecated, and access
may vary by project. Use `--model` to choose another available OpenAI model. An
unpriced model still works, with cost reported as unknown until a verified rate
entry is added. No live smoke test has been performed during implementation.

### Usage, pricing and failure semantics

`llm_runs` is additive: startup creates the new table without altering existing
source tables. A `started` record commits before sending; a logging failure here
prevents the API request. The client holds no database transaction across network
I/O. Completed responses update that record in a separate transaction.

Statuses distinguish `completed`, `provider_error`, `timeout`, `refused`,
`incomplete`, `validation_error`, `client_error` and `configuration_error`.
Non-completed runs raise `ModelRunError` with a run ID for inspection. Missing
credentials are `not_sent` with zero cost and unknown token counts. Errors with
no usage have unknown billing, not zero cost. Interrupted processes may leave a
`started` record: do not assume that means no provider charge occurred. No record
is automatically resumed or retried. After a response, a database logging failure
raises `RunLoggingError` with its run ID and an in-memory, sanitized result for
caller recovery. It must not cause another model call.

Costs use decimal arithmetic. `app/llm/costs.py` contains a versioned registry,
initially supporting `gpt-4.1-mini` and its `2025-04-14` snapshot at standard text
rates: $0.40/M input, $0.10/M cache reads, $1.60/M output. Each run retains the
applied rates, source URL and verification date; changing rates does not reprice
history. Returned model IDs must match a registry entry exactly—there is no fuzzy
alias fallback. Returned nonstandard service tiers remain unpriced.

Ordinary input excludes cache reads and separately billed writes. Cache writes
are priced only when the registry explicitly provides a verified write rate;
positive writes with unsupported pricing produce unknown cost. For the legacy
mini snapshot, omitted write counts have no separate write charge; the raw and
normalized omitted field remains unknown. Reasoning tokens are retained as an
output breakdown and are not billed a second time. Missing counts, malformed
breakdowns and unsupported pricing also produce unknown cost with a reason.
Cache-read ratio is cached input / input (zero when input is zero), not a
campaign-level request hit rate. Estimates are not provider invoices. The raw
usage object remains available for later billing-rule changes.

Official references: [model pricing](https://developers.openai.com/api/docs/models/gpt-4.1-mini),
[prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching),
and [structured output](https://developers.openai.com/api/docs/guides/structured-outputs).
Automated tests mock the SDK HTTP transport; they incur no API charges. The
in-app run/debug view and actual policy extraction are separate future milestones.

## Extract policy candidates from selected documents

Choose document IDs from Sources → Document → Technical details. Then explicitly
invoke the billable extraction command (the model must be available to your key):

```bash
.venv/bin/python -m app.brand.policies \
  --brand-id '<brand-id>' \
  --document-id '<first-document-id>' \
  --document-id '<second-document-id>' \
  --model '<available-openai-model>'
```

There is no default model or all-documents option. All IDs must belong to the
brand; the complete selection is validated before any model call. Documents over
24,000 Unicode characters are rejected before calls. Each selected document is
sent whole as JSON line records (line number plus literal source text), through
`TrackedOpenAI`, using a 6,000-output-token
cap. Source text is treated as untrusted evidence, not operating instructions.
No automatic retries occur. Startup and automated tests never invoke live policy
extraction. Set `OPENAI_API_KEY` for an explicitly requested real run.

The output schema distinguishes prohibitions, permissions, required wording,
qualifiers and approval requirements. Scope includes subjects, channels,
audiences, regions and source-worded dates. Unstated scope, conditions and
exceptions remain null; empty lists do not mean universal applicability.
Conditions and exclusions may require evidence from several document locations.

Every supporting quotation must appear literally and uniquely inside its claimed
source line range. Markdown and original line endings must match; the backend
does not fuzzy-match or rewrite evidence. It calculates character offsets and
actual source line ranges locally. Exact required wording must occur in cited
evidence. If any candidate fails these checks, none of that batch is saved.
These checks establish text correspondence only. A quotation matching the source
does not establish that the model interpreted its meaning, scope, exceptions,
source authority or currency correctly. All rules remain `candidate`; there is no
approval, enforcement or conflict-resolution step yet.

Successful batches are stored in the additive `policy_extractions` table, one
current successful batch per document, linked to the tracked run. The source
hash, requested model, extractor/prompt/schema versions and output limit control
reuse. Matching successful empty batches are also reused without another API
call. `--force` explicitly requests fresh calls and can incur charges. A failed
forced attempt retains the prior successful batch. A source update invalidates
its batch within the import transaction while retaining model-run audit records.

The final source-hash check and persistence run in a short SQLite write
transaction after model I/O. A changed source rejects the result. No database
transaction is held during model I/O, and persistence failure never automatically
repeats a paid request. Read-only cache reuse also rechecks that the selected
batch and document version still exist.

CLI JSON reports contain extracted/reused/failed counts and per-document status,
candidate count, failure details and run IDs. A failure exits 1; successful empty
results or reuse exit 0. A tracked run can show `completed` when the model returned
schema-valid output but later quotation checks or candidate persistence failed;
inspect the policy command's report as well as the run. Rejected model output is
retained in the tracked run. Inspect requests, responses, usage and estimated
costs with:

```bash
.venv/bin/python -m app.llm.cli show '<run-id>'
```

Reload Sources and open the **Policies** tab to inspect successful batches,
conditions, exceptions and source evidence. Current means the stored source hash
and extraction configuration match, not that a rule is approved. Stale batches
remain inspectable with source jumps disabled. No extraction or approval buttons
are added to the browser. Live model quality has not been evaluated during
implementation; tests use mocked responses, including fixtures for FedRAMP and
customer permissions from the supplied legal documents.

### Replay a saved policy response without another API call

If a schema-valid run was rejected during evidence validation, a compatible saved
response can be checked again locally:

```bash
.venv/bin/python -m app.brand.policies \
  --brand-id '<brand-id>' --document-id '<document-id>' \
  --model '<original-model>' --from-run '<saved-run-id>'
```

Replay requires exactly one document and cannot be combined with `--force`. It
constructs no model client and needs no API key. It verifies the original run's
operation, brand, document, source hash, model, prompt, schema and output limit.
It then grounds the saved response against the current source and persists only
if all checks pass. The report uses `replayed: 1`; the original model run and its
cost remain unchanged, and no new model-run row is created.

Extractor v2 permits one narrowly recorded citation adjustment: added leading or
trailing `...` / `…` omission markers and adjacent whitespace may be removed if
the remaining text matches uniquely and exactly within the claimed lines. It does
not repair internal omissions, Markdown changes, words, case or wrong line
ranges. Persisted quotations are always exact slices from the source. Adjusted
citations retain `model_quotation` and `match_method: boundary_ellipsis`, displayed
in the Policies tab; the raw model response also remains unchanged. Matching
source text still does not validate a candidate's semantic interpretation.

Rebuild the app after code/schema-contract updates before reloading Policies.

### Test keyword retrieval in Sources

Rebuild the app after updating:

```bash
docker compose up --build -d
```

Open **Sources → Retrieval test**, select the brand and click **Prepare / rebuild
index**. Enter a question and press **Search**. Each ranked result shows the
original chunk, its document path, headings and source lines. **View in document**
opens the source with those lines highlighted; returning to Retrieval test keeps
your results. Use **Run evaluation** for the installed 20-case Wickhold suite,
expand individual cases to compare expected and retrieved evidence, and download
the JSON report.

Index preparation writes only a derived SQLite FTS5 index. Searching and
measurement are read-only and make no OpenAI calls. No API key is needed.
Documents must already be imported and chunked. Changed source/chunk versions
invalidate the index; rebuild after updating ingestion or chunking. Stale chunks
are excluded, rather than silently searched. Each brand has its own FTS table so
other brands do not affect BM25 statistics.

The same operations are available from the CLI:

```bash
.venv/bin/python -m app.brand.retrieval index --brand-id '<brand-id>'
.venv/bin/python -m app.brand.retrieval search --brand-id '<brand-id>' \
  --query 'FedRAMP certification status' --limit 10
.venv/bin/python -m app.brand.retrieval evaluate --brand-id '<brand-id>' \
  --suite-id wickhold-baseline-v1 --output data/evaluations/keyword-baseline.json
```

The fixture lives in `evaluation/wickhold-baseline-v1.json`: 6 direct questions,
6 paraphrases, 6 questions requiring multiple evidence groups, and 2 distinctive
absent-product queries. Expected groups reference unique, exact source quotations;
the evaluator validates them against stored documents and current chunks before
scoring. Alternative quotations within a group count as one evidence requirement.
Custom local suites can be supplied through CLI `--cases path.json`; the HTTP API
accepts only registered suite IDs. Docker mounts `evaluation/` read-only.

Coverage @5/@10 is the fraction of expected evidence groups found. “All evidence”
counts positive cases with every required group found. MRR @10 measures the rank
of the first relevant chunk in each positive case (zero if missing); this is not
precision or answer quality. The two negative tests only check distinctive names,
not general no-answer detection. Reports include corpus/suite fingerprints,
retrieved passages, missing groups and timings for comparison with embeddings.

Retrieval uses Unicode word tokens joined with OR and BM25 weights 2/2/1 for
title/headings/body. Scores are relative ranking values, with lower values first;
they are not confidence probabilities. Archives and conflicting documents remain
searchable. This evaluates the imported database snapshot, not whether the source
folder still contains each file, which document is authoritative, policy
compliance, or generated-answer correctness. No policy candidates are applied.

### Prepare chunk embeddings (CLI)

This milestone stores vectors for later semantic retrieval. The webpage still
uses keyword search; semantic search and comparison are the next milestone.

Install the updated backend dependencies, or rebuild the Docker backend:

```bash
.venv/bin/python -m pip install -e './backend[dev]'
# Docker alternative:
# docker compose up --build -d
```

Inspect the pending work first. This command does not call OpenAI, create model
runs or write vectors, and does not require an API key:

```bash
.venv/bin/python -m app.brand.embeddings \
  --brand-id '<brand-id>' --dry-run
```

`tiktoken` counts the complete input, including the document title and heading
path. Its first use downloads the `cl100k_base` encoding; subsequent uses load its
local cache. For an offline machine, populate `TIKTOKEN_CACHE_DIR` beforehand.
Missing dependencies or an unavailable encoding produce an actionable error;
character-count estimates are never substituted for token counts.

To generate vectors, set `OPENAI_API_KEY` in the root `.env`, then explicitly run:

```bash
.venv/bin/python -m app.brand.embeddings --brand-id '<brand-id>'
# Within Docker, use the container's Python instead:
# docker compose exec backend python -m app.brand.embeddings --brand-id '<brand-id>'
```

Generation sends title, headings and original chunk content to
`text-embedding-3-small`, requesting 1,536 dimensions. No extracted metadata or
policy candidates are inserted into that input. Vectors are stored as JSON in
SQLite, linked to chunks and the model run that produced them. The new table is
created additively on application startup or normal command execution. Dry-run
also works on an imported database before this table exists.

Only chunks matching their document hash and the current chunker version are
eligible. Existing vectors are reused when the model, dimensions, input-format
version, source hash, chunker version and exact input hash match and the vector
is structurally valid. Re-importing changed documents or rechunking deletes their
old vectors through a foreign-key cascade. Updating a title changes the embedding
input and refreshes its vectors on the next run.

Batches run sequentially, with at most 32 inputs and 32,000 tokens per request.
Each input must have at most 8,191 tokens. Oversized inputs fail preflight before
any provider requests; the report identifies their chunks. Source text is never
silently truncated. Each validated batch is saved in a short transaction, after
checking that its source still matches. No transaction stays open during model
I/O. Run one preparation command at a time.

A failure stops further batches but preserves completed work. Rerun the same
command to resume. Before sending pending inputs, it checks successful saved
embedding runs and recovers exact, source-matched responses, including when
logging succeeded but saving vectors failed. Recovery does not require an API
key, create another model run or count the old run's cost again. Unknown outcomes
such as timeouts or failed response logging cannot be recovered this way and may
already have been billed; inspect the run before explicitly retrying. SDK retries
are disabled. This is resumable persistence, not an exactly-once billing guarantee.

The JSON report includes eligible/reused/generated/recovered/pending counts,
stale chunks excluded, stale vectors, oversized chunk IDs, planned batches,
pending token and cost estimates, new run IDs and recovered run IDs. Dry-run's
`recoverable` count remains part of `pending`; its pending tokens and projected
cost cover only inputs that still need a provider request. An unchanged repeat
has zero new provider calls. Normal failures exit 1. Dry-run exits 0 when inspection
succeeds, including when oversized inputs are reported for correction.

Each provider attempt records its exact request and response (with credentials
redacted), returned model, request ID, latency and raw usage. Embedding costs use
provider `prompt_tokens`, separately from text-generation usage handling. The
standard-price snapshot is $0.02 per million input tokens, verified November 2,
2026 against [OpenAI's model page](https://developers.openai.com/api/docs/models/text-embedding-3-small).
Missing or inconsistent usage, or unknown model pricing, remains unknown. The
command's actual cost totals cover only attempts made by that invocation;
preflight projections and recovered historical costs are separate.

Inspect any reported run with:

```bash
.venv/bin/python -m app.llm.cli show '<run-id>'
```

Automated tests use mocked OpenAI responses and injected token counts. They do
not incur API charges or establish real-world semantic retrieval accuracy.

### Semantic search and retrieval comparison

After preparing chunk embeddings, rebuild/restart the app and open **Sources →
Retrieval test**. Choose **Semantic**, refresh readiness, then submit a question.
Each explicit semantic search sends the question alone to OpenAI using the same
embedding model and dimensions as the stored document vectors. It compares those
vectors locally with exact cosine similarity; no FAISS server or separate vector
index is required. Higher scores rank first. Scores are similarities, not
confidence percentages. Document source links still open and highlight the
original lines.

Semantic readiness reports current, missing, stale and invalid vectors. Every
eligible chunk must have a valid current vector before any query API call is
attempted. If preparation is incomplete, run the embedding CLI again; the browser
never automatically regenerates document vectors. Semantic search works without
preparing the keyword index. Keyword mode retains its separate FTS5 preparation.
Neither mode applies policy candidates or resolves source authority.

Semantic search and evaluation are explicit billable actions. Method changes,
readiness checks and keyword searches make no model calls. There is no question
embedding cache in this milestone, so repeating a semantic query makes another
tracked embedding request. Results show the query's usage, estimated cost and run
IDs. Missing usage remains unknown, and SDK automatic retries remain disabled.

Choose a method and run the existing evaluation suite. Run both methods to show
the comparison table; it appears only for matching suite and corpus fingerprints.
Each method's latest report is retained while switching methods in the current
brand workspace. Reports include per-category evidence coverage, source chunks,
missing evidence, method configuration, fingerprints and query-embedding costs.
Download filenames identify the method. Semantic evaluation batches the questions
under the existing 32-input/32,000-token limits and reuses one local document-vector
snapshot for ranking. It uses the same gold evidence groups and scoring rules as
keyword evaluation.

```bash
.venv/bin/python -m app.brand.retrieval search \
  --brand-id '<brand-id>' --method semantic \
  --query 'What happens if an agent gateway goes down?' --limit 10

.venv/bin/python -m app.brand.retrieval evaluate \
  --brand-id '<brand-id>' --method semantic \
  --suite-id wickhold-baseline-v1 \
  --output data/evaluations/semantic-baseline.json
```

Omitting `--method` preserves keyword behavior. CLI semantic commands make paid
query-embedding calls; keyword commands do not. Embedding runs can be inspected
using the existing `app.llm.cli show <run-id>` command. These requests log the
operation `embed_queries`, not document preparation's `embed_chunks`.

Semantic nearest-neighbour search normally returns results for absent products,
too. No similarity threshold is applied. The two negative tests are reported
honestly and are not an answerability classifier. A retrieved neighbour does not
prove that the documents support an answer. Compare positive-case coverage and
complete evidence, then inspect individual failures before drawing conclusions.

Total evaluation duration includes loading, provider calls and validation.
Semantic per-case latency measures local cosine ranking because the question
embeddings are generated together; embedding-call duration is reported separately.
Keyword per-case latency measures its retrieval operation. Use total duration for
the end-to-end comparison rather than treating these per-case times as equivalent.

API additions:

- `GET /api/brands/{id}/retrieval/embeddings`: read-only vector readiness.
- `POST /api/brands/{id}/search`: `{ "query": "...", "limit": 10, "method": "semantic" }`.
- `POST /api/brands/{id}/retrieval/evaluate`: adds optional `method`, defaulting to `keyword`.

The legacy GET search endpoint remains keyword-only. Query calls use POST so
opening or prefetching a GET URL cannot trigger an embedding charge. Frontend
waits allow 90 seconds for semantic search and 300 seconds for evaluation; the
configured provider timeout still applies to each call. Switching brands aborts
obsolete browser requests, but does not guarantee that an already-started backend
or provider call is cancelled. Inspect recorded runs before repeating a timed-out
operation. Corpus or vector changes during a run reject its results rather than
silently returning scores from mixed snapshots.

### Hybrid retrieval (RRF)

Choose **Hybrid** in Sources → Retrieval test to combine exact keyword matches
with semantic neighbours. Both the keyword index and all eligible chunk vectors
must be current for the same corpus. Hybrid readiness shows both components; use
**Prepare / rebuild index** to prepare FTS5, and the embedding CLI to repair
missing or outdated vectors. There is no automatic fallback or vector generation.

Hybrid retrieves up to 50 candidates from each method, merges by chunk ID and
sums `1 / (60 + rank)` for each ranking in which the chunk appears. Both methods
have equal weight; missing rankings contribute zero. Duplicate chunks contribute
at most once per component. Higher RRF scores rank first; ties use the best
component rank, document path, start line and chunk ID. The requested result limit
is applied after fusion. Raw BM25 and cosine scores are not directly added.
Ranking details show RRF, keyword rank/score and semantic rank/score.

Each submitted hybrid search embeds its question once, just like semantic search.
Keyword retrieval and fusion are local. No question cache or similarity threshold
is applied. All keyword query limits are checked before a billable call. Changes
to source data, vectors or index readiness during execution reject the results.
RRF scores are ranking values, not confidence or source-authority decisions.

```bash
.venv/bin/python -m app.brand.retrieval search \
  --brand-id '<brand-id>' --method hybrid --query 'FedRAMP certification status'

.venv/bin/python -m app.brand.retrieval evaluate \
  --brand-id '<brand-id>' --method hybrid \
  --suite-id wickhold-baseline-v1 --output data/evaluations/hybrid-baseline.json
```

The existing POST search/evaluation APIs also accept `method: "hybrid"`.
`GET /api/brands/{id}/retrieval/hybrid` checks combined readiness without a model
call. The legacy GET search remains keyword-only.

Run each evaluation method explicitly to compare the same evidence suite in the
webpage. The comparison supports any two or all three methods. Suite and corpus
fingerprints must match; Semantic and Hybrid must also share a vector fingerprint.
Hybrid evaluation embeds the questions once in batches and reuses them for its
semantic candidate lists. Exported configuration records candidate depth, RRF
constant and both component configurations. Per-case latency covers local
candidate retrieval/ranking/fusion; batch embedding time is reported separately,
and total duration includes the full operation.

The candidate depth and RRF constant are fixed defaults for this first comparison.
Do not assume hybrid improves every case: inspect actual coverage, complete
evidence and missed groups. Absent-product queries can still return unrelated
neighbours; neither hybrid nor semantic retrieval establishes answerability or
policy compliance. Automated verification uses mocked query embeddings and incurs
no API charges. Live evaluation remains an explicit billable action.

### Brief planner: inspect plan → retrieve → assemble

Rebuild/restart the app, then open **Sources → Brief planner**:

1. Enter a campaign brief and click **Plan searches**. Inspect the interpreted
   topic, products, audience, channels and requested assets, then each proposed
   query and its purpose. Unspecified details remain unknown. Asset counts do not
   multiply queries. Planning makes one tracked structured-output model call.
2. Click **Retrieve all**. This runs every planned query through hybrid retrieval
   and shows the chunks grouped by query. Query embeddings are batched together;
   usage and costs are separate from the planner call. Both the keyword index
   and complete current vectors must be ready. No fallback or vector regeneration
   runs automatically. Failed groups remain visible; a provider batch failure
   marks all affected queries as failed. There are no automatic paid retries.
3. Click **Assemble context** with a 1,000–12,000-token budget (default 4,000).
   This stage makes no OpenAI call. It deduplicates by chunk ID across all groups,
   preserves all query/purpose attributions, interleaves results by rank and query
   order, and selects complete original passages that fit the budget. It never
   silently truncates passages, merges distinct facts, or summarizes away conflicts.

The final view shows the exact context text, counted tokens, selected sources,
duplicate count, excluded chunk IDs and uncovered search IDs. Brief details and
warnings count toward the budget too. Assembly reports evidence gaps and does
not establish source authority, approve extracted policies, or detect all semantic
contradictions. Inspect exclusions before relying on the package. A failed batch
with no successful evidence cannot be assembled; partial results may be assembled
with explicit warnings. The tokenizer's first use needs its encoding cache as
explained in the embedding setup section.

Editing the brief or replanning clears retrieved/assembled results. Retrieving
again clears the compact result; changing the budget clears only the compact
result. Changing brands resets the workspace. Source/chunk changes invalidate
server-side stage results, requiring a fresh plan. Browsing a source and returning
to the planner preserves the current manual workflow. Stage data is persisted in
an additive `context_artifacts` table with brand, parent stage, corpus fingerprint
and payload; model costs remain in `llm_runs`. A page reload resets the interactive
workspace, but saved artifacts and audit runs remain available in SQLite.

Backend services in `app.brand.context` are independent of React and FastAPI:
`plan_retrieval`, `execute_retrieval`, and `assemble_context`. A future agent can
call `get_brand_context(engine, brand_id, brief)` to execute them in sequence and
receive the same final package. The current UI deliberately calls each stage
separately; the wrapper is not automatically invoked by a campaign chat yet.

API stages accept server-side artifact IDs rather than client-supplied evidence:

- `POST /api/brands/{id}/context/plan`: `{ "brief": "..." }`.
- `POST /api/brands/{id}/context/retrieve`: `{ "plan_id": "..." }`.
- `POST /api/brands/{id}/context/assemble`: `{ "retrieval_id": "...", "max_tokens": 4000 }`.

Briefs must be nonblank and at most 6,000 characters. Plans contain 1–8 queries
with unique IDs and distinct normalized text, each satisfying existing hybrid
query limits. The planner produces information needs, not verified brand facts or
campaign content. It receives the brief, not the full document library. The
model is configurable with `MAGI_RETRIEVAL_PLANNER_MODEL`, initially
`gpt-4.1-mini`; unrecognized pricing remains unknown. Planner and query requests
use the existing redacted audit logs and disabled SDK retries. Assembly is
source-preserving selection, not an LLM summary. Automated tests use mocked
provider responses and controlled token counters; live planner quality and full
end-to-end context usefulness still need explicit evaluation on supplied briefs.

### Source authority and evidence conflicts

Rebuild/restart after this update to create the additive `authority_versions`
table. In **Sources → Source authority**, click **Propose authority map** to
inspect a source-backed draft. Proposal discovery uses governance wording as a
hint and includes a document manifest; it never establishes authority by itself.
The proposal is a tracked OpenAI call. A quotation match establishes textual
support, not a semantically correct interpretation. No map becomes active until
you review and explicitly confirm it.

Groups assign document IDs a priority (lower wins), topics and channel scope.
Same-standing groups may have equal priority. `"*"` means explicitly all;
`null` channels means unknown and cannot establish precedence. Topic names are
`claims`, `certification`, `customer_references`, `pricing`, `visual`, `voice`,
`channel`, `product`, `audience`, `general`, or `*`; a topic does not implicitly
include other topic names. `governing: true` adds those documents beyond the
retrieved shortlist during assessment, subject to channel applicability. Prefer
focused policy documents for these assignments. Explicit supersession links have
their own topic/channel scope and source quotations. Dates, filename years and
labels such as FINAL or CURRENT do not create precedence.

The UI shows assigned and unmapped sources, citation links and supersession
relationships. Edit the JSON to correct assignments, priorities, scopes or quotes,
then **Save new draft** and **Confirm reviewed authority map**. Saving or confirming
makes no model calls. Drafts are immutable versions; editing creates a new one.
Confirmation supersedes the previously confirmed version. This does not approve
all source contents or extracted policy summaries. If discovery finds no explicit
governance evidence, create a manual cited map in the editor instead of guessing
precedence. The map shape is:

```json
{
  "groups": [{
    "id": "governing_policy",
    "label": "Governing policy",
    "priority": 1,
    "document_ids": ["actual-document-id"],
    "topics": ["certification"],
    "channels": ["*"],
    "governing": true,
    "evidence": [{
      "document_id": "governance-document-id",
      "quotation": "Exact supporting text",
      "start_line": 3,
      "end_line": 3
    }]
  }],
  "supersessions": [],
  "notes": []
}
```

All assigned/cited sources and the complete document manifest are pinned. Document
changes (including new documents) make the map stale and require renewed review.
Changes to the authority version invalidate assessments and reviewed contexts;
retrieval artifacts remain inspectable when only the map changes. Current source
and authority checks also run inside the final artifact write transaction.

Brief planner exposes five manual stages:

**Plan searches → Retrieve all → Select assessment evidence → Check authority and conflicts → Assemble reviewed context**

Evidence selection uses the confirmed channel scopes to exclude unrelated governing
documents. Unknown source or campaign channels retain governing documents. It keeps
all retrieved passages, including historical alternatives, and omits search scores
from model inputs. Up to 12 governing sections are retained without a selection
model call. Larger collections use tracked model calls to classify complete
Markdown sections with exact-source exclusion citations, reasons and dependencies.
There is no fixed character cap, and source sections are never cut to fit.
Selection batches contain at most 12 complete sections. Their structured output
requires one named decision field per supplied section, preventing omitted or
repeated model-written section IDs. Coverage refers to the supplied batch and
its dependencies, not whether that batch answers the entire campaign brief.

Uncertain sections, retrieved sections, ancestor text, and dependency closure are
retained. The preview shows document applicability, source sections, exclusions,
known coverage gaps, and the exact assessment payload size. Review it before
assessment; excluded sections can be restored with their dependencies. Restoring
sections recalculates the budget. Invalid citations, incomplete selection coverage,
or evidence that remains over budget block assessment. Relevance classification
is fallible and does not certify policy coverage; review the original quotations.
No cross-batch conflict assessment is implemented: final conflict discovery still
receives all selected evidence together in one bounded request.

Selection artifacts are pinned to the retrieval, corpus, and confirmed authority
version. Changing sources or authority invalidates a selection. No authority scopes
or source content are automatically rewritten by selection.

Assessment requires a current confirmed map. It includes governing source passages
even when absent from the original search results, then makes a tracked model call
to extract atomic statements and propose contradictions. Sources remain untrusted
text. Quotations must match the claimed document lines and supplied passages.
The backend applies the confirmed map to overlapping subjects, attributes and
scope. Different products, tiers, channels or billing periods remain separate.
Unknown scope, ambiguous geography/dates, unmapped sources and same-level conflicts
do not receive an invented winner. Explicit, applicable supersession may resolve
conflicts; newer-looking documents alone do not. Nonconflicting lower-level facts
remain available. Model interpretations may still miss or misread conflicts.

The assessment view shows all source-backed statements, their scope, conflict
pairs, selected/rejected IDs and the deterministic basis. It does not automatically
approve extracted policy candidates. Review matching quotations and interpretations
before relying on the result. Assessment has no fixed character cap; optional
batches contain up to 50 complete passages, followed by cross-batch conflict review.
The output schema allows 80 extracted statements per assessment. A model-reported
incomplete analysis cannot be assembled into reviewed context.

Reviewed assembly selects original supporting quotations rather than reintroducing
raw passages containing rejected alternatives. Restrictions and both sides of
unresolved conflicts are budgeted first. If they cannot fit, assembly fails with
an incomplete-context error. Optional statements may be omitted with audit IDs.
Rejected alternatives remain visible in the audit view, outside usable context.
Unresolved conflicts remain explicit warnings; `authority_resolved` describes the
reported conflicts, not comprehensive compliance or truth certification. The
legacy raw assembly path remains explicitly unreviewed when no assessment ID is
supplied. No publishing or asset-generation approval is implied by this feature.

New endpoints:

- `GET /api/brands/{id}/authority`: versions, active state and source manifest.
- `POST /api/brands/{id}/authority/propose`: make a model-backed draft.
- `POST /api/brands/{id}/authority/drafts`: save an edited `AuthorityMap` as a draft.
- `POST /api/brands/{id}/authority/confirm`: `{ "version_id": "..." }`.
- `POST /api/brands/{id}/context/select`: `{ "retrieval_id": "..." }`.
- `POST /api/brands/{id}/context/assess`: `{ "retrieval_id": "...", "selection_id": "...", "include_section_ids": [] }`.
  Selection is optional for existing API clients; without it, assessment retains
  complete applicable governing documents and enforces the same input limit.
- Context assembly accepts an optional `assessment_id` in addition to retrieval ID
  and token budget. IDs and all associated documents must belong to the same brand.

`assess_evidence` is a standalone backend service. The future agent wrapper supports
`get_brand_context(..., reviewed=True)`; its default remains the legacy raw mode
for compatibility. The current UI always requires assessment for reviewed assembly.
Authority proposals and assessments use the existing configurable planner model,
tracked prompts/responses/usage and disabled automatic retries. Audit run IDs are
shown in the UI; inspect detailed costs with `python -m app.llm.cli show <run-id>`.
Tests use controlled source documents and mocked provider responses; no paid calls
are required for verification.

#### Recover a saved authority proposal

If a proposal was rejected because it copied presentation line numbers into
quotations, replay its saved response without paying for another model call:

```bash
.venv/bin/python -m app.brand.authority \
  --brand-id '<brand-id>' --from-run '<proposal-run-id>'
```

Replay accepts only completed authority-proposal runs for the same brand. It
revalidates the document manifest and all supplied governance lines. Only legacy
prompt prefixes such as `459: ` may be removed, and only when their numbers match
the claimed lines and the resulting quotation exactly matches the source. It
never fixes words, Markdown, omissions or line ranges. Adjustments are recorded
in draft notes, with the untouched model response retained in its original run.
Repeat replay returns the existing version instead of creating duplicates. It
never confirms a map or creates a model run.

Older requests lacked hashes for non-governance document contents. Their replay
revalidates the captured governance text and the complete ID/path/title manifest,
records that limitation in the notes, and pins the new draft to current document
hashes for human review. New proposals include document hashes and provide line
numbers and text in separate fields, preventing presentation prefixes from being
mistaken for source wording.

## Campaign planning (Milestone 1)

Open Campaigns, select an imported brand, and click New campaign. Send a brief,
answer any material questions, and revise the proposed plan through chat. Approve
plan confirms the current version; it does not generate assets. Conversations and
plan history persist in SQLite. A later message reopens planning and requires fresh
approval. Planning uses the configured retrieval planner model and tracked calls.
Complete brand retrieval setup and confirm a current source authority map in
Sources first; campaign planning requests reviewed brand context automatically.
Failed planning calls can be retried explicitly. Paid end-to-end planning has not
been validated by the automated tests. A server interruption during a request may
leave that campaign busy; create a new campaign in that case (recovery is pending).

### Simplified campaign evidence review

Campaign planning now uses retrieval plus applicable governing passages, followed by
one structured review of supported facts, restrictions, conflicts, and unresolved
questions. It does not run the legacy selection/atomic-statement pipeline; those
services remain available in Sources for inspection. The review references chunk
IDs, with original excerpts retained in its saved artifact. The backend checks
claimed topic/channel precedence against the confirmed authority map and downgrades
unsupported winners to unresolved. Interpretation and scope remain model judgments.
Unresolved claims are omitted or raised as essential questions in planning.
The input budget is 60,000 actual tokens, including the authority map; oversized
inputs fail explicitly without truncating governing restrictions. Budget and model
context limits are separate. No automatic model retries are performed. Live output
quality still requires validation with the supplied briefs.

### Approve and generate

Plans can be revised through chat (for example, “Actually create only three”).
The planning response schema contains only plan fields; chat summaries are assembled
by the application rather than letting the planner return draft posts as its reply.
Click **Approve & generate** on the latest plan to explicitly authorize draft writing.
The API pins the approved plan version, prevents concurrent edits/execution, and saves
one tracked result per asset. Drafts appear in chat as generation progresses. A failed
run exposes **Retry unfinished drafts**, which preserves completed results. This writes
local drafts only: it does not publish, schedule, or send them. Source and authority
freshness are checked before writing. A server restart during generation currently
requires recovery of the interrupted campaign state; durable job recovery is pending.

### Campaign plan evidence gate

Before saving a plan backed by retrieved sources, a separate tracked
`campaign_plan_check` call checks the goal, outlines and clarification questions
against both the review and original source excerpts. It checks statistic expiry
at the requested publication date and flags questions that assume a prohibited
claim is usable. A failing plan gets up to two tracked repair attempts, each followed by another
check. If it still fails, the rejected outline is replaced with an application-authored clarification and no new assets, rather than exposing the audit as an agent error or presenting the outline for approval.
The gate adds one model call to normal planning and up to four more when repair is needed;
all calls retain prompts, responses and usage in the process inspector. This is a
model-based check, not a deterministic guarantee of policy interpretation.


### Compact campaign contexts

New campaign plans compact all retrieved chunks into one topic context and one
context per requested channel. Repeated chunk IDs are deduplicated within each
group. Summaries retain source IDs and original evidence in saved artifacts.
This path does not require a confirmed authority map or resolve source conflicts.
It preserves differing statements with attribution. Each draft receives its
topic and matching channel context. Plan approval, source freshness, deliverable
validation, citation checks and draft format checks remain in place.
Existing saved plans retain their original review path. Sources review tools are
unchanged. Expand **Compact contexts** below retrieval questions to inspect output.


### Campaign auditors removed

Campaign planning, generation and revisions no longer invoke conflict resolution,
authority ranking, plan compliance audits, draft audits or audit-driven repairs,
including for existing saved campaigns. They use compact retrieved contexts.
Basic deliverable validation, source-ID validation, source freshness and explicit
approval before generation remain. Separate Sources inspection tools are unchanged.

## Campaign memory

Open **Memory for this brand** in Campaigns to add, edit or forget preferences.
Saved preferences persist in SQLite and inform planning, writing and targeted
revisions for the same brand. Current instructions and source restrictions take
priority. New chat messages are interpreted for durable editorial preferences;
one-off edits and factual claims should not be retained. Extraction uses a
tracked `campaign_memory` request and requires an OpenAI key. Existing chats are
not backfilled. Entries remain until changed or deleted (50 entries per brand,
500 characters each). Historical prompts and chat records remain after deletion.

Memory changes retain before/after values and source message/run references in the
`memory_events` table. Updates use atomic revision checks, and extraction receipts
prevent duplicate processing. The extractor cannot control campaign routing;
campaign-only edits never become lasting preferences. Lasting proposals require
an exact supporting quotation with durable intent. Origins/history are recorded
in storage but are not yet displayed in the panel. Extraction remains synchronous.
