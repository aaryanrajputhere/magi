# Campaign token baseline

Campaign `6459de35-b2ab-4e6c-9ed9-fa29514c4d84`: Wickhold Q1 awareness push, containment-led, awareness rather than demand generation. Requested channel mix was delegated; three LinkedIn posts and one blog are recommendations.

## Capture and measurement

This is a frozen **partial execution baseline**, not a completed-campaign total. Captured directly from the inspector's `/api/campaigns/{id}/runs` and `/activity` endpoints. Activity state is `generating`. The two reads are sequential, not a transactional snapshot. All 26 saved requests and all available responses were inspected. No new model requests were triggered and no application behavior was changed.

- 21 completed, 2 timeout, 3 started without recorded response.
- Recorded input: **1,085,699 tokens**; output: **25,740**; total: **1,111,439**.
- Recorded estimated cost: **$0.146655865**; five requests have unknown usage/cost. This is a lower bound, not a billing reconciliation.
- Cached input: **16,554 tokens (1.52%)**. Cache-write tokens are not cache hits. Output includes **16,196 reasoning tokens (62.9%)**; visible text alone understates output usage.
- Inputs account for **97.7%** of recorded tokens. Prioritize context reduction before output brevity.

`runs.json` and `activity.json` preserve raw evidence. `summary.json` records capture timestamp, hashes and aggregate metrics. `requests.csv` and `request-analysis.json` map every request to usage, findings and opportunities. Character counts are serialization diagnostics, **not token counts**. No tokenizer-based field attribution or savings experiment was run.

## Biggest opportunities

1. **Planning, verification and repair: 443,658 input tokens (40.9% of measured input).** Verification and repair each embed 217 LinkedIn and 219 blog chunks with **208 shared chunks**, 228 unique total. One shared evidence dictionary removes 214,692 serialized characters of duplicate chunks per request before any semantic pruning. Planning already deduplicates evidence, but still carries a 63,304-character source catalog, 26,568-character hash snapshot, and 103,059 characters of per-asset packages. Remove model-irrelevant hashes and redundant metadata. Repair #20 sends 148,446 tokens to fix one unfinished blog-purpose sentence; a field patch can use candidate, defect and relevant dependencies instead of the full corpus.
2. **Four conflict passes: 250,721 input tokens (23.1%).** Two initial passes and two more after extra retrieval. Preserve independent challenge, but reuse the prior reviewed snapshot and review changed evidence plus affected claims/authority dependencies. The second review actually changes treatment of stale boilerplate, so blindly skipping it would lose meaningful behavior.
3. **Drafting: 155,187 measured input tokens (14.3%), plus one unknown request.** Completed LinkedIn drafts each receive ~77.6k input tokens. Shared/global rules and channel-specific rules should be extracted once, with exact quotations and source IDs, then combined with relevant claim support for the asset. Both attempted writing packets timed out, leaving `channel_rules_to_apply: null` and full-source fallback. This is a concrete propagation path for context inflation; timeout causation itself is unproven.
4. **Image selection: 88,399 measured input tokens (8.1%), plus one unknown request.** A 579-character post receives 241,117 characters of brand guidance and a 110-entry, 52,074-character image catalog. Use applicable visual/channel rules, explicit eligibility filtering and a ranked shortlist with expansion if no eligible match exists. Current broad path/keyword filtering retains the full 217-chunk packet here.
5. **Document selection and evidence-gap detection: 121,943 input tokens (11.2%).** Select governing documents from a smaller inventory; cache versioned assignments and extracted rules. Gap detection should consume a coverage index and focused evidence rather than reread 202,972 characters. Existing rule-pack artifact caching should be measured as cold versus warm behavior before adding another cache.

These groups partition the dominant calls, but proposed changes within them overlap. Do not add speculative savings estimates together. The directly evidenced first change is deduplicating shared verification/repair evidence without removing any unique source text.

## Request-by-request input and output review

Unknown usage is shown as “?” and remains excluded from measured totals.

| # | Operation | Input tokens | Output tokens | Status |
|---|---|---:|---:|---|
| 1 | `campaign_memory` | 483 | 896 | completed |
| 2 | `campaign_agent_step` | 1876 | 19 | completed |
| 3 | `campaign_requirements` | 1741 | 3001 | completed |
| 4 | `campaign_agent_step` | 3700 | 54 | completed |
| 5 | `plan_retrieval` | 4139 | 554 | completed |
| 6 | `campaign_rule_documents` | 63159 | 3341 | completed |
| 7 | `campaign_rule_limits` | 2845 | 167 | completed |
| 8 | `campaign_writing_rules` | ? | ? | timeout |
| 9 | `campaign_rule_limits` | 3530 | 334 | completed |
| 10 | `campaign_writing_rules` | ? | ? | timeout |
| 11 | `campaign_conflict_find` | 62036 | 1968 | completed |
| 12 | `campaign_conflict_check` | 62439 | 1527 | completed |
| 13 | `campaign_agent_step` | 5641 | 260 | completed |
| 14 | `campaign_evidence_gap` | 58784 | 395 | completed |
| 15 | `campaign_conflict_find` | 62755 | 2973 | completed |
| 16 | `campaign_conflict_check` | 63491 | 1562 | completed |
| 17 | `campaign_research_plan` | 1836 | 133 | completed |
| 18 | `campaign_plan` | 163933 | 2962 | completed |
| 19 | `campaign_plan_verification` | 131279 | 1407 | completed |
| 20 | `campaign_plan_coverage_repair` | 148446 | 1758 | completed |
| 21 | `generate_campaign_asset` | ? | ? | started |
| 22 | `generate_campaign_asset` | 77597 | 1116 | completed |
| 23 | `generate_campaign_asset` | 77590 | 797 | completed |
| 24 | `select_campaign_image` | ? | ? | started |
| 25 | `select_campaign_image` | 88399 | 516 | completed |
| 26 | `campaign_asset_validation` | ? | ? | started |

### 1. campaign_memory

Run `9b690dcb-0b32-46f6-9a7f-ef7c4d9724d2`. New brief plus existing memories → one campaign memory.

Small input; 775/896 output tokens are reasoning. Consider extracting memory with requirements, retaining explicit evidence.

### 2. campaign_agent_step

Run `c68a1785-076e-4f77-9da1-2df222f97f1d`. Agent context → extract_requirements(addition=false).

Deterministic initial transition can replace this routing call; retain agent handling for ambiguous revisions.

### 3. campaign_requirements

Run `0d5a593b-d25a-4a52-9e10-7ac33653929b`. Conversation and duplicated latest instruction → four recommended assets and expanded requirements.

Send latest message once; store shared constraints once. Preserve distinction between recommendations and explicit user requirements.

### 4. campaign_agent_step

Run `056666d7-61c5-4cd2-9ffc-87243c1b323b`. Growing agent history → retrieve_context(addition=false).

Deterministic transition after validated requirements; avoid replaying expanded requirements merely to choose the next tool.

### 5. plan_retrieval

Run `269620d0-f86b-4b8a-a8f1-4c58ad6b3c85`. Requirements serialized in both brief and requirements → retrieval plan.

Remove duplicate structured brief: 7,999 serialized characters repeat the 7,556-character requirements object.

### 6. campaign_rule_documents

Run `0ed822fd-ff19-405c-b06d-8bc0ba7d5054`. 83-document inventory plus authority → 15 assignments per channel packet.

Cache by corpus/authority version (code already supports artifact caching). Compact inventory openings/headings, retaining scope quotations and supersession. 245,585 characters are inventory.

### 7. campaign_rule_limits

Run `e28e65dd-7fdb-4abb-850f-ef1e132641db`. LinkedIn passages → explicit 180-word maximum.

Already focused. Reuse validated limit by document hash; preserve exact quote and count semantics.

### 8. campaign_writing_rules

Run `b2a168fc-281f-4f20-be72-129e94567d6c`. 217 evidence chunks → timeout, no output.

Split shared/global rules from channel rules; extract/cache in bounded batches. Unknown usage; do not count as zero.

### 9. campaign_rule_limits

Run `6ba32d91-cb26-40a0-8381-8d6d7179591e`. Blog passages → 900–1,400 body-word bounds.

Already focused. Cache validated limit, preserving caption/code counting semantics.

### 10. campaign_writing_rules

Run `02edb43a-b369-4c70-8be1-afb140dd1e1d`. 219 evidence chunks → timeout, no output.

Same compaction failure as #8. Share extraction of common 208 chunks rather than processing them twice.

### 11. campaign_conflict_find

Run `0e1169d2-d0ff-4751-8ad9-7bdf756a779f`. Brief, requirements and source passages → no conflicts, important limitations.

Retain broad initial coverage; cache stable source-rule analysis and strip transport metadata. Do not discard missing-boilerplate and report-claim qualifications.

### 12. campaign_conflict_check

Run `3a50613c-c9e0-49ce-8f0d-e9f731a08c25`. Same evidence plus proposed review → independently checked limitations.

Keep independent challenge; share evidence dictionary, concise rules and provenance. An independent reviewer still needs access to original evidence.

### 13. campaign_agent_step

Run `683b8c2a-ffff-4542-83dd-170902f1f09b`. Agent history → propose_plan with awareness-first instructions.

Small relative to corpus calls. Replace repetitive history with current state and unresolved decisions, or deterministic orchestration.

### 14. campaign_evidence_gap

Run `ee5c85d4-0934-4da2-9602-1569db574797`. 202,972 characters of evidence → two missing-evidence queries.

Use structured coverage index plus focused passages; missing boilerplate can be checked against source inventory before rereading the corpus.

### 15. campaign_conflict_find

Run `ec6e4cf0-76ea-4ee9-9f1a-69937445a976`. Updated evidence → stale boilerplate/naming finding.

After retrieval, review changed passages and dependency closure against prior findings. Keep full review when applicability or authority changes.

### 16. campaign_conflict_check

Run `1d025c40-b7d3-463e-be19-04b76995a4e1`. Updated evidence and finding → no user conflict; naming/claims limitations retained.

Delta challenge against prior reviewed snapshot. Preserve rejection of false user-choice conflicts and current approved naming/customer claims.

### 17. campaign_research_plan

Run `6d81bf5b-d816-4d67-82cb-53bf60c2f8e7`. Requirements only → no external research queries.

Small. Output incorrectly says industry/containment is unidentified despite prior source evidence. Pass compact verified company identity to improve decision quality.

### 18. campaign_plan

Run `e6831158-8886-4fba-9d0b-9597b3d7b960`. Brand context → four-asset plan.

163,933 input tokens. Keep one evidence dictionary; remove source hash snapshot and redundant catalog, shared per-asset ID lists/context. Use successful compact writing packets with original-source lookup.

### 19. campaign_plan_verification

Run `8f224643-8a00-413d-9a02-25294e14a669`. Plan plus two full rule packs → one unfinished-sentence defect.

Deduplicate 208 shared chunks across channel packs first. Preserve all-asset verification, current claims, authority and source access.

### 20. campaign_plan_coverage_repair

Run `f3b07697-01fd-467f-8bb6-fa486d491278`. Whole candidate plus full packs and review → repaired plan.

148,446 input tokens to finish blog purpose ending “and state the product's.” Return validated field patch with focused context, preserve other fields in code; recheck changed field and dependencies.

### 21. generate_campaign_asset

Run `7bf53cd2-8b5c-4996-920d-c3abe2b98a43`. Full LinkedIn source context → no recorded response (started).

Same task-specific packet reduction as #22–23; usage and completion unknown.

### 22. generate_campaign_asset

Run `62f0934e-2e36-426d-96b4-7b66b3b616c5`. 217 chunks plus rule-pack metadata → short LinkedIn draft, four citations.

77,597 input tokens; channel_rules_to_apply is null after timeout fallback. Supply compact global + LinkedIn rules + claim support; preserve citation resolution and independent validation.

### 23. generate_campaign_asset

Run `1d5b3a66-5ddd-4a68-b6ea-c464b00ac619`. Nearly identical 217-chunk context → another LinkedIn draft.

77,590 input tokens. Share stable prefix across sibling assets; focus each asset on its angle/support rather than all source text. Do not remove needed qualifications.

### 24. select_campaign_image

Run `9555f6d7-e0f4-4d76-bddb-5307a0cebadd`. Full brand guidance and 110-image catalog → no recorded response (started).

Restrict to visual/channel requirements, post and eligible catalog shortlist; usage unknown.

### 25. select_campaign_image

Run `918bf24b-b342-4353-86a5-d38cdbc47e03`. 241,117-character brand guidance and 52,074-character catalog → hero-contained-tray selection.

88,399 input tokens for 516 output tokens. Filter licensed/current/eligible images in code; send relevant visual rules and shortlist, with fallback expansion and grounded alt text.

### 26. campaign_asset_validation

Run `ff8b4fed-07b4-4b6e-bccd-a615a466a8cb`. 217-chunk rule pack plus conversation → no recorded response (started).

Deduplicate and batch shared rules; validate length/required fields in code and semantic claims against applicable original passages. No outcome or usage to assess.

## Implementation locations and evaluation

- `backend/app/brand/rule_pack.py`: full document inventory, per-channel rule extraction, shared rules, artifact cache, timeout fallback.
- `backend/app/llm/prompt_payloads.py`: `lean_plan_context` and `image_guidance`; remove model-irrelevant metadata and centralize shared evidence. Current working tree contains existing edits; local source is explanatory and is not proof of the exact code revision that produced these traces.
- `backend/app/brand/plan_verification.py`: full rule packs in verification/repair; switch to shared evidence and bounded patches with schema validation.
- `backend/app/api/campaigns.py`: drafting payload, gap/planning orchestration and image selection.

Start with lossless duplicate removal and metadata cleanup, then bounded repair and reliable cached rule extraction, then task-specific retrieval/delta reviews. Preserve original documents and provenance outside prompts. Compact rule packets must retain exceptions, authority, dates, qualifiers and source lookup; truncating arbitrary tokens would defeat the review guarantees.

For each experiment, snapshot the same brief, corpus and authority version, model/configuration, cache state and user decisions. Compare full completed runs with full completed runs, and this partial run only against matching stages/assets. Separate cold artifact preparation, warm reuse, provider cache hits, known usage, unknown requests, retries and failures. Capture per-request actual input/output/reasoning/cached tokens, cost and wall time. Sum of request durations is not wall time when calls overlap.

Quality gates: same four recommended assets and awareness-first intent; recommendations remain labeled; Q1 2027 remains an inference; current Wickhold naming and approved claim qualifications; stale boilerplate handled without false user-choice conflicts; LinkedIn ≤180 words; blog 900–1,400 body words with correct exclusions; valid citations; compliant imagery and alt text; no unfinished plan fields. Check that the final blog and all four assets actually complete—this snapshot cannot establish that. Review multiple runs for nondeterminism before accepting semantic pruning or reduced reasoning.

Prompt caching is a secondary cost/latency optimization: stable, identical shared prefixes may improve the low hit rate, but cache keys alone do not establish reuse and cached input still counts as input tokens. Keep it separate from actual token reduction. Existing cache keys on drafting did not produce consistent hits in these two sibling requests (5,927 versus zero).
