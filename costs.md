# Campaign costs

## Brief 1: Leashgate GA launch

[Campaign in the app](http://localhost:5173/#campaigns/34dc9933-7f5e-4984-9600-d6917c6a801a) · campaign ID: `34dc9933-7f5e-4984-9600-d6917c6a801a`

### Brief and recorded outcome

The initial user message is the Leashgate general-availability campaign from [brief-1-leashgate-ga-launch.md](wickhold-candidate-package/candidate/briefs/brief-1-leashgate-ga-launch.md), pasted with formatting changes. It announces GA on January 26, 2027, targets 60 demo requests by March 31, and requires launch-day and follow-up assets while preserving product-scope and pre-launch restrictions.

Snapshot: 2026-11-05. Campaign state: `needs_review`. The conversation records two answers about retaining the working-room hero and seeking a documented Brand exception to product-page imagery guidance. Those answers record the requested campaign treatment; they do not establish that the exception was granted. One saved plan and 11 saved drafts are present: one landing page, three emails, three LinkedIn posts, two X posts, and two blogs. All drafts are marked `needs_review`. This is a measured attempt, not a claim of a fully validated campaign or permission to publish.

### Campaign usage

| Scope | Calls | Reported input tokens | Reported output tokens | Reported cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Linked campaign, including conflict clarification and generation | 74 | 11,162,445 | 223,854 | 1,353,747 | 12.13% | $3.06979200 | 30 |

Reported input plus output tokens: **11,386,299**. Cached tokens are a subset of input tokens. These are the totals reported by 73 calls; one `campaign_conflict_find` call timed out without usable token usage. Its tokens and any provider billing remain unknown, so the reported token total is incomplete. Missing usage is excluded from the cache-hit denominator, not treated as a measured zero-token call.

The USD figure is a **known-cost subtotal**, not the full campaign cost. Thirty `gpt-6-luna` calls have no persisted cost estimate, including the timeout. No new provider calls were made to prepare this report.

### Breakdown by step

| Recorded operation | Calls | Reported input tokens | Reported output tokens | Reported cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `campaign_memory` | 1 | 2,572 | 449 | 0 | 0.00% | $0.00174720 | 0 |
| `campaign_agent_step` | 2 | 15,675 | 31 | 5,760 | 36.75% | $0.00459160 | 0 |
| `campaign_requirements` | 1 | 5,829 | 3,162 | 0 | 0.00% | $0.00739080 | 0 |
| `plan_retrieval` | 1 | 8,179 | 497 | 0 | 0.00% | $0.00406680 | 0 |
| `campaign_conflict_find` | 5 | 320,696 | 36,575 | 70,955 | 22.13% | Unknown | 5 |
| `campaign_conflict_check` | 3 | 260,267 | 17,479 | 7,016 | 2.70% | Unknown | 3 |
| `campaign_evidence_gap` | 1 | 322,597 | 105 | 0 | 0.00% | $0.12920680 | 0 |
| `campaign_research_plan` | 1 | 3,810 | 140 | 0 | 0.00% | $0.00174800 | 0 |
| `campaign_plan` | 1 | 749,412 | 1,962 | 0 | 0.00% | $0.30290400 | 0 |
| `campaign_deliverables_repair` | 1 | 176,021 | 1,944 | 0 | 0.00% | $0.07351880 | 0 |
| `campaign_plan_verification` | 1 | 464,182 | 539 | 0 | 0.00% | $0.18653520 | 0 |
| `campaign_plan_coverage_repair` | 1 | 765,997 | 1,632 | 0 | 0.00% | $0.30901000 | 0 |
| `generate_campaign_asset` | 11 | 3,455,635 | 15,485 | 1,270,016 | 36.75% | $1.02602520 | 0 |
| `select_campaign_image` | 11 | 1,344,073 | 1,535 | 0 | 0.00% | $0.54008520 | 0 |
| `campaign_asset_validation` | 22 | 2,120,882 | 127,122 | 0 | 0.00% | Unknown | 22 |
| `campaign_asset_repair` | 11 | 1,146,618 | 15,197 | 0 | 0.00% | $0.48296240 | 0 |

All 74 recorded attempts are included: 66 completed, seven incomplete, and one timeout. The table includes 11 automatic asset repairs and all 22 validation attempts. Initial-generation repairs are not user-requested draft edits. Token columns sum available reported usage; the conflict-finding row includes the timeout but cannot include its unreported tokens.

### Breakdown by model

| Recorded model | Calls | Reported input tokens | Reported output tokens | Reported cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt-4.1-mini-2025-04-14` | 44 | 8,460,600 | 42,678 | 1,275,776 | 15.08% | $3.06979200 | 0 |
| `gpt-6-luna` | 30 | 2,701,845 | 181,176 | 77,971 | 2.89% | Unknown | 30 |

### Research and shared preparation

Firecrawl research: one completed recorded attempt, five requests, and seven locally estimated credits. Its USD cost is unavailable in these records and excluded from the known-cost subtotal. Shared ingestion, document embeddings, and unrelated development/evaluation runs are excluded; their cost is not assumed to be zero. Campaign-linked query embeddings, if recorded, are included.

### Typical user edit

**Not measured for this campaign.** The recorded user follow-ups answer imagery-conflict questions before generation. They do not revise a saved draft. A typical edit still requires a targeted chat revision and measurement of all associated routing, memory, revision, validation, and retry calls.

### Calculation and evidence

Source: a consistent read-only transaction over `data/magi.db`, checked against the local campaign API for the linked campaign. Selection uses `llm_runs.context.campaign_id = '34dc9933-7f5e-4984-9600-d6917c6a801a'`, with the inspector's fallback check for legacy retrieval-planning references; no additional legacy run was found. The first recorded call started at 2026-11-05 08:38:52.019703 UTC; the last started at 2026-11-05 09:05:23.486177 UTC.

Costs use persisted per-run estimates. Cache hit rate is total reported cached input tokens divided by input tokens from calls with both counts available. Step and model breakdowns reconcile to the reported campaign totals. Unpriced rows display **Unknown**, and unreported usage remains unknown. Shared preparation and Firecrawl USD costs are not included.

## Brief 2: Q1 awareness push

[Campaign in the app](http://localhost:5173/#campaigns/09de5fdf-17ff-4dfb-babc-e37e97121c23) · campaign ID: `09de5fdf-17ff-4dfb-babc-e37e97121c23`

### Original brief

From: Ilse Varga-Holm
To: #marketing
Date: October 19, 2026
Re: Q1 awareness push

Adding this to the Q4 planning doc late, sorry. I'd like an awareness push in Q1.

We've come a long way since the rebrand, but I keep having conversations where people don't really know who we are or what we do. Maren had the same thing happen to her twice at dinner last week. It would be good to get the name out there more and give people a reason to pay attention.

Containment feels like the right thread, since that's what we want to be known for. It doesn't have to be a big production, but it should feel like more than our usual posts. If it helps pipeline along the way, great, though I don't want it to turn into a demand gen program.

Mix of channels is fine, whatever makes sense. Can someone pull a campaign together and show me a plan?

Ilse

### Recorded outcome and scope

Snapshot: 2026-11-05. Campaign state: `needs_review`. The saved plan contains five assets: two LinkedIn posts, one X post, one blog, and one email. All five saved drafts are marked `needs_review`; this is a measured attempt, not a claim of a fully validated campaign.

This is the selected run of the two measured Brief 2 campaigns. It used 2,998,637 total tokens versus 3,815,412 for campaign `6d3ec876-d2e2-4e93-b288-c49da5620a91`, and its known estimated subtotal was $0.79163360 versus $0.99552280. Both produced five drafts marked `needs_review`, and neither included a measured user draft edit. The alternative had a slightly higher cache hit rate (0.31% versus 0.30%). Selection favors lower recorded token use and known spend; it does not establish better draft quality or a lower full cost, because review pricing is incomplete in both runs. Only the selected run is included in the tables below.

This report follows the token, cost, cache-hit, and edit reporting requirements in [the assessment brief](brief.pdf). Measured runs for all three supplied briefs are included in this report.

### Campaign usage

| Scope | Calls | Input tokens | Output tokens | Cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Linked campaign, including later memory update | 43 | 2,939,270 | 59,367 | 8,960 | 0.30% | $0.79163360 | 17 |

Input plus output tokens: **2,998,637**. Cached tokens are a subset of input tokens, not additional tokens.

The USD figure is a **known-cost subtotal**, not the full campaign cost. Review calls using `gpt-6-luna` lack a supported pricing snapshot in the local registry and remain unknown. No new provider calls were made to prepare this report.

### Breakdown by step

| Recorded operation | Calls | Input tokens | Output tokens | Cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `campaign_memory` | 2 | 876 | 212 | 0 | 0.00% | $0.00068960 | 0 |
| `campaign_agent_step` | 3 | 8,839 | 146 | 4,992 | 56.48% | $0.00227160 | 0 |
| `campaign_requirements` | 1 | 1,711 | 1,217 | 0 | 0.00% | $0.00263160 | 0 |
| `plan_retrieval` | 1 | 3,651 | 399 | 0 | 0.00% | $0.00209880 | 0 |
| `campaign_rule_documents` | 1 | 69,503 | 1,644 | 0 | 0.00% | $0.00000000 | 1 |
| `campaign_rule_limits` | 4 | 11,337 | 1,513 | 0 | 0.00% | $0.00000000 | 4 |
| `campaign_conflict_find` | 1 | 89,963 | 1,493 | 0 | 0.00% | $0.00000000 | 1 |
| `campaign_conflict_check` | 1 | 90,299 | 1,435 | 0 | 0.00% | $0.00000000 | 1 |
| `campaign_evidence_gap` | 1 | 82,551 | 109 | 0 | 0.00% | $0.03319480 | 0 |
| `campaign_research_plan` | 1 | 1,601 | 117 | 0 | 0.00% | $0.00082760 | 0 |
| `campaign_plan` | 1 | 218,795 | 1,025 | 0 | 0.00% | $0.08915800 | 0 |
| `campaign_plan_verification` | 1 | 284,946 | 58 | 0 | 0.00% | $0.11407120 | 0 |
| `generate_campaign_asset` | 5 | 486,125 | 1,758 | 3,968 | 0.82% | $0.19607240 | 0 |
| `select_campaign_image` | 5 | 477,813 | 506 | 0 | 0.00% | $0.19193480 | 0 |
| `campaign_asset_validation` | 10 | 721,136 | 46,089 | 0 | 0.00% | $0.00000000 | 10 |
| `campaign_asset_repair` | 5 | 390,124 | 1,646 | 0 | 0.00% | $0.15868320 | 0 |

All recorded attempts are included, including automatic asset repairs and incomplete validation responses. Repairs during initial generation are not counted as user-requested edits.

### Breakdown by model

| Recorded model | Calls | Input tokens | Output tokens | Cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt-4.1-mini-2025-04-14` | 26 | 1,957,032 | 7,193 | 8,960 | 0.46% | $0.79163360 | 0 |
| `gpt-6-luna` | 17 | 982,238 | 52,174 | 0 | 0.00% | $0.00000000 | 17 |

### Research and shared preparation

Firecrawl research: 1 recorded attempt(s), 5 requests, 7 locally estimated credits. Credits are separate from OpenAI tokens; their USD cost is not available in this record and is excluded from the known-cost subtotal.

Shared brand ingestion, document embeddings, and unrelated development/evaluation runs are excluded. They are not attributable to this campaign from the selected campaign records. This report does not treat their cost as zero or distribute it arbitrarily across briefs. Campaign-linked query embeddings, if recorded, are included in the tables.

### Typical user edit

**Not measured for this campaign.** The later message, “Remember to put two hashtags in every linkedin post”, updated memory; it did not revise a draft. Its `campaign_memory` call used 290 input tokens, 43 output tokens, zero cached tokens, and an estimated $0.00018480. That cost is already included above and is not a typical asset edit cost.

To finish the edit measurement, record a targeted draft edit and sum every associated call (routing, memory processing, revision, validation, and any retries), then report input/output/cached tokens and the known cost plus unknown-cost calls. Do not substitute an initial-generation repair for that measurement.

## Brief 3: Regulated teams push

[Campaign inspector](http://localhost:5173/inspect/fa0fecb2-1536-4314-847e-531ba5b02f6e) · campaign ID: `fa0fecb2-1536-4314-847e-531ba5b02f6e`

### Brief and recorded outcome

The supplied “Regulated teams push, February 2027” brief requests a campaign for healthcare and public-sector security leaders, focused on Evenburn with Quenchline supporting it. Its goal is 25 qualified demo requests by the end of March, with a February 16–March 31, 2027 campaign window. The brief asks to lead with FedRAMP authorization and feature Vaskell Health's result and customer quote; those requests require checking against the governing brand sources.

Snapshot: 2026-11-05. Campaign state: `needs_review`. The recorded conversation includes a clarification about Vaskell's lack of external-use clearance. The user chose a different customer reference only if already cleared for the intended asset types. The user then requested “remove post 4 from the plan”; the saved plan changed from five assets to four before generation. Four drafts were saved: two LinkedIn posts, one email, and one blog. All four are marked `needs_review`. This is a measured attempt, not a claim that the drafts passed validation or are ready to publish.

### Campaign usage

| Scope | Calls | Input tokens | Output tokens | Cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Linked campaign, including clarification and plan adjustment | 44 | 4,086,441 | 92,797 | 203,648 | 4.98% | $1.14817800 | 14 |

Input plus output tokens: **4,179,238**. Cached tokens are a subset of input tokens.

The USD figure is a **known-cost subtotal**, not the full campaign cost. Fourteen `gpt-6-luna` review calls lack a supported pricing snapshot and remain unknown. All 44 calls have recorded input, output, and cached-token counts. No new provider calls were made to prepare this report.

### Breakdown by step

| Recorded operation | Calls | Input tokens | Output tokens | Cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `campaign_memory` | 2 | 1,253 | 330 | 0 | 0.00% | $0.00102920 | 0 |
| `campaign_agent_step` | 7 | 259,868 | 411 | 203,648 | 78.37% | $0.04351040 | 0 |
| `campaign_requirements` | 1 | 2,523 | 1,698 | 0 | 0.00% | $0.00372600 | 0 |
| `plan_retrieval` | 1 | 4,322 | 385 | 0 | 0.00% | $0.00234480 | 0 |
| `campaign_conflict_find` | 3 | 252,222 | 17,509 | 0 | 0.00% | Unknown | 3 |
| `campaign_conflict_check` | 3 | 255,268 | 10,592 | 0 | 0.00% | Unknown | 3 |
| `campaign_evidence_gap` | 1 | 95,361 | 134 | 0 | 0.00% | $0.03835880 | 0 |
| `campaign_research_plan` | 1 | 1,934 | 121 | 0 | 0.00% | $0.00096720 | 0 |
| `campaign_plan` | 1 | 271,564 | 1,091 | 0 | 0.00% | $0.11037120 | 0 |
| `campaign_plan_verification` | 2 | 517,832 | 786 | 0 | 0.00% | $0.20839040 | 0 |
| `campaign_plan_coverage_repair` | 2 | 607,737 | 2,364 | 0 | 0.00% | $0.24687720 | 0 |
| `generate_campaign_asset` | 4 | 492,289 | 1,687 | 0 | 0.00% | $0.19961480 | 0 |
| `select_campaign_image` | 4 | 393,105 | 494 | 0 | 0.00% | $0.15803240 | 0 |
| `campaign_asset_validation` | 8 | 604,646 | 52,477 | 0 | 0.00% | Unknown | 8 |
| `campaign_asset_repair` | 4 | 326,517 | 2,718 | 0 | 0.00% | $0.13495560 | 0 |

All recorded attempts are included: 42 completed calls and two incomplete asset-validation responses, plus four automatic asset repairs. Initial-generation repairs and the pre-generation plan adjustment are not user-requested draft edits.

### Breakdown by model

| Recorded model | Calls | Input tokens | Output tokens | Cached input tokens | Cache hit rate | Known estimated USD | Unknown-cost calls |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `gpt-4.1-mini-2025-04-14` | 30 | 2,974,305 | 12,219 | 203,648 | 6.85% | $1.14817800 | 0 |
| `gpt-6-luna` | 14 | 1,112,136 | 80,578 | 0 | 0.00% | Unknown | 14 |

### Research and shared preparation

Firecrawl research: one completed recorded attempt, five requests, and seven locally estimated credits. Its USD cost is unavailable in these records and excluded from the known-cost subtotal. Shared ingestion, document embeddings, and unrelated development/evaluation runs are excluded; their cost is not assumed to be zero. Campaign-linked query embeddings, if recorded, are included.

### Typical user edit

**Not measured for this campaign.** “remove post 4 from the plan” adjusted the proposed deliverables before generation; it did not revise a saved draft. A typical edit still requires a targeted chat revision and measurement of its associated routing, memory, revision, validation, and retry calls.

### Calculation and evidence

Source: a read-only snapshot of `data/magi.db`, checked against the local campaign API behind the linked inspector. Selection uses `llm_runs.context.campaign_id = 'fa0fecb2-1536-4314-847e-531ba5b02f6e'`. The retrieval-planning call is directly linked by that field; the saved trace supplies no additional legacy planning-run ID. The first recorded call started at 2026-11-05 07:38:39.032262 UTC; the last started at 2026-11-05 07:57:32.496474 UTC.

Costs use persisted per-run estimates, and cache hit rate uses total cached input tokens divided by total input tokens. The step and model breakdowns reconcile to the campaign total. Unpriced calls display **Unknown**, and the known subtotal excludes them. Shared preparation and Firecrawl USD costs are not included.

## Brief measurement summary

| Supplied brief | Tokens | Cost | Cache hit rate | Status |
| --- | --- | --- | --- | --- |
| Brief 1: Leashgate GA launch | 11,386,299 reported; one call has unknown usage | $3.06979200 known subtotal; 30 calls unpriced | 12.13% of reported input | Eleven drafts need review; user edit unmeasured |
| Brief 2: Q1 awareness push | 2,998,637 total | $0.79163360 known subtotal; 17 review calls unpriced | 0.30% | Selected run; five drafts need review; user edit unmeasured |
| Brief 3: Regulated teams push | 4,179,238 total | $1.14817800 known subtotal; 14 review calls unpriced | 4.98% | Four drafts need review; user edit unmeasured |

## Calculation and evidence

Source: read-only snapshot of `data/magi.db`, using `campaigns`, `llm_runs`, and `research_runs`; campaign selection was checked against the linked app's campaign API. Run selection uses `llm_runs.context.campaign_id = '09de5fdf-17ff-4dfb-babc-e37e97121c23'`. This campaign's retrieval-planning run is directly linked by that field. The first recorded call started at 2026-11-05 03:10:28.719864 UTC; the last started at 2026-11-05 03:31:48.105701 UTC.

- Cache hit rate = sum of cached input tokens / sum of input tokens, weighted by tokens rather than averaging per-call ratios.
- All 43 selected calls report input, output, and cached-token counts. Unknown pricing does not prevent reporting their token usage or caching.
- Known estimates use each run's persisted estimate, rather than repricing historical calls at today's rates. The local `gpt-4.1-mini` snapshot uses $0.40 per million uncached input tokens, $0.10 per million cached input tokens, and $1.60 per million output tokens; these are stored application rates, not a newly verified provider quote.
- Formula for that snapshot: `((input - cached) * 0.40 + cached * 0.10 + output * 1.60) / 1,000,000` USD.
- Unknown cost is never replaced with zero. Figures are estimates, not provider invoices. Prompt cache keys and reusable prefixes do not guarantee cache hits; the table reports actual recorded cached tokens.

Implementation references: [cost estimation](backend/app/llm/costs.py), [run schema](backend/app/models/run.py), and [prompt caching notes](docs/prompt-caching.md).
