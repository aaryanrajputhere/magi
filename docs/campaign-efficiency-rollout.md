# Campaign model efficiency rollout

Implemented changes preserve the campaign API's requirement and plan shapes. Originals stay in saved evidence; prompt-only projections remove hash/ranking bookkeeping and reference repeated requirement fields without rewriting their values.

## Defaults and switches

- Stage-aware context selection, exact-option requirement updates, duplicate-brief removal, campaign-only memory guards and deterministic prerequisite dispatch are active.
- `MAGI_EXPLICIT_PROMPT_CACHE=true`: requests with explicit source boundaries use explicit-only caching on supported models. Unmarked append-only agent histories retain implicit behavior. Set false to restore previous implicit writes.
- `MAGI_PARALLEL_VERIFICATION=false`: opt-in global and channel checks. Completed checks are cached by complete request/schema signature; changed candidates, evidence, decisions, model or template cause fresh checks. Partial failures retain known findings and keep the review incomplete.
- `MAGI_PARALLEL_PLANNING=false`: opt-in strategy/calendar then independent channel outlines. Asset IDs, order, explicit audiences/dates and cross-channel dependencies are preserved; the ordinary verification/repair flow checks the merged result. A required channel clarification returns a campaign question instead of a partial approvable plan.
- `MAGI_PARALLEL_CONFLICT_RESOLUTION=false`: opt-in independent conflict resolutions after decision matching.

All provider calls share a three-request limit per campaign in a backend process. Workers own independent production clients and DB logging sessions. No worker changes campaign state. Existing generation and conflict batching remain bounded. Application deployment currently runs one backend process; the in-process limit is not a distributed rate limiter.

The inspector exposes cache writes and per-request prefix diagnostics. Retrieval planning, decision matching and conflict resolution move stable instructions into a marked developer prefix without duplicating them.

Use normal deployment/restart procedures to load settings. No database migration is required. Planning-selection receipts use `planning_scope_v3`, so older selections cannot bypass the changed scope logic. Existing saved campaigns are not rerun automatically.

## Offline evaluation

Export the campaign and `/api/campaigns/<id>/runs` via read-only requests. Keep these private original-input snapshots outside source control. Run from the repository with backend dependencies installed:

```sh
python backend/scripts/evaluate_campaign_efficiency.py \
  --runs /tmp/campaign-runs.json --campaign /tmp/campaign.json \
  --output /tmp/efficiency-offline.json
```

The report measures fields with cl100k_base and records retained/deferred source IDs. These are payload estimates, not exact provider token counts or speedup claims. It does not execute model source selection or generation. Its filtered accounting objects are not replayable provider requests.

Observed offline estimates for campaign `84aed838-0184-4a46-87fe-cb2f1f88e2b7`:

| Payload | Before | After |
|---|---:|---:|
| Decision matching | 19,002 | 1,446 |
| Conflict resolution | 23,685 | 6,029 |
| Planning | 81,380 | 76,722 |
| Verification | 75,102 | 73,033 |

Planning retained 245 of 264 original sections in this conservative offline filter; verification deferred 9 sections. Additional narrowing depends on the live selector's factual-source choices. The original source bodies are unchanged. Do not infer the proposed 25% total-input or 20% latency targets from these partial estimates.

## Isolated paid evaluation

The harness can run three cold/warm pairs for serial optimized planning and three for parallel planning/verification. Each trial uses an in-memory clone of the exported campaign and logs to a NEW evaluation database; it never accesses the application database or campaign mutation APIs. It measures the planning/verification segment, not the entire intake conversation. It makes paid calls only with `--live`:

```sh
python backend/scripts/evaluate_campaign_efficiency.py \
  --runs /tmp/campaign-runs.json --campaign /tmp/campaign.json \
  --live --database /tmp/new-efficiency-evaluation.sqlite \
  --output /tmp/efficiency-live.json
```

A namespace isolates each cold/warm pair at the beginning of the instructions. Reuse within a pair is permitted; provider read/write counters remain authoritative. Each trial has fresh selection/review receipts so warm trials measure provider caching rather than skipping calls via application receipts. The harness refuses an existing database path.

Inspect output plans for the FedRAMP prohibition and approved replacement, SOC 2 qualification, Vaskell exclusion, all six deliverables, the 25-qualified-demo target, channel limits and CTA dependencies. Compare input/output/reasoning tokens, cache reads AND writes, accepted review status, and elapsed wall time. Do not enable flags from token reductions alone. Target 25% less input and 20% lower elapsed processing time without quality loss; these remain release goals pending paid evaluation. Enable parallel verification first, followed by parallel planning only if its additional strategy call yields a worthwhile measured result. Restore the corresponding flag to false to return to serial execution.

## Validation limits

Focused efficiency, cache, scope, review-recovery and generation tests pass. The repository's full suite also includes legacy conflict tests that import removed APIs (`Finding`, `DecisionCoverageCheck` and related helpers) and cannot collect. A connection-diagnostics test expects `ConnectionRefusedError`, while this environment reports an underlying `OSError`. These issues must not be presented as successful full-suite validation.

Final broad-suite attempt: 661 passed, 70 failed, 9 errors. This suite is not green; the failures include legacy conflict API imports, outdated fixtures, and the connection-diagnostics environment mismatch. These results are separate from the focused passing regression set.

Paid smoke results: serial cold 129.271s ($0.02871), serial warm 107.126s ($0.01056), parallel cold 106.166s ($0.07445). The remaining warm parallel trial was stopped to wrap up at user request. These are preliminary single trials, not release qualification; parallel paths remain opt-in. Focused validation: 128 passed; frontend type checking passed.
