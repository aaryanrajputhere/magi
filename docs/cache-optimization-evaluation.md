# Prompt-cache changes and live verification

Applied the remaining changes from the audit of campaign
`4932d91b-2508-4999-9d05-a7fb9be18105`. Its saved data was not changed.

## What changed

- Draft schema is fixed (`source_ids: list[str]`). Python rejects unknown citations
  before validation or saving. Common brand and channel prefixes are intersections
  of selected originals, with immutable source references and no added corpus.
- Ordinary tool results return concise outcomes. The agent retains append-only
  within-turn history; `read_campaign` explicitly exposes full saved state.
- Planning originals precede per-campaign mappings; outline and verification
  schemas are fixed. Python preserves deliverable coverage, citation membership
  and field-scope checks. Focused rechecks remain focused. Field-patch schemas
  remain bounded to authorized edits rather than allowing unrestricted patches.
- Gap checking, source routing and image selection put selected originals first.
  Research input omits execution IDs. Image selection uses a fixed schema with
  exact batch coverage and per-asset shortlist checks in Python.
- Image-query vectors are reused by brand plus model/dimensions/exact-input hash.
- Revision returns structured clarifications separately from copy; saved drafts
  remain unchanged while clarification is pending. Small edit/memory calls are
  not padded for caching.
- Supported models receive explicit cache breakpoints at reusable message endings.
  Earlier/unknown models keep plain messages. No synthetic prewarming is used.

## Measured samples

These are provider-reported examples, not a claim of equivalent whole-campaign
cost or quality. Outputs, source selections and repair counts can vary.

| Operation | Original recorded call | Updated recorded call |
| --- | ---: | ---: |
| Independent conflict check input | 74,232 | 74,383 |
| Cached input | 0 | 72,330 |
| Cache hit rate | 0% | 97.24% |
| Cache-write tokens | 74,229 | 2,050 |
| Estimated request cost | $0.009960925 | $0.00151285 |
| Request duration | 15.824s | 11.807s |

One updated draft request reused 3,778 of 6,672 input tokens (56.62%); the initial
cold draft request had zero reads. Writers remain concurrent, so simultaneous
cold requests are not guaranteed to warm one another.

## Evaluation scope and discovered issue

The five-asset evaluation is `c8cf66d1-7486-497f-b185-b48368ad02c8`.
Its planning probe ran before explicit boundaries were added; draft generation
uses the completed boundary implementation. The conflict-check sample above is
from `c0610293-c124-4401-86b5-71e904d9a534`, a separate run of the same brief that
recommended four assets. Those runs must not be combined into a claimed single
end-to-end campaign improvement.

The four-asset run exposed a fixed-schema verifier returning unchanged asset IDs
in a focused recheck. Python correctly blocked it. The verifier now retries once
with explicit target-ID feedback while preserving the same source prefix; a
second invalid response still fails. Regression tests cover both outcomes. This
correction was tested locally rather than rerunning another complete planning
campaign merely to improve benchmark numbers.

Offline replay of the original five drafting calls establishes identical shared
brand messages and exact preservation of every selected passage once. It does
not establish runtime quality equivalence. Use `backend/scripts/audit_prompt_cache.py`
on saved inspector traces for per-operation token counts, reads/writes, estimated
costs and durations. Wall spans include pauses; summed durations include overlaps.

Final handoff: five-asset evaluation state `generating`, with 3 saved drafts. Main caching changes are running locally. The final bounded verifier retry is code-tested and requires a backend restart after the active evaluation finishes. No further evaluation campaign is scheduled.

Follow-up: the verifier retry and approval controls are now loaded. The temporary
400K budget gate was subsequently removed at the user's request. The current flow
reduces calls: normal preparation skips gap-model calls, answers to already-reviewed
choices reuse the completed review, and evidence retries retain valid checkpoints.
Historical budget_blocked rows remain as records of past attempts; they are not
active restrictions. No campaign was regenerated to validate this follow-up.
