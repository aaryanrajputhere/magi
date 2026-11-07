# Shared evidence cache

`MAGI_SHARED_EVIDENCE_CACHE=true` opts supported gpt-6-luna requests into a
common source-text prefix. The default remains false pending full campaign
quality evaluation. `MAGI_EXPLICIT_PROMPT_CACHE` must also be enabled.
Docker Compose passes both flags through; recreate the backend after setting them.

Planner, verifier, context router, conflict review, writers, validators and repairs
participate. Other models and tool-using requests keep the existing wire format.
Original content is stored once in content-addressed `original_texts` blocks;
task-local source metadata and citation IDs retain their original structure and
refer to the text with `text_ref`. No evidence is added or dropped.

Successful persisted runs form a campaign/brand/model-scoped registry. Later calls
reuse only complete previously emitted prefixes fully contained in their own
selected evidence. Natural brand/channel message groups remain separate blocks.
Up to three explicit boundaries leave changing task data uncached. A task with
only partial overlap writes a new block rather than importing unselected sources.
No future source selections or hardcoded campaign intersections are assumed.

The top-level instructions and plain-text response format stay constant. Strict
operation-specific schemas are appended as configuration updates after the shared
boundaries; responses still undergo existing Pydantic validation and grounding.
There are no automatic provider retries, prewarming calls, TTL increases, changed
writer concurrency, or changes to saved evidence artifacts.

Run metadata includes `evidence_version`, `evidence_blocks`,
`evidence_source_count`, `effective_schema_fingerprint` and the actual prefix
fingerprint. These fields are available through the inspector runs API.
Disable the shared-evidence flag to restore the legacy request path; historical
runs remain readable, with no database migration.

## Verification

A two-request live gpt-6-luna probe successfully extracted the exact original
source value through the text reference with two different strict schemas.
The first request wrote 1,530 prefix tokens; the second read all 1,530 tokens
with zero writes. This establishes wire compatibility, not whole-campaign quality
or a 50% campaign hit rate.

Offline chronological replay:

```
PYTHONPATH=backend python backend/scripts/replay_shared_evidence.py runs.json
```

This respects observed request start/end times, so simultaneous writers cannot
benefit from future completions. Counts are cl100k_base estimates of reusable
blocks, not provider hit-rate predictions. On campaign b232b066, planner evidence
is reusable by initial verification and routing (~58,744 block tokens each).
Conflict discovery remains cold; its full block does not fit later selected sets.

Before default rollout, evaluate a fresh campaign against the same brief/corpus.
Compare provider reads, writes, total input cost, output quality, citation/source
coverage and repair counts. Never count a warmed run as a cold benchmark or force
extra repairs to improve the metric. Full campaign evaluation has not been run.

## Isolated replay results (2026-11-07)

Three stage inputs from b232b066 were replayed into a separate temporary database.
Verifier/router used their recorded candidate inputs, rather than forming a new
end-to-end campaign. The original application database was not modified.

| Stage | Input | Cached read | Cache write | Read rate |
| --- | ---: | ---: | ---: | ---: |
| Planner | 105,859 | 0 | 58,263 | 0% |
| Verifier after scope clarification | 104,723 | 58,263 | 0 | 55.63% |
| Context router | 83,670 | 58,263 | 3,410 | 69.63% |

The first verifier attempt also read 58,263 tokens, but failed the existing
field-scope validator because it emitted asset_id=null for a timing finding.
The schema description now explicitly lists which fields require an asset ID.
A bounded follow-up passed strict validation. No validator was relaxed and no
automatic retry was added. Both successful verifier/router outputs covered all
eight supplied assets, and all router source references belonged to supplied
originals. This evaluation does not establish equivalent campaign quality or
repair counts. Additional text references add input overhead, so full rollout
must compare total cost including cold writes and failed attempts.

109 focused tests passed after the final changes. Exact source text and metadata
round-trip checks also passed for all 33 participating historical requests. Full backend collection
is blocked by three existing test modules importing removed conflict APIs
(FormulatedQuestions, DecisionCoverageCheck and Finding).

## Follow-up: metadata boundaries and reliable repairs

The `evidence-v2` transport separates channel sources from a single general source
pool. A focused verifier can drop channel passages while retaining the unchanged
non-channel block. Natural writer brand/channel boundaries remain intact, and
selected source membership/text is unchanged. Offline replay of a250a011 gives
196 shared non-channel passages (~37,811 estimated block tokens) reusable by its
209-passage focused recheck, which previously had no matching source block.

One additional explicit breakpoint now covers the caller-declared stable metadata
messages after the operation's schema and instructions. Changing drafts, findings
and measurements remain after that boundary. At most four explicit breakpoints
are emitted. This repairs the earlier source-only caching regression.

Validation sends compact per-chunk lists of paragraph aliases instead of repeated
chunk IDs and character offsets. Python retains exact original paragraphs and
restores immutable canonical references before saving findings/dependencies.
Unknown spans and mismatched chunks remain rejected. Table rows within one
paragraph are not separate spans.

Repair patches may supply null offsets and a unique exact original anchor. Python
resolves the anchor only against the matching draft hash; ambiguous anchors,
missing text, overlap and stale hashes remain rejected. Short repairs receive an
8K output limit; bodies of at least 500 words receive 12K. Exhausted/invalid model
responses retain the original draft and findings as needs_review, with the failed
run recorded, rather than dropping the asset. A rejected patch gets at most one
additional correction attempt per asset, with exact occurrence counts and the
unchanged draft. Both attempts are recorded; no fuzzy matching or transport
retries are used. A second failure retains the draft for review.
Whole-field drafting targets stay within original limits and exclude section
budgets. Missing CTA URLs remain production dependencies; internal asset IDs
cannot satisfy the requirement for a publishable URL.

These changes do not mark unresolved assets as complete. Full-campaign cache and
quality comparisons must account for changes in repair counts and all costs.

### Follow-up validation evidence

137 focused regression tests passed, including unique-anchor resolution, repeated
anchor rejection, one bounded correction, exhausted repair preservation, exact
source grounding with compact aliases, immutable plan-metadata blockers, source
membership, publication dependencies and unchanged metadata cache prefixes.

Isolated copies of the failed blog and landing-page inputs were evaluated without
changing a250a011. The blog repair completed and increased the body from 692 to
913 words, within its original 900–1,400 range; contain-mode, observe-mode and
early-access qualifications remained present. Mechanical checks passed. It was
not marked ready while editorial/dependency issues remained. Metadata-cache
rechecks read 9,562 tokens out of 11,640–11,687 (~82%).

The final compact-alias check retained a blocker for an internal asset-ID CTA
link. The final landing-page flow removed both repeated unsupported links using
unique exact surrounding anchors, with mechanical checks passing and a recheck
reading 6,748 of 7,833 input tokens (86.15%). Findings about the immutable approved
plan remain publication blockers rather than copy-patch instructions. No failed
or unresolved review was waived. These samples do not establish a whole-campaign
hit rate or equivalent quality for every future generation.
