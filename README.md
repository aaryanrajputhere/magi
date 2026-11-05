# Magi

Magi is a local brand-aware campaign workspace built for the [assessment brief](brief.pdf). A marketer supplies a brief in chat, adjusts a proposed plan, approves draft generation, and edits individual assets beside the conversation. React and FastAPI use SQLite for sources, campaigns, memory, and model-run evidence.

Supported assets are social posts (LinkedIn and X), blogs, landing pages, and emails. Outputs are local drafts; the app does not publish or schedule them.

## Run locally

Requires Docker Engine/Desktop with Docker Compose, the supplied brand package at `wickhold-candidate-package/candidate`, and network access for builds and provider calls. From the repository root, run:

```bash
docker compose up --build
```

A clean brand database with sources, chunks, metadata, and precomputed embeddings is bundled in `backend/seed/brand.db.gz`. Compose installs it into a persistent Docker volume on first startup; existing databases are never overwritten. It contains no campaigns, memory, original model prompts, or responses. The app can start and browse sources without an API key. To generate campaigns, set `OPENAI_API_KEY` in the environment or root `.env`; optional research uses `FIRECRAWL_API_KEY`. Never commit credentials.

Open http://localhost:5173. API documentation is at http://localhost:8000/docs. Both ports bind to localhost; override `FRONTEND_PORT` and `BACKEND_PORT` in `.env` if needed. Startup automatically imports the corpus, chunks documents, extracts source metadata, builds the keyword index, and prepares embeddings before starting the API. The bundled corpus reuses precomputed embeddings without paid preparation calls. Changed or new source content requires billable embedding calls and an OpenAI key. Preparation errors stop startup and remain visible in the terminal. Browsing existing data needs no API key; AI operations do.

Stop with `Ctrl+C` or `docker compose down`. Local state persists in the `magi-data` Docker volume. `docker compose down` retains it; `docker compose down -v` deletes it. Brand files are mounted read-only.

### Automatic brand preparation

The default source is `wickhold-candidate-package/candidate`, with `brand/`, `images/`, and `briefs/` subdirectories. Compose manages the data volume without requiring a host `data/` directory. The backend installs the seed only into an empty volume, then verifies and refreshes derived data before its health check succeeds and the frontend starts. Restart after changing source files to refresh them.

If a previously imported source disappears, startup stops rather than serving its retained stale content. Restore that source, or deliberately choose a fresh database after preserving any campaigns you need. Failed embedding batches can be retried by rerunning `docker compose up --build`; successfully saved batches are reused. The Docker build caches the tokenizer encoding; native development may download it on first use.

For manual development without Docker, run the equivalent preparation sequence after installing the backend:

```bash
.venv/bin/python -m app.brand.ingestion --brand-name "Wickhold"
# Substitute the brand_id printed above in each following command.
.venv/bin/python -m app.brand.chunking --brand-id '<brand-id>'
.venv/bin/python -m app.brand.source_metadata --brand-id '<brand-id>'
.venv/bin/python -m app.brand.retrieval index --brand-id '<brand-id>'
.venv/bin/python -m app.brand.embeddings --brand-id '<brand-id>'
```

The supplied package contains 83 brand Markdown documents, 133 image candidates, and three briefs. Briefs and images are separate campaign inputs, not imported as brand-document text. Policy-candidate extraction and confirmed authority maps are separate Sources inspection workflows; current campaign conflict coverage does not require a confirmed authority map.

### Use another brand

Outside Docker, set `MAGI_BRAND_SOURCE_DIR` to its source folder. In Docker, change the backend brand-source bind mount in `compose.yaml` to that folder; the container path remains `/app/brand-source`. Set `MAGI_DOCUMENT_DIRECTORY`, `MAGI_IMAGE_DIRECTORY`, and `MAGI_BRIEF_DIRECTORY` if its subdirectory names differ. They must be distinct, non-overlapping relative paths. Set `MAGI_BRAND_NAME` to the new display name. Remove or replace the bundled sample seed and use a fresh Docker volume for a different brand. The sample brand name in the command above is an import label, not application logic.

### Local storage

Docker uses a named volume owned by the image's default UID/GID `1000:1000`; no host directory or ownership repair is required. The existing host `data/magi.db` is not modified or automatically imported. Native development continues to use that host path. Keep the default Docker UID/GID unless you also arrange matching volume permissions.

The seed can be rebuilt from a prepared local database without exporting campaign history:

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/export_seed.py data/magi.db backend/seed/brand.db.gz
```

## Assessment walkthrough

1. Open **Sources**, select the imported brand, and inspect original documents, section chunks, and metadata. Use retrieval inspection to see which passages are found rather than assuming the entire folder enters context.
2. Open **Campaigns**, select the brand, and start a new campaign. Paste one supplied brief from the package's `briefs/` folder. Answer material clarification or conflict questions when requested.
3. Review the proposed deliverables. Adjust the plan in chat before approving, for example, “Create only three LinkedIn posts.” Click **Approve & generate** on the current plan to authorize writing.
4. Select a generated draft. Chat appears on the left and a format-specific asset preview on the right. Library imagery can be selected automatically or changed using the image controls.
5. Request a targeted edit in chat, for example, “Shorten the second post.” Check that the selected draft changes and the other drafts remain intact.
6. Inspect and edit saved preferences in the brand memory UI. Start a new campaign for the same brand and demonstrate a relevant preference carrying over.
7. Open the campaign activity/process inspector. Select an asset-generation step, inspect **Full request** and raw JSON for the exact logged model request, then inspect its response, tokens, duration, and estimated cost.

Generation saves completed drafts as workers finish. **Retry unfinished drafts** preserves completed results. A changed plan requires fresh approval. Do not treat an evidence review or an approved outline as approval to publish.

## How the system works

### Brand retrieval and contradictions

Ingestion retains original Markdown, relative paths, content hashes, and source provenance. Section-aware chunks preserve headings, line ranges, lists, and tables. SQLite keyword search and `text-embedding-3-small` vectors are combined through reciprocal-rank fusion. Campaign-specific search questions select evidence without pasting the complete corpus into every prompt.

Current campaign planning pools retrieved passages, extracts source rules and campaign decisions, compares scope and exceptions, and independently checks the result against original evidence. Material decisions require comparison coverage; invalid references or incomplete batches cannot establish readiness. Supplemental evidence also goes through review. Plan verification is tied to the saved outline. The process inspector exposes rules, decisions, comparisons, and coverage artifacts.

Topic and channel contexts compact the retrieved evidence for writing. Source references and original passages remain available in saved artifacts. Conflict handling covers retrieved evidence and uses model judgments; it cannot guarantee that every relevant rule was retrieved or interpreted correctly. Source authority-map and atomic-statement tools in **Sources** are separate inspection paths, not mandatory campaign setup.

### Images, research, and editing

Image selection uses the library catalog and metadata, brief restrictions, and relevant visual guidance. It may select no image when none fits. An image-selection failure can leave a text draft available for manual image attachment.

When configured, Firecrawl research uses generic industry queries rather than looking up the fictional company. External material is campaign-scoped and cannot establish brand facts. Research is bounded to two queries and three scraped pages per attempt, with local estimated-credit limits. Missing credentials or provider failures leave research skipped, partial, or failed rather than inventing evidence. Firecrawl credits are separate from OpenAI token costs.

Generation runs up to three asset pipelines concurrently. Chat revisions target an individual asset; prior draft bodies are retained in revision history. Model-based validation supplements format, reference, and freshness checks; it is not a guarantee that copy is ready to publish.

### Memory

Preferences persist per brand across campaigns in SQLite. Users can add, edit, and forget them. Current instructions and source restrictions take priority. A tracked extraction step looks for durable editorial intent supported by an exact message quotation; one-off edits and factual claims should not become lasting preferences.

Memory is limited to 50 entries per brand and 500 characters per entry. Stored memory events retain before/after values and originating references. Forgetting removes the active preference; it does not erase historical prompts or chats. Event history is stored but is not yet displayed in the memory panel.

### Models, prompts, and cost

Only OpenAI models are used for model inference. Current configured defaults are:

| Task | Model | Intended tradeoff |
| --- | --- | --- |
| Planning, chat orchestration, writing, memory, image selection | `gpt-4.1-mini` via `MAGI_RETRIEVAL_PLANNER_MODEL` | Shared structured-output model keeps configuration and per-step costs simple. |
| Conflict coverage, rule extraction, draft validation | `gpt-6-luna` via `MAGI_CONFLICT_REVIEW_MODEL` | Separate review setting allows evaluation independently of the writing model. |
| Document and query embeddings | `text-embedding-3-small`, 1,536 dimensions | Compact vectors support local semantic retrieval in SQLite. |

These are repository settings, not measured quality comparisons. Compose forwards both model settings from `.env`; recreate the backend after changing them. Verify model access with the supplied key before a live run.

Tracked requests retain prompts, responses, provider usage, timing, source/campaign/asset references, and cost estimates. Inspect them in the app or without making model calls:

```bash
docker compose exec backend python -m app.llm.cli list --limit 20
docker compose exec backend python -m app.llm.cli show '<run-id>'
```

Cost is estimated from recorded usage and supported pricing snapshots. An unrecognized model or incomplete usage yields **unknown**, not zero. In particular, the current text pricing registry covers `gpt-4.1-mini`, not the default conflict-review model; a campaign total must disclose unknown-cost steps.

Prompt caching places reusable messages before changing task data and uses brand/operation cache keys. A key does not guarantee a hit. Calculate aggregate cache hit rate as `sum(cached_input_tokens) / sum(input_tokens)` over runs with reported usage. Include preparation, retries, failed billable attempts, research, and edits separately when reporting spend. See [prompt caching and recovery notes](docs/prompt-caching.md).

SDK retries are disabled, but selected agent preparation operations have one application-level recovery attempt. Saved edits, approval, and generation are not automatically replayed by that recovery path. Successful review checkpoints can be reused after interruptions; durable recovery of every interrupted campaign state is not complete.

## Develop without Docker

Requires Python 3.12+ and Node.js 22+. Configure the root `.env` as above, then run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
.venv/bin/python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

For brand preparation and run inspection, replace `docker compose exec backend python` in the commands above with `.venv/bin/python` from the repository root. The frontend proxies `/api` to `http://127.0.0.1:8000`; override `API_PROXY_TARGET` in the frontend process environment if needed. Relative database paths resolve from the project root.

## Verification

```bash
.venv/bin/python -m pytest backend/tests
.venv/bin/python -m ruff check backend
npm --prefix frontend run build
docker compose config --quiet
```

These checks cover backend behavior and contracts, lint, frontend compilation, and Compose configuration. Mocked-provider tests do not prove live output quality or actual campaign spend. For offline conflict evaluation:

```bash
PYTHONPATH=backend .venv/bin/python backend/scripts/evaluate_conflict_coverage.py
```

The script's `--live` option makes paid model calls. No live assessment run or spend benchmark is claimed by this README.

## Submission status and limitations

The implementation includes ingestion and retrieval, campaign planning and approval, draft generation and previews, targeted edits, image selection, optional research, editable cross-campaign memory, and prompt/usage inspection. The walkthrough above identifies how to demonstrate each requirement.

The assessment also requires the following submission evidence, which is not present in this repository at the time of this documentation update:

- **DECISIONS.md (1–3 pages):** brand-layer and memory design rationale, conflict handling, omissions and reasons, a concrete case where an AI tool led the work wrong, and a two-week improvement plan.
- **COSTS.md:** measured tokens, cost, and cache hit rate for each of the three supplied briefs, plus a typical edit. Report unknown-cost steps and Firecrawl usage explicitly.
- **Screen recording:** one brief through clarification, plan adjustment, generation and editing; a new campaign showing retained memory; brand retrieval and an exact asset prompt; tokens and cost for the demonstrated run.

These require actual execution evidence and author experience; this README does not substitute invented measurements or examples. One-command startup includes corpus preparation through `docker compose up --build`, with the source package present and precomputed brand data. Campaign inference still requires a configured API key. Live quality across all supplied briefs and an unseen brief remains to be demonstrated. There is no authentication or deployment workflow; this is a localhost application. Existing tables are created additively with SQLAlchemy rather than managed by a versioned migration system.

## Repository layout

- `backend/app/api/`: HTTP routes and campaign lifecycle.
- `backend/app/brand/`: ingestion, chunking, retrieval, evidence and conflict review.
- `backend/app/campaign_agent.py`: tool-based chat orchestration.
- `backend/app/memory.py`, `backend/app/research.py`: preferences and external research.
- `backend/app/llm/`: tracked provider clients, prompt caching, and pricing.
- `backend/tests/`: backend tests with controlled fixtures/provider mocks.
- `frontend/src/`: React chat, previews, Sources, memory, and process inspector.
- `evaluation/`, `backend/scripts/`: retrieval fixtures and evaluation tooling.
- `data/`: generated local storage, ignored by Git.
- `wickhold-candidate-package/`: supplied sample materials.
