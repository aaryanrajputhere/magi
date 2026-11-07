# Three campaign runs: evidence audit

Audited November 7, 2026. The three briefs, all 91 recorded provider requests (89 text requests and two embedding requests), request bodies, responses, saved plans, drafts, questions and activity were exported and inspected. No campaign was rerun, edited or approved. Application source was read to explain the observed behavior; the workspace already contains substantial changes, so current code is not treated as proof of the exact deployed version during every historical request.

The verdict: evidence and claim handling are substantially more useful than a naive writer, but context management, repair efficiency and recovery still fall short of Yathaarth's request. Moving the stable passages first helped some operations. It did not fix the full campaign. The biggest opportunity is to prevent unnecessary context and repeat work, rather than optimize the short orchestration calls.

## Measurements and limits

| Campaign / brief | Requests | Reported text input | Output, including reasoning | Weighted cache reads | Sum of request durations | Observed first-request-to-last-completion span | Estimated recorded subtotal | Outcome |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `688e97e7…` / regulated teams, brief 3 | 39 | 740,393 | 82,705 | 15.55% | 782.7 s | 581.1 s | $0.12079 | Six drafts, all publication-blocked |
| `67bac97f…` / awareness, brief 2 | 32 | 554,936 | 52,248 | 19.31% | 541.7 s | 604.8 s | $0.08325 | Four drafts, all publication-blocked |
| `af3bdcc1…` / Leashgate GA, brief 1 | 20 | 618,270 known | 44,301 known | 3.39% of known input | 497.4 s | 417.8 s | $0.09701 known | Planning timeout; no saved plan or drafts |

Cache rate is sum(reported cached input)/sum(reported text input), excluding embeddings and missing usage. Embedding inputs add 8,097 and 5,537 tokens to the first two campaigns respectively. Failed Leashgate planning has no reported usage or cost: its totals are lower bounds, not a zero-cost failure. Dollar values are the application's recorded/display estimates, not independently reconciled invoices; external research fees are outside this subtotal.

Summed durations include concurrency; observed spans include user waits and gaps and are not pure service latency. They must not be used interchangeably. The Leashgate campaign is incomplete, and the campaigns have different briefs and asset counts. These are diagnostic comparisons, not a controlled model or before/after benchmark.

Provider-reported reasoning tokens were 64,200, 40,312 and 29,321: respectively 77.6%, 77.2% and 66.2% of output tokens. Output-token volume is mostly not delivered copy. This makes call count and reasoning overhead important alongside input size.

## Regulated teams: good safety judgment, weak execution efficiency

The original brief asks for two dangerous things: a headline claiming completed FedRAMP authorization and externally using Vaskell Health's unapproved story and quote. The agent uses the narrower approved FedRAMP wording, preserves SOC 2 wording, identifies Vaskell's clearance problem, and asks a useful proof-choice question. The user chooses an approved replacement. Saved drafts use Ostler Grocers with its actual measure, duration and scale, rather than borrowing Vaskell's audit-preparation outcome. That is the strongest quality result in this audit.

The agent proposes six assets: landing page, email, blog and three LinkedIn posts. This is within the delegated channel mix, though arguably generous for a focused push. It correctly skips external research. Plan verification catches an implied audit-preparation benefit from the Ostler result, then repairs it. It also repeatedly checks the blog's statistic/citation opening. The plan gets three verification requests plus one repair request; provider completion alone hides that overhead.

Generation takes six calls and 51,979 input tokens. Validation takes eleven calls and 174,509 tokens; repairs add five calls and 19,756 tokens. Validation plus repair consumes 308.3 summed seconds versus 86.8 seconds writing. The review machinery costs more time and context than producing the content.

Concrete avoidable defects:

- All three LinkedIn drafts initially use CTA labels longer than the four-word rule, despite the approved campaign action being “Book a demo”. These lead to three separate model repairs and repeat validations. A structured CTA field with the approved label avoids the defect at construction time.
- The blog is initially 897 words by the application's counter. A tiny insertion intended to fix the deficit produces a malformed fragment and only reaches 899. A second repair finally reaches 908. Targeting exactly the minimum and patching individual words wastes calls and damages prose.
- The final blog still has no byline. The draft metadata cannot be considered complete merely because the prose check passes.
- The landing page is only 94 body words by the application-style counter. It names functions and a proof point, but it is a skeletal page rather than a persuasive regulated-buyer destination. Short is not automatically wrong, but the validator's pass is not a strategic or editorial quality score.
- Every draft remains blocked for production dependencies, including destinations, review/clearance and image review. Some are appropriate human requirements. Others, such as asking for an Ostler quote while the copy already uses the approved numeric result without a quote, deserve a requirement-level check rather than automatically becoming user work.

Final blog quality is acceptable as a cautious first draft, but generic: much of it repeats distinctions between vendor status, team decisions, reporting and containment. Compliance improved more than campaign persuasiveness did. The email and LinkedIn posts are grounded but very thin.

## Awareness: useful autonomy, information lost after planning

This brief intentionally delegates audience, channel mix and campaign details. The agent proposes three LinkedIn posts and a blog, labels choices as assumptions, keeps educational CTAs rather than turning the work into demand generation, and avoids unnecessary clarification. Discovery and the independent check correctly return no unresolved conflicts. External research is skipped. These are sensible autonomous behaviors.

However, requirement extraction already chooses the four-asset mix. Later planning operates under an exact-asset contract built from those recommendations. The agent distinguishes assumptions in prose, but implementation can prematurely freeze recommendations as requirements. Keep explicit brief requirements separate from the planning proposal until approval.

The plan establishes Q1 2027 as an assumption, proposes dates and Kofi as the author, and includes report and rollout-guide URLs. In contrast, the asset passed to validation still says “Q1; year not specified.” The saved blog has a null byline and an unlinked rollout CTA. Validators repeatedly ask for approved URLs even though the plan had named destinations. This is a context-handoff defect: availability at plan level does not mean the correct structured field and evidence reach the writer/reviewer.

Validation consumes 155,764 input tokens across eight requests; four repairs add 17,917. Their combined 271.3 summed seconds compare with 62.4 seconds writing. Repairs fix first-mention naming, a missing educational CTA, repetition and an underlength blog. The initial blog is 780 words by the application's counter; the final saved body is 941. Reaching the length range is better than leaving it short, but the added discussion is repetitive and generic. It needs editorial judgment, not just count compliance.

Two image-selection requests have byte-equivalent serialized request bodies. The second reuses 11,596 of 11,599 input tokens and selects different images. This demonstrates excellent reuse for an actual identical request, but the export does not establish why the request was invoked twice. Treat it as possible duplicate work to trace, not a proven automatic retry or cache necessity. Do not count an unnecessary duplicate as optimization success.

All four final drafts pass the stored text validation and remain blocked for publication dependencies. The process creates usable starting copy, but does not deliver four ready-to-publish assets.

## Leashgate GA: failure and defects before failure

This is the most demanding brief: eleven distinct assets, audience separation, embargo, a tightly scoped GA-status override, exact outcome language, no borrowed proof or latency figures, no pricing/support promises, no early-access screenshots, and two specific X workflows.

The agent extracts eleven assets and recognizes that the future GA status is explicitly authorized for these campaign assets. It does not need another approval document for that status change. It detects the genuine non-launch-X scheduling conflict and the product-page hero conflict. The user's choices resolve the workflow and permit suitable current UI subject to constraints/timing. This does not magically supply an eligible screen; that remains a design dependency.

But the hero question is asked twice. The saved question list contains two answered versions of the same asset-01 issue. The batched review emitted the issue with different decision types (`other` versus `campaign_choice`) and different wording/quotes. Current deduplication requires matching decision type as well as asset and grounding, which offers a concrete explanation for why equivalent questions can survive. Canonicalize issue identity around the affected asset and governing rule before rendering questions; persist the answer against that identity.

The two logical review passes expand into six provider calls: three find batches and three independent-check batches. They consume 479,691 input tokens, 77.6% of all known campaign text input, and 303.5 summed seconds. These are overlapping batches, not six repeated complete reviews. The pairwise batching algorithm nevertheless has quadratic growth in the number of groups and repeats the whole brief in each batch.

A second defect is visible in the actual independent-check outputs. Each check receives the merged proposed findings, but only its local passage batch. One check rejects the hero finding because `source_256` is absent; another rejects the X finding because `source_106` is absent. Those sources exist elsewhere in the overall review. Local absence must not be interpreted as global absence. Partition proposed findings with their complete supporting evidence, or include their grounding passages with every batch that evaluates them. Merge support status globally before judging whether a finding survived.

The cache layout also deteriorates between passes: discovery batches have different source membership from check batches, and their early passage content changes. All six use the same cache key, yet check batches read only 3,199 tokens each. A cache key cannot make different prefixes identical.

### Exact failure

Run `96e495b6-e7e9-4f0b-bd98-25af6c3c93e2`, operation `campaign_plan`, fails after 60.095 seconds with `APITimeoutError`, rooted in an HTTP `ReadTimeout` against `api.openai.com`. There is no completed response and no reported usage. It is not a recorded refusal, JSON/schema error, context-limit rejection or rate-limit response. Provider-side cause beyond the read timeout is unknown.

The failed request is 624,037 serialized characters (approximately 624 KB for this mostly ASCII JSON), with a 12,000-output-token allowance and eleven outlines to construct. This is materially larger than the successful regulated and awareness plan requests, 339,495 and 237,827 serialized characters respectively. Request size and required work are plausible latency contributors, not proof of the server-side cause.

Its dynamic `brand_context.resolved_review.findings` contains three `source_snapshot` maps. Each is about 50,652 JSON characters: roughly 152 KB of nested hashes alone. The top-level snapshot is removed by prompt assembly, but nested snapshots survive. The resolved-review subtree is about 167 KB. External research adds about 48 KB of source bodies, including scraped website navigation. These are avoidable prompt payloads. Keep immutable audit snapshots in storage and pass applied decisions plus relevant exact evidence to the planner.

The recovery hole is concrete in current source: the final pending-conflict-answer path calls `process_message` directly. It bypasses `recover_preparation`, which wraps agent-invoked planning. The planning request has no operation-specific timeout, so it inherits 60 seconds, while conflict review explicitly permits at least 300 seconds and validation permits 180. Only one timed-out planning request appears in the export.

A bounded retry through saved checkpoints is appropriate for this preparation step, with separately recorded attempts and unknown billing for a timed-out attempt. A longer planning timeout helps availability, but without reducing payload it primarily makes the user wait longer. No failed campaign was retried during this audit.

## Why caching still underperforms

| Operation | Regulated cache rate | Awareness cache rate | Leashgate cache rate |
|---|---:|---:|---:|
| Independent conflict check | 93.45% | 96.84% | 3.94% |
| Draft writing | 14.75% | 40.82% | No drafts |
| Asset validation | 8.59% | 9.80% | No drafts |
| Asset repair | 0% | 0% | No drafts |

The large stable-first conflict prefix works well when the passage packet stays identical. It fails when batching changes the packet. Other misses have distinct causes:

- Actual validation and repair requests contain plain string messages with no explicit breakpoint after their reusable source message. Current `check` and repair requests do not set `cache_prefix_messages`. They therefore miss the explicit boundary used by writers, plan checks and conflict review.
- Validation dynamically embeds paragraph references into a `Literal` output schema. Schemas change between asset evidence packets. Stable source text alone cannot overcome earlier cache-affecting schema changes. Use a fixed string reference schema and enforce membership, source-span correctness and grounding in Python.
- Whole rule packs differ by asset and are not divided into reusable brand/channel segments. Repeated validation of a single asset reads only a short prefix despite identical first-message strings; absence of an explicit boundary is a strong implementation finding. The exact provider match failure is not proven without cache diagnostics.
- Concurrent cold writer calls do not necessarily warm each other. Keep concurrency for throughput; do not serialize all writers or add paid prewarming merely to raise the percentage.
- One-off planning, routing and extraction have limited intra-campaign reuse. Reducing their input is generally more useful than forcing them to be cacheable.

Official documentation confirms that reuse requires a matching rendered prefix and compatible settings, including output formats. It also documents explicit breakpoints and paid cache writes. The observed records report 624,068, 446,811 and 596,992 write tokens; optimizing only read percentage ignores that write work. See [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) and [cache diagnostics](https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics). Use diagnostics on a naturally occurring request pair, not a paid rerun solely to get a nicer percentage.

## Comparison with Yathaarth's feedback

| Feedback | Evidence in these runs | Judgment |
|---|---|---|
| About 3.8M input tokens for a campaign | These runs use about 0.55M–0.74M known text input; Leashgate stops before drafting | Lower observed volume, but not comparable enough to claim a percentage improvement. Full context remains expensive in time and unnecessary volume. |
| Stable material follows changing content | Many operations now put originals first; direct check reuse exceeds 93% in two campaigns | Partially fixed. Batching, nested snapshots, dynamic validation schemas and missing boundaries remain. |
| Single-digit cache rate | Overall text rates are 15.55%, 19.31%, 3.39%; asset validation stays below 10% | Improved selectively, not solved end to end. |
| gpt-4.1-mini is a poor agent choice | All text calls here return `gpt-6-luna` | The named old model is no longer used in these records. No controlled stronger-model comparison establishes the best replacement. |
| Blogs miss the brand length range | Both saved blogs meet 900–1,400 words after repair; initial drafts still miss it | Final enforcement improved; first-pass writing and repair efficiency remain weak. |
| Editing prints copy in chat instead of changing it | These runs contain no substantive saved-draft user edit test | Unverified. These runs cannot prove the edit bug is fixed. Add an explicit saved-edit acceptance case. |
| Tools should provide only context needed for each step | Writers are smaller than planning/review calls, and scoped routing exists | Direction is correct, but handoffs lose useful URLs/bylines/dates while retaining huge audit material. |

A stronger model everywhere would not solve these orchestration defects. Evaluate stronger planning/review only after packets are smaller; retain the inexpensive model where it passes a stage-level quality evaluation. Do not infer a current Sol/Terra price multiplier from this data.

## Priorities: fastest useful fixes and largest impact

Effort estimates are engineering estimates, not measured implementation times. These are recommendations; no application changes were made as part of the audit.

| Rank | Change | Estimated effort | Expected effect / acceptance evidence |
|---|---|---|---|
| 1 | Strip nested snapshots, hashes, review histories and raw research bodies from model DTOs; retain audit references in storage | 1–3 hours | Removes roughly 152 KB of nested snapshots from the failed planner alone, before research trimming. Assert exact source passages and active decisions survive. Measure request input on the next real run. |
| 2 | Route final-conflict-answer planning through bounded checkpoint recovery; set a justified planning timeout | 1–3 hours | Covers the exact failed path. Inject a read timeout: retry preparation once, retain review receipts, record both attempts, never replay approval or publication. |
| 3 | Pass approved destinations, byline proposals and date assumptions as explicit per-asset fields | 2–4 hours | Removes false missing-context dependencies and avoids stale “year unspecified” fields. Distinguish a proposal from confirmed metadata; preserve real human clearance requirements. |
| 4 | Add explicit validation/repair boundaries and replace dynamic span enums with fixed schemas plus Python checks | 2–4 hours | Targets 330,273 validation input tokens across the two drafted campaigns. Verify actual cache reads on repeated same-asset review; retain rejection of invalid references. |
| 5 | Supply exact CTA labels; aim blogs comfortably inside the range; mechanical preflight before semantic review | 2–4 hours | Avoid the three regulated CTA repairs and near-minimum word-count repairs. Keep grounded review, but do not pay it to rediscover a known count failure before correcting that failure. |
| 6 | Canonicalize equivalent conflicts and keep each finding with its grounding through batching | Half–one day | One hero question, no local-batch false source-absence judgments. Tests should reproduce these exact two failures. |
| 7 | Narrow mandatory evidence by applicability and keep stable batch membership across review passes | One–two days | Largest architectural token reduction, especially the 479,691-token Leashgate review. Preserve explicit coverage of relevant rule pairs; do not silently discard coverage to improve a benchmark. |
| 8 | Reduce repeated broad validation; check changed fields and dependent rules after small repairs | Half–one day | Asset validation/repair uses 28 calls for ten assets. Recheck dependency closure and perform a final coherent review; do not simply trust a patch or weaken gates. |
| 9 | Test lower reasoning effort where supported, and stronger models only for failing judgment stages | Half day for a small controlled evaluation | Reasoning dominates output. Measure first-pass quality, repair count and total elapsed time, not per-call price. No savings magnitude is established yet. |
| 10 | Trace identical image calls and add idempotent selection receipts if duplication is confirmed | 1–3 hours | Avoid paying even cached input and fresh output for unintended repeated work. Confirm the trigger first. |

The fastest convincing second submission would show: one successful eleven-asset GA plan through the conflict-answer path; no duplicate hero question; lower actual planner input without losing exact claims/overrides; approved metadata carried to drafts; and one real saved-blog edit verified by an actual state change. Report cold and warm cache rates separately, first-pass and final word counts, repair counts, unknown billing and service time separately from user pauses. Preserve the useful inspection and exact-source behavior.

## Evidence files

- Raw campaign snapshots, activity and all request/response bodies: `evaluation/campaign-audit/`.
- Every individual request's input inventory and actual structured output: the three `*-request-review.md` files in that directory.
- Recomputable totals and operation breakdowns: `summary.json`; run `python3 evaluation/campaign-audit/analyze.py` to rebuild them from the snapshots, with no network or model calls.
- Relevant source paths: `backend/app/api/campaigns.py` (conflict-answer handoff, planning, failure handling, generation), `backend/app/campaign_agent.py` (bounded recovery), `backend/app/llm/prompt_payloads.py` (planner DTO), `backend/app/brand/context_scope.py` (mandatory source retention), `backend/app/brand/draft_validation.py` (span schema, counters, repairs), `backend/app/brand/review_budget.py` and `conflict_coverage.py` (batching and merge), `backend/app/brand/image_selection.py` (shortlists and selection validation).
