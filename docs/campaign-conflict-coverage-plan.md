# Campaign conflict coverage implementation plan

Status: implemented for new/revised plans. Existing ungenerated plans can opt in through **Review evidence**. Model recall still requires the opt-in live evaluation described below.

## Objective

Replace isolated per-query conflict detection with a campaign-wide, evidence-backed comparison of campaign decisions and applicable source rules. A clean result must demonstrate coverage and survive an independent challenge. Retrieval remains bounded; this improves detection within retrieved evidence rather than guaranteeing that the corpus contains no other relevant rule.

The motivating failures are the Leashgate product-page hero, scheduling a non-launch X post, and a product claim whose legal prohibition appeared in a different retrieval group. Implement general comparisons, not special cases for these documents or phrases.

## Current integration points

- `api/campaigns.py:retrieval_checkpoint` already saves deduplicated original evidence and query groups. Extend this boundary rather than building a second retrieval system.
- `brand/conflicts.py:prepare_conflict_intake` currently runs `detect_query_conflicts` independently for each query. Replace discovery here while retaining question persistence, answer reconciliation and usable resolution helpers.
- Both `retrieve_campaign_context` and `process_message` must call the same coordinator. Agent and direct routes must enforce identical readiness checks.
- `brand/evidence_ledger.py` retains historical chunks. Keep history inspectable, but distinguish current evidence from superseded versions before reviewing.
- Supplemental results currently bypass conflict discovery and flow into compaction. Route new evidence through review before it can support a plan.
- `brand/compact.py` should consume resolved review artifacts and preserve their decisions. Compaction must not become an implicit conflict resolver.

## Target flow

Retrieve → shared evidence pool → campaign decisions and source rules → applicability comparisons and source contradictions → independent coverage challenge → resolution/questions → compaction → candidate plan verification → approval.

### 1. Persist a versioned shared evidence pool

Create a `campaign_conflict_review` artifact using existing artifact storage. Reference the retrieval artifact, requirements revision, corpus fingerprint, current decision revision and review/prompt version.

Deduplicate by chunk ID plus content hash; preserve every originating query ID. Store original text, source identity, line range and hash. Flag historical evidence separately; two versions of the same passage must not silently become simultaneous current rules. Do not silently truncate input: batch with explicit token budgets and save each batch manifest. An unprocessed batch means incomplete review.

### 2. Extract campaign decisions and applicable source rules

Add closed schemas in a focused module such as `brand/conflict_coverage.py`:

- `CampaignDecision`: stable ID, action or claim, affected asset IDs, scope, publication date, exact original brief/message quotation and reference, explicit versus inferred status.
- `SourceRule`: stable ID, obligation/prohibition/claim, conditions, product/channel/audience/asset scope, effective dates, exceptions, original quotations and chunk/line references.
- `ScopedException`: original authorization, subject, scope, dates and the specific rule or decision it modifies. A status exception cannot implicitly authorize imagery or scheduling exceptions.

Extract decisions from the full original conversation and saved answers, using requirements as an index rather than a substitute for original wording. Extract rules from original passages. Include source assertions needed for source-to-source contradictions, not just imperative sentences. Validate quotation membership and citation ownership deterministically. Ambiguous scope stays explicit.

### 3. Build and review decision–rule comparisons

Give each comparison batch the complete decision inventory and shared rule catalog, then attach original passages for the selected rules. Match by scope across all query groups. Deterministic metadata may narrow clear exclusions; uncertain matches remain candidates. Do not rely solely on retrieval query labels or top-k matching.

Save `DecisionComparison` records containing decision ID, rule IDs, exact opposing/supporting quotations, applicability explanation, exception analysis and outcome: `compatible`, `conflict`, `uncertain` or `not_applicable`. Every material decision needs a disposition, including an explicit evidence-gap assessment when no rule matches. No rule match is not proof of permission.

Also compare source rules/assertions with overlapping scope across retrieval groups. Save source contradictions separately from user decisions so stale source wording can be identified without asking the user to resolve a legal rule that already has a clear authority winner.

Enforce unique IDs, complete batch receipts, valid citations and comparison references in code. A rule may be excluded as irrelevant with a reason; do not demand an all-pairs model comparison or manufacture conflicts from different dates/scopes.

### 4. Independently challenge coverage, including clean results

Run a distinct tracked challenge stage over original intent, the full rule/decision inventories, comparison results and original evidence. It must check for omitted campaign decisions, omitted relevant restrictions, incorrect applicability, unsupported compatibility, overbroad exceptions and missed source contradictions. The challenger must inspect raw evidence in bounded batches, including passages not selected by the first matcher; otherwise it inherits the same omissions.

Require structured findings or an explicit covered disposition for each assigned decision/evidence batch. Missing receipts, invalid citations, provider failures and token-budget overflow produce `incomplete`, never a clean review.

Allow one bounded correction cycle for extraction/comparison/coverage defects, then rerun the affected checks. Persist unresolved uncertainty as a question where a user can supply the missing decision; technical failures remain retryable errors. Do not convert provider failures into user preference questions or retry indefinitely.

### 5. Resolve and persist the campaign treatment

Reuse existing authority and answer reconciliation after conflicts are established. Resolve only where an applicable confirmed authority or valid scoped authorization establishes the treatment. Otherwise ask one concrete decision question with citations and actionable choices. User intent does not establish factual truth or waive legal restrictions.

Keep separate statuses for detected contradiction, authority resolution, user answer and remaining uncertainty. Previously answered questions reopen only when relevant evidence/intent changes or the answer is materially ambiguous. Record the reason and changed references.

Use aggregate states `incomplete`, `needs_decision` and `ready`. `ready` requires complete coverage, a passed challenge and no unresolved material issue. Report resolved source contradictions even when no user question is necessary.

### 6. Gate compaction and verify the proposed plan

Compact only after review is ready. Supply selected treatments and exact wording alongside original evidence references, retaining rejected alternatives for inspection rather than presenting them as equally usable guidance.

After planning and any deliverable repair, compare the final candidate against the reviewed decision inventory. Check asset coverage, dates and explicit exact-wording obligations deterministically where possible; use a scoped semantic check for imagery, scheduling treatment and other meaning-based constraints. Verify the saved final candidate, not only the initial model response. Permit one bounded correction followed by verification; otherwise withhold approval readiness and expose the specific unresolved problem.

This stage checks planning decisions; it does not add a general finished-copy audit or scheduling feature.

## Implementation sequence

1. **Artifacts and contracts:** add schemas, evidence normalization, stable IDs, snapshot keys, quotation/citation validation and readiness checks. No new database table is needed initially.
2. **Discovery coordinator:** implement extraction, cross-query applicability comparisons and source contradictions; replace query-isolated discovery in both routes. Keep existing question/answer interfaces.
3. **Challenge and lifecycle:** add coverage challenge, bounded correction, resumable checkpoints, cache invalidation and supplemental-evidence review. Include review version in cache keys; old empty results cannot satisfy the new gate.
4. **Planning integration:** pass resolved treatments into compaction and asset packages; verify final candidate before exposing approval. Store a review ID on each new plan.
5. **Inspector and rollout:** show counts of decisions compared, rules considered, unresolved/resolved issues, excluded scopes and incomplete batches in the process inspector. Link each finding to original evidence and tracked runs. Avoid a bare “No conflicts found”; use “No unresolved conflicts in reviewed evidence” with coverage details.

Preserve existing drafts and approved plan snapshots. Apply the new gate to newly created or revised plans; expose explicit re-review for existing unapproved plans instead of silently modifying campaign history or initiating paid calls. Leave source inspection tools unchanged. Remove obsolete query-only discovery only once both execution paths use the coordinator.

## Verification and acceptance

Add deterministic unit/integration tests with mocked provider outputs for orchestration, reference integrity and gates. These prove mechanics, not model detection quality.

Required cases:

- Product-page working-room hero versus applicable product-UI rule yields a cited conflict.
- Non-launch X post scheduled versus live-posting rule yields a cited conflict.
- Launch X post scheduled is compatible; role/problem-page working-room imagery is compatible.
- Explicit valid imagery exception is scoped correctly; GA-status exception does not waive imagery rules.
- Product claim and legal prohibition in separate retrieval groups are compared together.
- Compatible first-pass response is challenged and corrected when raw evidence contradicts it.
- Decision/rule omitted by extraction or matching is detectable by the raw-evidence challenge.
- Failed/missing review batches, malformed references and coverage failures cannot produce readiness.
- New supplemental evidence invalidates affected comparisons; unchanged answered issues stay settled.
- Source changes invalidate cached reviews; historical versions remain traceable without masquerading as current evidence.
- Both agent and direct routes, reload/resume and final plan repairs preserve the gate.
- Final hooks retain required wording; numeric limits, explicit dates and asset IDs remain enforced.

Create a separate opt-in live evaluation set with these positives, compatible controls and paraphrases. Use saved original passages and synthetic/minimized briefs; capture expected citations and outcomes. Score conflict recall, false positives, coverage omissions, cost and latency. Any live calls are explicitly invoked, never part of routine tests. Compare against the current baseline before enabling by default; mocked tests alone cannot establish that the model now catches these cases.

Run focused conflict/pipeline/agent/compaction/plan tests first, followed by backend lint and the frontend build if inspector UI changes. Expand verification when failures or changed shared behavior warrant it.

## Operational limits

- Bound concurrency and tokens per stage; record cost/latency per operation using existing tracked calls. Do not promise a fixed call count until evidence batching is measured.
- Resume completed artifact stages after failure; do not repeat paid work for an unchanged snapshot.
- A clean review is bounded by retrieved evidence and model judgment. Preserve an inspectable record of what was checked and what was excluded.
