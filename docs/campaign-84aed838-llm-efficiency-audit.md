# Campaign model-request efficiency audit

Campaign: `84aed838-0184-4a46-87fe-cb2f1f88e2b7`

Read-only audit of all 16 saved requests and responses returned by the local campaign runs API on 2026-11-07, with the saved campaign state and current repository implementation. No new model calls were made and no application behavior was changed. This covers intake through planning/verification; there are no draft-writing or image-selection calls in this campaign snapshot.

## Measured baseline

- Model: gpt-6-luna for every call.
- Input: 384,008 tokens; cache reads: 75,412 (19.64%); cache writes: 307,096.
- Output: 29,125 tokens, including provider-reported reasoning.
- Sum of request durations: 233.832 seconds. Calls do not overlap in this trace. This is model service time, not end-to-end user wait: the trace also includes user-answer pauses and local work.
- 10 of 16 calls report zero cache reads. Zero reads does not mean caching is disabled: most of these calls report cache writes.
- Four calls—conflict find/check, planning, verification—account for 299,180 input tokens (77.91%).

## Every call

| # | Operation | Seconds | Input | Cache read | Output | Input/output assessment |
|---|---|---:|---:|---:|---:|---|
| 1 | campaign_memory | 3.82 | 840 | 0 | 220 | Brief + memories → changes=[]; no lasting preference found. |
| 2 | campaign_agent_step | 1.86 | 2,662 | 1,299 | 30 | Duplicated brief in request/conversation → extract_requirements. |
| 3 | campaign_requirements | 49.56 | 1,889 | 1,234 | 6,763 | Brief → complete requirements, six assets and Vaskell permission question. |
| 4 | campaign_agent_step | 2.44 | 3,307 | 2,536 | 163 | Requirements tool result → retrieve_context. |
| 5 | campaign_agent_step | 3.39 | 3,736 | 3,181 | 268 | Retrieval result → asks Vaskell publication-permission question. |
| 6 | campaign_agent_step | 1.11 | 2,077 | 0 | 19 | Permission answer + state; tool list reduced to extraction only → extract_requirements. |
| 7 | campaign_requirements | 25.25 | 5,488 | 1,234 | 3,957 | Conversation + previous requirements → entire requirements object regenerated. |
| 8 | campaign_agent_step | 1.90 | 2,241 | 0 | 84 | Tool list reduced to retrieval only → retrieve_context. |
| 9 | plan_retrieval | 8.19 | 3,540 | 0 | 887 | Full requirements → topic/channel search plan. |
| 10 | campaign_conflict_find | 18.11 | 70,050 | 0 | 2,107 | 277 original passages + brief/requirements → initial FedRAMP conflict finding. |
| 11 | campaign_conflict_check | 23.20 | 70,748 | 65,928 | 2,876 | Same 277 passages + proposed finding → checked conflict and treatments. |
| 12 | campaign_decision_match | 2.72 | 19,591 | 0 | 151 | Finding + prior decision → FedRAMP is new; Vaskell permission is a different subject. |
| 13 | campaign_conflict_resolve | 14.52 | 24,261 | 0 | 2,187 | Finding + original passages + authority map → structured resolution analysis. |
| 14 | scope_campaign_plan | 18.00 | 15,196 | 0 | 2,020 | Catalog + task → selects 29 refs but full_review_required=true; retains all 264 passages. |
| 15 | campaign_plan | 34.74 | 82,729 | 0 | 4,378 | 264 original passages + campaign context → six-asset outline. |
| 16 | campaign_plan_verification | 25.03 | 75,653 | 0 | 3,015 | 251 original passages + outline → four defects; rejected because two cite a transport alias. |

## Highest-confidence context reductions

1. **Remove source snapshots from semantic prompts.** `brand/conflicts.py:reconcile_decisions` sends raw findings and decisions. `brand/conflict_resolver.py:resolve_question` sends the raw finding again. The finding contains `source_snapshot`: 277 UUID-to-SHA256 entries, 29,916 JSON characters. That is about 86% of the finding object, transmitted twice. Keep this map in storage and compute changes in Python; send only changed IDs and relevant scope/quotes. Retain the exact original source passages needed for resolution. The two whole requests currently consume 19,591 and 24,261 input tokens; removing their snapshots should remove most of this avoidable overhead. Exact field token savings require tokenizer measurement; character counts here are measured, not token estimates.

2. **Separate evidence-review scope from planning scope.** `brand/context_scope.py:select_scope` uses `full_review_required` to select the entire source pool even for `scope_campaign_plan`. The selector returned 29 refs but set the flag because the FedRAMP answer changed the main message; all 264 available passages were retained, with zero deferred. This cost 18.00 seconds and 15,196 input tokens before the 82,729-token plan call. Changing a central claim can justify conflict re-review without requiring every typography/style/example passage in a plan prompt. Use distinct review-invalidation and source-selection decisions. Retain required legal/authority passages, exceptions, dependencies, relevant product facts and channel planning constraints.

3. **Use stage-specific source subsets.** Conflict discovery receives 277 passages; planning receives 264; verification receives 251. The planner source packet alone is 247,656 JSON characters. Examples of retained content include typography hierarchy, voice/word lists, visual production rules, templates and archived examples. Some visual constraints affect planning and must stay, but detailed typography, export rules, sentence-level editing examples and unrelated archives should be deferred until drafting/image work. Select intact sections using explicit applicability and dependency closure, rather than blindly truncating text or dropping whole legal documents. The largest paths in the planner packet include brand guidelines (40,233 content characters), voice/tone (23,980), content style (18,289), and common visual rules (12,001). These are candidates for section-level classification, not unconditional removal.

4. **Reduce repeated requirements and output.** Initial extraction took 49.56 seconds with 6,763 output tokens (2,852 reasoning). Its assets occupy 9,969 serialized characters; the same campaign intent appears in topic_details, constraints and per-asset provenance. The permission follow-up resends 12,697 characters of previous requirements and regenerates the object, taking another 25.25 seconds. Store shared requirements once; let asset fields reference them; retain provenance through brief spans/IDs rather than repeating long quotations. For an answer to a saved structured question, apply an explicit field update and invalidate only affected downstream artifacts; use extraction for ambiguous/new instructions. Do not reduce the output cap to a value that truncates required structured output.

5. **Trim agent orchestration context.** The first agent request includes the approximately 2.5k-character brief twice: once in request and again in conversation. Its task instructions span 4,655 characters and include draft/image editing tools irrelevant to initial intake. Deduplicate the latest user message. For a known campaign continuation with exactly one valid next action, dispatch directly in code. The two resumed calls chose the only available tool and took 1.11 + 1.90 seconds. Five orchestration calls total 10.70 seconds; retain model routing where the action is ambiguous.

6. **Skip negative memory extraction when confidently campaign-only.** The first call used 840 input tokens and 3.82 seconds to return changes=[]. Strengthen the existing campaign-only guard, or run memory interpretation beside initial extraction and join before preference-dependent work. Preserve ordering if the message might modify preferences used by the campaign.

## Prompt caching recommendations

Existing infrastructure is present: `llm/prompt_cache.py` splits shared/changing messages and marks explicit breakpoints; the tracked client passes cache keys. Adding another key alone will not fix these misses.

- **Keep the successful find/check pattern.** These calls share instructions, schema and an identical first source message, then append different task directives. Check read 65,928 of 70,748 input tokens (93.19%). Keep find before check: check consumes the findings.
- **Fix agent tool-prefix churn.** The initial calls use the same 12-tool list and show hits. After clarification, one request exposes only extract_requirements and the next only retrieve_context; both have zero reads. Tool definitions precede context in the rendered prefix. Prefer direct dispatch when only one tool is valid. Otherwise evaluate a stable tool catalog with server-side prerequisite enforcement; do not weaken guards merely for cache hits.
- **Split reusable instructions/catalogs from changing tasks.** Requirements, retrieval planning, decision matching, resolution and source selection currently use a single JSON input without a marked reusable message boundary. Use a stable instruction/schema prefix, then original brief or stable source/catalog blocks, then follow-up decisions and the changing task. For source selection, the 39,172-character catalog can be a separate stable block. Keep snapshots out entirely.
- **Planning and verification are different cache families today.** They have different top-level instructions, schemas, source subsets and wrappers (`original_sources` versus `governing_rules`). Shared underlying documents do not produce an exact common prefix. Repeated plan requests and repeated verification requests can reuse their own stable source blocks. Cross-stage reuse needs an intentional common instruction prefix and canonical source block with stage directives after it, plus compatible schema handling; measure whether that refactor is worth its complexity. Do not add irrelevant sources to make the packets identical.
- **Control cache writes as well as reads.** The run writes 307,096 tokens and reads 75,412. The client does not set `prompt_cache_options`, so explicit markers coexist with default implicit behavior. Evaluate explicit-only mode after stable prefixes are established to avoid writing one-use changing suffixes. Cache write counts on both first and second requests must be measured.
- **Choose retention from observed reuse.** This campaign runs within minutes, so longer retention is not the first fix. Consider longer TTL only for brand context actually reused across campaigns throughout the day.
- **Do not pad the 840-token memory call just to raise its cache percentage.** Prefer removing it when unnecessary. A cold write can be expected; optimize useful reused tokens, total cost and latency together.

Official OpenAI documentation: reuse needs an exact rendered prefix, including compatible tools and settings; the current family requires at least 1,024 visible tokens. Explicit-only mode can avoid cache writes after the selected breakpoint. Longer retention carries a higher write rate. See [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).

## Where parallel requests help

| Opportunity | Dependency and implementation | Expected impact |
|---|---|---|
| Memory interpretation alongside initial requirements | Read from an immutable input snapshot; use separate sessions/clients; apply memory changes before downstream preference-dependent work. | Up to the measured 3.82 seconds if neither blocks the other. Skipping the unnecessary memory call is better here. |
| Scope/catalog preparation alongside conflict review | Build source inventories, applicability maps and static rule packs from retrieved evidence while conflict review runs. Model selection depending on final decisions must join/revalidate afterwards. | Can hide preparation latency. Hiding the full measured 18-second selection requires proving it does not depend on unresolved answers; that is not true for the existing selector. |
| Conflict batches within a pass | Already implemented with three workers, but this campaign fits one batch. Smaller semantic batches may fan out with all governing cross-cutting rules retained and a merge/check step. | Potential improvement to the 18.11-second find stage; needs coverage evaluation and rate-limit measurements. More batches also duplicate shared tokens. |
| Different conflict resolutions | Resolve unrelated finalized findings concurrently after decision matching, then merge updates serially. | Only one finding here, so no benefit in this campaign. |
| Per-channel outline planning | Once campaign decisions/shared strategy are fixed, generate channel outlines concurrently and merge with global asset IDs, timing and CTA dependencies. | Possible benefit to the 34.74-second planning stage; requires architecture changes and a global consistency pass. |
| Per-channel plan verification | Once the candidate is fixed, run global brief/decision review and channel checks concurrently, each with relevant original sources. Merge all findings before any repair/approval decision. | Possible benefit to the 25.03-second review, with smaller individual contexts. Global dependencies must remain checked. |
| Asset generation/validation | Already uses three workers in `api/campaigns.py:generate_drafts`; each asset is generated then validated/repaired inside its worker. | No additional benefit from merely adding a pool; no generation calls exist in this snapshot. |

**Keep these dependencies sequential:** requirements → requirements-based retrieval planning; find → check; check → semantic decision matching → required conflict resolution; resolved user answers → final plan; candidate plan → candidate verification; repair → confirmation. `parallel_tool_calls=False` is not the primary bottleneck and should not be flipped globally: most agent actions mutate prerequisite campaign state.

## Output correctness issue discovered during the audit

The final model response reports a missing “25 qualified demo requests” goal, a landing-page budget issue and two email-timing issues. The email findings cite `r88b9081767f1`, a transport alias, whereas validation requires immutable chunk IDs. The entire response is rejected as “Plan verifier returned an invalid defect target or reference.” The saved `plan_review` is incomplete with empty findings/history and no approval blockers; repair was skipped. Fix schema/reference handling and preserve valid known findings when another reference is invalid. This is particularly important before reducing review context or parallelizing checks. A provider status of completed does not imply the application accepted the result.

## Recommended implementation order and validation

1. Fix verification reference acceptance/normalization and preserve valid blockers.
2. Strip source_snapshot and search-only metadata from decision/resolution prompts; deduplicate brief fields.
3. Correct the full_review_required/planning-scope coupling, then implement stage-appropriate original-source selection.
4. Make requirement follow-ups concise; bypass agent calls with exactly one permissible action.
5. Establish stable per-operation prefix boundaries and measure explicit-only caching on repeated representative requests.
6. Parallelize channel/global verification; consider channel planning only if shared consistency and dependency evaluation remain reliable.

Replay saved payloads locally for size comparisons before spending model tokens. Evaluate changes against this campaign’s FedRAMP prohibition, exact approved replacement wording, SOC 2 qualification, Vaskell exclusion, all six deliverables, internal numeric target, and channel/CTA sequencing rules. Measure input tokens per field, output/reasoning tokens, read/write counts, accepted/rejected outputs and end-to-end critical-path latency. Compare cold and warm repetitions separately. Do not claim a specific total speedup from this single trace; the changes interact and need an end-to-end rerun.
