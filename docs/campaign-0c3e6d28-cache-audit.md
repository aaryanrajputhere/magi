# Campaign cache audit: 0c3e6d28-7c62-4d4e-8bf9-56d3c5634fc6

Analysis date: 2026-11-07. Read-only analysis of all 33 saved campaign runs from the local API, plus their complete saved request bodies and current source. No model calls, campaign mutations, or runtime code changes.

## Measured result

- 32 generation/review requests with reported cache usage: 644,134 input tokens; 100,398 cached reads; **15.59%** hit rate.
- One embedding request: 6,762 tokens, excluded from cache denominator.
- 476,940 cache-write tokens. Writes are not hits. Total estimated campaign cost: $0.096738320, including outputs and embedding.
- Campaign span: 456 seconds. TTL expiration is not a credible explanation for the within-campaign misses; all model calls used gpt-6-luna.
- Only 11 of the 32 measured requests recorded any cached reads. Token-weighted percentage is the relevant inspector metric.

## What is stopping reuse

### 1. Missing validation boundaries: highest-confidence fix

`backend/app/brand/draft_validation.py:116–156` calls cached_prompt to split rule_pack/source_spans/brand_preferences from changing draft data, but never sets cache_prefix_messages. The client therefore sends ordinary messages with implicit caching rather than marking the evidence boundary. The helper name cached_prompt only splits JSON; it does not enable breakpoints. Repair calls at line 224 have the same omission.

Nine validation requests consume 187,700 input tokens (29.14% of measured input), with only 16,452 reads (8.77%). Four rechecks have byte-identical first evidence messages AND identical output schemas to their earlier checks (18→22, 17→23, 27→29→31). They still rewrite most of their input. Add an explicit boundary after the stable first evidence message, leaving changing draft/measurements/findings after it. First checks remain cold unless a compatible earlier packet exists.

Offline cl100k_base estimates put the reusable evidence messages at 12,727 / 13,290 / 20,230 tokens for those three packets. Including their unchanged instructions/schema, the four rechecks could add roughly 67,600 reads, moving this historical denominator toward **26.1%**. This is a sizing estimate, not a provider-measured replay; provider tokenization and framing differ.

### 2. Cross-stage evidence is identical in substance but incompatible as a prefix

Conflict discovery/check, planner, plan verification and asset context router use different request-level instructions, output schemas, cache keys, JSON wrappers and passage IDs. A cache reads an identical prefix, not an unordered collection of familiar passages. Matching evidence deep inside different prompts cannot be reused.

Planner and plan verifier contain exactly the same **229 distinct passage contents** (38,235 content-only cl100k_base tokens). The router contains all 229 too. Conflict discovery, planner, verifier and router share **207 identical passage contents** (34,889 content-only tokens). A candidate canonical representation using chunk_id/content/relative_path/heading_path is about **51,176 tokens** including metadata. This is real existing overlap, not a proposal to add the entire corpus.

Implement a common evidence prefix with stable source IDs/order/serialization, a common cache accounting scope, and common request-level safety instructions. Put stage instructions after the evidence breakpoint. Different top-level output schemas also affect the rendered prefix: use a validated stable schema or supported appended format updates after the shared boundary. Changing only JSON sorting or cache keys will not fix this. Preserve selected source membership, authority rules, citation checks and task semantics; evaluate any schema/message-role changes.

Three downstream reads of that approximately 51K common block represent about 153.5K extra reads. Combined with validation boundaries, this is approximately **49.9% of the existing denominator**. Rewriting requests changes actual token totals; the calculation demonstrates the size of the opportunity, not a guaranteed final rate. The first common block still must be written once. The within-conflict second pass already hits and is not counted as an additional opportunity.

### 3. Validation schemas vary by asset

check() constructs Literal[tuple(spans)] in source_span. All five asset packets therefore have different schemas, with serialized schemas estimated at 2,731–4,534 tokens. That prevents a common validator prefix across assets even if source intersections are extracted. Use a fixed source_span string and retain Python membership validation (`spans.get`, matching rule_chunk_id and original quote checks), or evaluate appended schema updates. This is separate from the missing boundary: rechecks already have the same schema and still miss.

### 4. Drafting already shares its available brand/channel prefix

Five writer requests consume 51,285 input tokens and read 12,814 (24.99%). Three LinkedIn calls share 3,581-token prefixes; blog and landing-page calls reuse 2,826-token brand prefixes. Unique selected sources and task data follow. The first three calls start within 70ms, so a cold request may be processed/written in a different order from its logged start. Serialization could eliminate some cold overlap but adds latency; this trace already has just one cold writer among that trio. Even recovering its 3,581-token prefix adds only 0.56 percentage points overall. Further improvement requires factoring genuinely shared product/evidence passages before asset-specific fields, with no extra irrelevant sources.

### 5. One-off calls and changing suffixes create a real ceiling

Requirements, scope, coverage repair and image selection run once each. Their first prefixes have no earlier identical within-campaign request. Briefs, plans, selected evidence, drafts, hashes, measurements and findings are changing data. Focused repairs deliberately use small different evidence subsets. Do not add irrelevant source material, repeat calls or lengthen prompts solely to improve the hit-rate metric. Memory is only 483 tokens. Retrieval-planner marked instructions are below the cacheable minimum; its 2,749-token total does not make that short boundary eligible.

## Target assessment

All arithmetic below holds the historical 644,134-token denominator fixed for comparison; actual optimized inputs and repair counts will change.

| Target | Reads needed | Additional reads over observed | Assessment |
|---|---:|---:|---|
| 50% | 322,067 | 221,669 | Plausible after validation boundaries AND cross-stage canonical evidence; sizing model reaches ~49.9%, not measured. |
| 60% | 386,481 | 286,083 | Requires ~64.4K more than 50%: more compatible asset evidence, richer shared subsets, reduced unique overhead, or warm cross-campaign prefixes. Not established by this run. |
| 70% | 450,894 | 350,496 | Requires ~128.8K more than 50%. Aggressive for a cold campaign; likely needs substantial warm cross-campaign reuse and/or less unique input. No evidence here supports a guarantee. |

TTL increases will not fix a 7.6-minute run. Longer retention can help real cross-campaign reuse over longer gaps, with higher writes; choose it only after measuring actual reuse frequency. Keep keys compatible within intended reuse families; a shared key alone does not make different rendered prefixes match.

## All requests in recorded start order

Input, read and write columns are provider-reported. IDs are included for inspector lookup.

| # | Operation / asset | Input | Cache read | Cache write | Hit rate | Run ID | Diagnosis / opportunity |
|---|---|---:|---:|---:|---:|---|---|
| 1 | campaign_memory | 483 | 0 | 0 | 0.00% | 88a7d6c9-43eb-4205-95a7-8ba3010f5ffd | 483 tokens; below cache minimum. Leave small. |
| 2 | campaign_agent_step | 1,744 | 0 | 1618 | 0.00% | 96fde4a4-f2d5-40b3-8204-acad19072484 | Cold agent prefix; subsequent steps reuse it. |
| 3 | campaign_requirements | 1,567 | 0 | 1524 | 0.00% | 445ecb41-e863-46da-8f8d-f8f38b400e87 | One-off conversation prefix. Cross-campaign reuse limited by brief-specific content. |
| 4 | campaign_agent_step | 2,037 | 1618 | 293 | 79.43% | 0521862b-7e98-4901-8c1b-5dfb06dce165 | Append-only history works: reuses request 2. |
| 5 | plan_retrieval | 2,749 | 0 | 0 | 0.00% | b3ba90c8-88bb-4468-a58f-1c0a3eb972d8 | Explicit boundary covers only developer instructions; this prefix is too short (0 writes). Most input is unique extracted requirements. |
| 6 | campaign_conflict_find | 69,987 | 0 | 67603 | 0.00% | f0c40a5b-f2b2-41ce-8a6c-b8a9b14b777e | Cold 67,603-token evidence prefix. Reused by request 7; canonical evidence could also serve downstream stages. |
| 7 | campaign_conflict_check | 70,189 | 67603 | 0 | 96.32% | 9b599f92-18b4-4992-ba76-7b9451731b64 | Healthy reuse of request 6: 96.32%. Preserve this pattern. |
| 8 | campaign_agent_step | 2,117 | 1911 | 80 | 90.27% | 841c43cb-fe7d-4b87-ba2c-b1688d78395b | Healthy growing agent history reuse: 90.27%. |
| 9 | scope_campaign_plan | 13,705 | 0 | 11362 | 0.00% | bb6ac1e4-8619-4b4f-8517-91ee4624c4e2 | Cold source catalog. Reusable across campaigns with same selection/version, but only one scope call here. |
| 10 | campaign_plan | 69,857 | 0 | 60474 | 0.00% | cffe87e2-2ab5-428b-a80a-2485e2471f72 | Cold source dictionary. Same 229 passage contents as verification, different layout/instructions/schema/key. |
| 11 | campaign_plan_verification | 68,199 | 0 | 66186 | 0.00% | 8caa2a62-90d8-4225-9f26-6e3682b66f07 | Cold governing-rules structure. Repeats all 229 planner passages but cannot match its prefix. |
| 12 | campaign_plan_coverage_repair | 9,311 | 0 | 4489 | 0.00% | cdb3d66e-46a7-46d2-add2-0fcc19c59ca0 | Focused repair uses a different governing-rule subset and schema; one call. Keep focused. |
| 13 | route_asset_context | 60,729 | 0 | 59214 | 0.00% | 30a02b40-5097-4c9f-913d-a4856794cc63 | Cold source list with local sN IDs; includes all 229 planner passages. Normalize shared evidence, task instructions and schema placement. |
| 14 | generate_campaign_asset / q1-awareness-linkedin-03 | 9,238 | 3581 | 0 | 38.76% | 18817f89-79d9-42c0-9133-0919c433e6d9 | LinkedIn writer; 3,581 cached tokens. Shared brand/channel prefix works; asset sources and task remain uncached. |
| 15 | generate_campaign_asset / q1-awareness-linkedin-01 | 9,611 | 3581 | 0 | 37.26% | ff89b254-cdd4-49c3-b13f-b297c401d944 | LinkedIn writer; same 3,581-token hit as request 14. |
| 16 | generate_campaign_asset / q1-awareness-linkedin-02 | 9,667 | 0 | 3581 | 0.00% | cfc053b1-4fd9-4c19-91bd-b087f74f5406 | LinkedIn writer; writes the common 3,581-token prefix. Concurrent starts mean request start order is not cache-write order. |
| 17 | campaign_asset_validation / q1-awareness-linkedin-03 | 16,984 | 0 | 16981 | 0.00% | 1720abb8-d03a-4945-91ae-92b2dea2089a | LinkedIn 03 initial review. No explicit evidence boundary; asset-specific source-span enum/schema. |
| 18 | campaign_asset_validation / q1-awareness-linkedin-01 | 17,604 | 0 | 17601 | 0.00% | 3e54a059-5f01-4cc5-802d-106b99e0d0ea | LinkedIn 01 initial review. No explicit evidence boundary; asset-specific source-span enum/schema. |
| 19 | campaign_asset_validation / q1-awareness-linkedin-02 | 16,846 | 0 | 16843 | 0.00% | 908e0e0e-e677-4666-bd05-585fc7c0f79d | LinkedIn 02 initial review. Different evidence packet/schema; no initial reuse. |
| 20 | campaign_asset_repair / q1-awareness-linkedin-01 | 2,926 | 0 | 2923 | 0.00% | 49058b15-747e-4043-baca-d6e5e4cfec88 | LinkedIn 01 focused repair. Changing evidence subset and draft; implicit caching writes nearly the entire input. |
| 21 | campaign_asset_repair / q1-awareness-linkedin-03 | 4,188 | 0 | 4185 | 0.00% | 7b8b2f69-5c64-4e74-be2b-6dc24911d8f8 | LinkedIn 03 focused repair. Different focused evidence from request 20; no reuse. |
| 22 | campaign_asset_validation / q1-awareness-linkedin-01 | 17,612 | 3398 | 14211 | 19.29% | a0e58cf4-7551-404c-ad18-a907d40ec7a8 | LinkedIn 01 recheck. First evidence message and schema exactly equal request 18, but only 3,398 tokens reused. Missing explicit boundary loses most reusable evidence. |
| 23 | campaign_asset_validation / q1-awareness-linkedin-03 | 16,954 | 3298 | 13653 | 19.45% | 17712a45-7769-4fc0-9dde-07653ad9249a | LinkedIn 03 recheck. First evidence message and schema exactly equal request 17, but only 3,298 reused. Same boundary issue. |
| 24 | generate_campaign_asset / q1-awareness-blog-01 | 12,919 | 2826 | 1761 | 21.87% | 02cd9a47-d3f2-4257-a82a-290ca9942c77 | Landing-page writer. Reuses 2,826-token brand prefix; writes distinct channel prefix. |
| 25 | generate_campaign_asset / q1-awareness-landing-01 | 9,850 | 2826 | 1766 | 28.69% | 41663b9f-4628-4e9d-9bac-2910fb7cf593 | Blog writer. Reuses 2,826-token brand prefix; writes distinct channel prefix. |
| 26 | campaign_asset_validation / q1-awareness-landing-01 | 20,224 | 0 | 20221 | 0.00% | cb514db3-258e-4351-bc8a-a031e3d35043 | Landing-page initial review. Different evidence packet/schema, no read; mark stable evidence for future rechecks. |
| 27 | campaign_asset_validation / q1-awareness-blog-01 | 27,154 | 0 | 27151 | 0.00% | 8ede50c6-6f67-4883-ad1c-b7777abdc3d3 | Blog initial review. 27,154 input, no read; implicit cache writes almost everything, including changing draft. |
| 28 | campaign_asset_repair / q1-awareness-blog-01 | 5,300 | 0 | 5297 | 0.00% | 737a86d1-b3e1-437b-a833-749ab4d899ab | Blog focused repair. New evidence subset; no read. Put unchanged repair instructions before evidence and mark when long enough. |
| 29 | campaign_asset_validation / q1-awareness-blog-01 | 27,162 | 4878 | 22281 | 17.96% | 03a24adc-3484-41af-9150-bc1ba6cd78b0 | Blog recheck. First evidence message/schema exactly equal request 27, but only 4,878 reused; 22,281 rewritten. |
| 30 | campaign_asset_repair / q1-awareness-blog-01 | 3,629 | 0 | 3626 | 0.00% | a74d57c2-9a78-4ca7-b49c-ebd856e628c1 | Blog second focused repair. Different evidence subset; no read. Do not inflate packet to chase reuse. |
| 31 | campaign_asset_validation / q1-awareness-blog-01 | 27,160 | 4878 | 22279 | 17.96% | a727bddb-862a-4d3d-9f49-fefec9ef7aa0 | Blog second recheck. Same evidence/schema as requests 27/29; still only 4,878 reused and 22,279 rewritten. |
| 32 | embed_image_queries | 6,762 | — | — | Excluded | 20a414b4-0c53-4d0d-b794-cc498ad0d22c | Embedding request: no provider prompt-cache counter. Correctly excluded from inspector hit-rate denominator; exact-input application vector cache is a separate mechanism. |
| 33 | select_campaign_images | 16,432 | 0 | 9737 | 0.00% | 10bb5629-2867-457b-8a51-8e6cabbf4416 | Image selection: cold 9,737-token visual-rule prefix. Single call here; stable selected visual rules can reuse across campaigns. |

## Implementation order and verification

1. Mark validation and repair stable boundaries; fingerprint them in inspector metadata. Verify same-asset rechecks reuse the full evidence boundary. Keep scoped evidence and Python grounding checks.
2. Stabilize validator schema, preserve source-span membership checks, then factor exact shared evidence across assets.
3. Normalize the common evidence representation across conflict/planning/verifier/router; move task-specific instructions and schema changes after the reusable boundary using supported mechanisms. Retain separate first/second conflict decisions.
4. Measure a full cold campaign and a naturally warm second campaign separately. Report cache reads, writes, uncached tokens, total tokens, cost, latency and quality/repair counts. A quality improvement that removes rechecks may reduce hit percentage while saving money.
5. Evaluate cross-campaign TTL only after same-prefix reuse over time is measured. No prewarming or additional paid runs were performed for this audit.

## Provider rules used

[Official OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching): identical rendered prefixes, explicit stable boundaries, 1,024-token minimum for GPT-5.6+, default 30-minute lifetime, schema/instruction effects, supported appended format updates and key accounting. Provider rules explain opportunities; logged usage establishes this campaign’s measured results.
