# Campaign evidence pipeline

## Plan and implementation

1. Extract topic, global constraints, and one requirement per requested asset from the full original conversation. Preserve IDs, formats, audiences, publication dates, senders/bylines and individual constraints. Save the extraction and original input as a `campaign_requirements` artifact before search planning. User requirements express intent; they do not establish brand authority.
2. Plan topic/product/claims searches. Replace bundled channel searches with separate format/rules and voice/imagery searches for each requested channel. Repeated assets share channel retrieval, but remain separate deliverables.
3. Review topic evidence and channel evidence independently, concurrently with bounded worker pools. Each channel gets its own authority scope and reconciliation. Keep citations, supersession decisions and unresolved conflicts in persisted review artifacts. Missing evidence or a conflict without a confirmed winner cannot establish a usable claim.
4. Join topic findings with the relevant channel findings and individual requirement into an asset evidence package. Use these packages for outlining, generation, image selection and revisions. Do not send other channels' rules to an individual drafting call.
5. Enforce the requirement IDs, formats, audiences and dates on the plan. Attempt one repair for incomplete outlines, then reject continued mismatch. Recheck after compliance repairs and before execution. A genuine clarification can defer all outlines while keeping the complete requested asset list saved.
6. Audit finished copy and imagery using the same package, including actual character/word counts, factual support, exact wording, links and channel rules. Attempt one repair, omitting optional imagery, then audit again. Persist only passing drafts as completed. Failed drafts leave the job in `generation_error`; explicit retries retain completed drafts.

## Compatibility and observability

- Existing saved campaigns retain their original generation behavior; new plans carry requirements and evidence packages.
- Extensions retain existing drafts and remap new requirements/packages to the added asset IDs.
- Model requests and audit/repair attempts retain normal run logging. Requirements, channel/topic reviews and per-asset packages are inspectable through persisted artifacts and `/api/campaigns/{id}/activity`.
- A completed audit is a model assessment, not a guarantee of complete corpus coverage. Retrieval remains bounded; each review carries its coverage limitations. Source and authority fingerprints are checked again during execution.
- New work does not publish, schedule or send content. Draft generation still requires explicit UI approval.

## Verification

Regression tests cover the eleven-asset launch shape, saved original input, per-channel search expansion, scoped authority review, unresolved conflicts, missing/changed deliverables, bounded repair, reload persistence, execution guards and draft audit success/failure. Provider responses are mocked; no live launch brief is sent by these tests.
