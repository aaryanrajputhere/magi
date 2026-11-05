# Campaign review reliability: explanation and fix plan

Status: proposed. This document plans changes; it does not change runtime behavior or rerun campaigns.

## What the pipeline does now

1. Extract asset requirements from the original conversation.
2. Generate topic searches plus rules and voice searches for each channel.
3. Retrieve four initial hybrid-search hits per question, then add remaining sections of matched documents. Merge chunks into a shared pool.
4. Extract campaign decisions and source rules. The provider now selects quotation references; Python copies the original text and source/message identity.
5. Give the decision and rule inventories to one campaign-wide comparison call. The model still writes decision IDs, rule IDs and issue citations into ordinary string fields.
6. Validate comparison references; allow one correction for structural defects.
7. If the comparisons validate, independently challenge the original passages for missed restrictions, incorrect scope and omitted decisions.
8. Resolve issues or ask questions, compact context, propose a plan, and verify it before approval.

The quotation-reference fix secures the copied text. It does not establish that a selected line supports an extracted rule, or constrain all later references.

## What happened in campaign e765ef7c-ea64-489a-92c3-a6308a576330

The run extracted requirements, retrieved 336 unique chunks and produced 12 decisions and 217 rules in 11 sequential rule-extraction batches. The comparison call marked every decision compatible and returned no source conflicts. It also cited rule IDs that were absent from its catalog. One correction retained invalid IDs, so the turn failed before the independent challenge ran. No plan or drafts were saved.

For example, it cited `5:rule_landing_page_hero_image_type`; the actual entry was `6:rule_landing_page_hero_image_type`. It also linked Maren's draft-only LinkedIn instruction to an unrelated landing-page rule. Fixing the prefix alone would therefore leave an incorrect applicability judgment.

The X rules and voice queries both completed successfully as searches but neither retrieved the X playbook. They returned product and messaging passages. Search success was mistaken for sufficient channel coverage.

Some rules used heading-only quotations, such as `## Hero copy`. The reference was exact but did not substantiate the rule's detailed statement. The imagery extraction also summarized the broad choice of product UI or a working room without preserving all narrower page-type conditions in that rule. That can make an incompatible product-page treatment look compatible.

The campaign's stored trace still says conflict review is running, although the agent turn is failed and its lease is released. The failure is logged in the agent turn but not consistently propagated to the campaign and trace.

The recorded run used 19 model calls, approximately four minutes, and $0.199 in estimated cost. Extraction accounted for 11 calls before comparison and challenge work. These figures describe this run, not a general performance guarantee.

## Fix 1: Make all cross-stage references selectable

Extend the pattern used for quotation references to decision, rule, source, asset and comparison references.

- Assign canonical IDs in Python after extraction. Derive stable identities from the snapshot and original references; keep model-written labels as descriptions only.
- Supply short aliases mapped to canonical IDs, with closed provider schemas that permit only aliases in the current catalog.
- Require one comparison per decision through required schema entries. Preserve deterministic validation as a second check.
- Bind issue citations, source-conflict pairs, challenge links and plan-verification receipts to supplied catalogs too. Handle empty catalogs explicitly.
- Require corrections to preserve valid coverage. Do not discard invalid citations, guess a nearby ID or substitute the same name from another batch.

Integration: `brand/conflict_coverage.py` schema construction, validation and materialization. Keep canonical saved artifacts compatible with the inspector and existing question interfaces. Version contracts independently so reference changes do not unnecessarily invalidate unrelated extraction stages.

Acceptance: the invalid references from this campaign are impossible under the provider contract; unknown IDs are rejected in offline schema tests. All references remain traceable to original chunks and lines.

## Fix 2: Verify channel evidence before extracting hundreds of rules

Add a retrieval coverage manifest with a result for each requested channel and guidance category: `covered`, `not_found`, `ambiguous` or `failed`. Record the documents and passages establishing coverage, not merely the query label.

- Use the imported document inventory to discover channel guidance by explicit title, headings and metadata. Where useful, persist a configurable channel-to-guidance index. Do not hardcode Wickhold filenames or infer legal authority solely from a title/date.
- Search for the channel's guidance independently of campaign product wording, then retrieve relevant rules and their surrounding scope. A lengthy product announcement should not dominate the query intended to find an X playbook.
- If ordinary ranking misses an identifiable guide in the corpus, retrieve that document directly with provenance. Treat multiple plausible guides as an applicability/authority question rather than silently choosing the latest filename.
- If no guidance exists in the corpus, record that limitation. Distinguish optional missing guidance from an essential compliance gap; do not invent a conflict merely because no document was found, and do not claim complete policy coverage.
- Perform this check before rule extraction. Supplemental retrieval must update the manifest and review only affected evidence.

Integration: `brand/campaign_tracks.py:channel_queries`, `brand/context.py:execute_retrieval` and `expand_document_sections`, plus the retrieval checkpoint in `api/campaigns.py`.

Acceptance: a mocked ranking that misses an existing X playbook still discovers its live-posting rule through guidance discovery. A corpus without a guide yields an explicit coverage limitation. Successful product hits alone never establish channel-policy coverage.

## Fix 3: Require supporting passages and preserve narrower rules

Exact quotation membership and semantic support are separate checks.

- Replace the single supporting line with one or more selected evidence spans. Copy each span independently; never present disjoint text as one continuous quotation.
- Keep headings as context. A heading alone cannot establish detailed claim wording, numerical limits, permissions or exceptions.
- Extract independent obligations separately: broad allowed image types, the product-page requirement, role-page alternatives and any approved exceptions. Preserve page type, channel, audience, date and conditions as explicit scope fields with supporting references.
- Validate extracted assertions and their scopes against original passages before treating them as comparison-ready. Mark unsupported, overstated or underspecified records for bounded correction; retain their original responses for inspection.
- Detect contradictory rules across queries without flattening authority, chronology or scope. A status exception cannot authorize a visual or scheduling exception.

Integration: `Rule`, `Decision`, quotation contracts and persisted extraction artifacts in `brand/conflict_coverage.py`.

Acceptance: a rule about product-page imagery cannot be supported only by a section heading or a broad image-type allowance. Missing the narrower product-page condition is an extraction coverage defect caught before a clean decision comparison.

## Fix 4: Make compatibility an evidence-backed applicability judgment

- Split bundled campaign decisions when their subjects differ: publication date is separate from scheduled versus live posting; hero subject is separate from page CTA and layout.
- For each applicable decision–rule comparison, require an explicit explanation of shared scope, required behavior, requested behavior and any exception. A relevant-sounding rule is not enough.
- Keep unrelated rules out of the justification, but retain an inspectable exclusion reason. Maren's draft-only post must be checked against founder publication instructions, not landing-page targeting.
- Retain the independent challenge over original evidence, including passages omitted from the extraction. Its coverage must include narrower conditions and supporting-quotation adequacy.
- Structural failures produce an incomplete review with an actionable diagnostic. They must not be interpreted as no conflicts; nor should unsupported comparisons be passed to the challenger as valid conclusions.

Acceptance: the product-page working-room case and scheduled non-launch X case yield conflicts with relevant evidence. Valid page-specific exceptions and scheduled launch announcements remain compatible. Challenge tests must include a deliberately incorrect first-pass compatible result.

## Fix 5: Make failure states and recovery truthful

- Persist the exact failed stage, attempt, run ID, error category and completed checkpoint references before releasing the lease.
- Update campaign state and the running trace node to failed/incomplete in both agent and direct review entry points. Preserve completed work and historical plans.
- Resume the failed stage from compatible artifacts. Invalidate stages based on their actual dependencies: source content, requirements/answers, model/prompt/schema version and upstream artifact hashes. A comparison-schema change should not repeat all unchanged source extraction.
- Associate retry and repair calls with their parent stage in the inspector. Show the user what is complete, what failed and what a retry will repeat.
- Make review/retry claiming atomic so concurrent clicks cannot start duplicate paid work.

Integration: `campaign_agent.py:run_turn`, `api/campaigns.py:retrieve_campaign_context`, the explicit review endpoint, and the inspector/workspace components.

Acceptance: injected failures at extraction, comparison, challenge and verification leave no stale running state. Reload and retry preserve the brief and completed artifacts, block approval while incomplete, and do not start duplicate requests.

## Fix 6: Reduce work without weakening coverage

Address correctness first, then measure costs by stage.

- Keep the global pool deduplicated while recording every query's provenance. Current expansion uses a global seen set, so complete sections can be attached only to the earliest matching group; separate evidence ownership from group membership.
- Replace unconditional whole-document expansion with relevant sections plus governing scope, exceptions and referenced context. Do not drop restrictions to meet a budget. Use explicit context-gap results when more evidence is necessary.
- Classify historical and unrelated passages with recorded reasons instead of extracting every sentence as a current obligation. Authority decisions must remain evidence-backed.
- Run independent extraction batches with bounded concurrency and persist each completed batch separately. Keep database writes ordered and respect SQLite transaction boundaries.
- Reuse unchanged extractions across reviews. Keep the rule catalog global for cross-query comparison even when extraction and challenges are batched.
- Display extraction and challenge batch progress, token usage, latency and cost. Measure before setting latency or call-count targets.

Acceptance: trimming and batching changes retain all motivating restrictions and their scopes. Performance checks compare evidence coverage as well as cost; a faster clean result that omits a relevant guide is a failure.

## Implementation order and verification

1. Close comparison/reference schemas and fix error-state propagation. These directly address the current crash and misleading UI.
2. Add channel-guidance discovery and the retrieval coverage manifest before extraction.
3. Add multi-span support and scope validation, then strengthen decision decomposition and comparison/challenge contracts.
4. Implement dependency-based resume, atomic retry claiming and inspector diagnostics.
5. Optimize extraction volume/concurrency only after the correctness cases pass.

Use offline fixtures reproducing this run's malformed IDs, heading-only support, narrowed hero rules, missing X guide, unrelated rule linkage and stale running state. Include compatible controls and unrelated brands/documents so fixes are general. Exercise both agent and direct entry points and approval/execution gates. Verify schema membership, orchestration, persistence and retry behavior deterministically with mocked provider outputs; those tests do not establish live semantic recall.

The user's current preference is offline verification only. Do not send saved briefs or run paid model evaluations as part of this plan. Re-evaluation with a live model would require separate authorization. Preserve the failed campaign and its run history; do not rewrite its comparison output to look successful.
