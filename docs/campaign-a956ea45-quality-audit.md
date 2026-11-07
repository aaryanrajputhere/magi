# Campaign a956ea45: request-by-request quality audit

Read-only review of all 39 recorded requests, including one embedding request. Original source text and saved campaign were not modified.

| # | Operation | Input tokens | Output tokens | Input reviewed | Output reviewed | Finding |
|---|---|---:|---:|---|---|---|
| 1 | `campaign_memory` | 483 | 596 | Brief + memories | Three campaign memories | Useful intent preservation; no quality blocker. |
| 2 | `campaign_agent_step` | 1875 | 19 | Brief, state, conversation | extract_requirements | Routing is correct; brief repeats in conversation. |
| 3 | `campaign_requirements` | 1531 | 3033 | Brief and decisions | Five proposed assets | Retains awareness objective; LI-03 purpose is too vague to differentiate its copy. |
| 4 | `campaign_agent_step` | 3918 | 18 | Growing tool history | retrieve_context | Correct transition; no need to replay expanded requirements solely for routing. |
| 5 | `plan_retrieval` | 2544 | 850 | Requirements | Five search questions | Reasonable questions; separate factual unknowns from optional planning choices. |
| 6 | `campaign_evidence_gap` | 4183 | 611 | Requirements, coverage, focused originals | Two supplemental searches | Correctly distinguishes product support from company boilerplate; unconfirmed audience is a planning choice, not necessarily another search. |
| 7 | `campaign_conflict_find` | 92823 | 1134 | Brief + 344,927 characters of passages | No conflicts, limitations | Broad original corpus; keep source review independent from finished-copy validation. |
| 8 | `campaign_conflict_check` | 93072 | 1942 | Same passages + review | No conflicts, authority/expiry limitations | Useful source precedence, but 93k input tokens; preserve treatments downstream. |
| 9 | `campaign_agent_step` | 6027 | 126 | Preparation history | propose_plan | Instruction stresses brief lacks facts despite retrieved evidence; explicitly distinguish user omissions from available sources. |
| 10 | `campaign_research_plan` | 2035 | 119 | Full requirements | No external searches | Reasonable decision; pass verified identity rather than repeating missing-brief facts. |
| 11 | `campaign_plan` | 75590 | 3228 | Approved preparation context | Five-asset plan | Vague CTAs and landing hero trigger repairs. Strengthen roles and distinguish label from target. |
| 12 | `campaign_plan_verification` | 79663 | 2681 | Plan + original rules | Four defects | Useful CTA-destination and hero checks; failed to enforce the CTA word bound while recommending new wording. |
| 13 | `campaign_plan_coverage_repair` | 13004 | 703 | Defects + source sections | Three identical 5-word CTAs and new hero | Introduces CTA labels over the 4-word rule. Repair needs all applicable CTA dependencies, not destination guidance alone. |
| 14 | `campaign_plan_verification` | 12206 | 1713 | Changed assets + sources | Landing audience defect | Valid request to identify the selected audience. |
| 15 | `campaign_plan_coverage_repair` | 6342 | 415 | Landing hero + source sections | Platform-engineer hero | Good precise repair; preserves mode qualification. |
| 16 | `campaign_plan_verification` | 6023 | 325 | Landing-only context | No defects | Checks only hero; earlier invalid CTA labels remain undetected. |
| 17 | `route_asset_context` | 60325 | 2464 | Deduplicated sources + approved plan | Five routes and dependencies | Useful source reduction; dependencies are prose so image selection cannot reliably resolve them later. Use typed dependencies and coverage for exact asset obligations. |
| 18 | `generate_campaign_asset` | 12375 | 1532 | LI-03 route + outline + sibling purposes | Generic containment statement | Underfulfills distinct asset role; starts from vague approved purpose. Needs a reader benefit, not more product-policy recitation. |
| 19 | `generate_campaign_asset` | 13415 | 1503 | LI-02 route + outline | Statistic + explanation + long CTA | Good initial statistic citation, but survey base missing and CTA too long. |
| 20 | `generate_campaign_asset` | 11196 | 2412 | LI-01 route + outline | Product introduction | Unqualified containment sentence and long CTA. Generate explicit mode qualifiers from selected support. |
| 21 | `campaign_asset_validation` | 12417 | 1745 | LI-02 original rules + draft | CTA/base defects + production dependencies | Mostly actionable; image and URL dependencies correctly separated. |
| 22 | `campaign_asset_validation` | 11608 | 2284 | LI-03 original rules + draft | Concrete-action/CTA requests | Formatting is actionable. A list of allowed product actions alone does not prove every post must list an action; distinguish editorial advice from binding requirements. |
| 23 | `campaign_asset_validation` | 11250 | 4309 | LI-01 rules + draft | Positioning, modes, naming, CTA defects | Most useful; second-use short name should not be treated as a mandatory violation without supporting imperative wording. |
| 24 | `campaign_asset_repair` | 4406 | 1036 | LI-02 focused repair context | Statistic-only post with changed citation | Regression: inserts survey base inside fixed citation and deletes explanatory paragraph. Require preservation of compliant spans and named facts. |
| 25 | `campaign_asset_validation` | 12366 | 2463 | LI-02 recheck | Citation-format and CTA defects | Correct citation finding. A second CTA rewrite is avoidable with structured target + validated label. |
| 26 | `campaign_asset_repair` | 3757 | 531 | LI-03 focused repair context | Specific action + mode qualification | Fixes factual specificity but risks duplicating LI-01; maintain distinct asset role. |
| 27 | `campaign_asset_validation` | 11601 | 624 | LI-03 recheck | Production-only findings | Text passes; missing destination stays legitimate. Image warning must be resolved after selection. |
| 28 | `generate_campaign_asset` | 17892 | 1602 | Blog routed context | 685-word initial blog | Real length failure, missing survey base, repetition and policy-like prose. Plan sections and useful detail before writing. |
| 29 | `campaign_asset_repair` | 5853 | 1294 | LI-01 repair | Mode-qualified introduction + shorter CTA | Substantive improvement; preserve it. No need for further full-copy repair if only production items remain. |
| 30 | `generate_campaign_asset` | 14751 | 1054 | Landing routed context | Clear modes, limitations, bottom CTA | Initial copy correctly includes observe-mode recording; hero CTA placement missing. |
| 31 | `campaign_asset_validation` | 16787 | 2805 | Blog validation | Length/base/CTA/mode defects + dependencies | Length and citation qualifications are real; deduplicate code and model word-count findings. |
| 32 | `campaign_asset_validation` | 13596 | 1629 | Landing validation | Repeat primary CTA in hero | Valid focused layout request; unknown article URL remains a dependency. |
| 33 | `campaign_asset_validation` | 11260 | 1945 | LI-01 recheck | Production-only output | One quote does not match its source, causing Review incomplete. Select source spans in code, rather than trusting regenerated quotations. |
| 34 | `campaign_asset_repair` | 4701 | 1728 | Landing repair | CTA duplicated; observe-mode sentence removed | Regression: the repair deletes correct unrelated copy. Apply a minimal insertion/field patch or verify protected spans. |
| 35 | `campaign_asset_repair` | 11635 | 2757 | Blog repair | Expanded ~935-word blog | Length and survey base improve; excessive repetition and meta-writing discussion reduce reader value. Preserve structure and add practical supported substance. |
| 36 | `campaign_asset_validation` | 13584 | 2089 | Landing recheck | Restore observe recording + URL dependency | Recording defect was introduced by repair. URL source quotation mismatch is a grounding failure, not a copy defect. |
| 37 | `campaign_asset_validation` | 17056 | 2709 | Blog recheck | Length, recap, byline/URL/date findings | Length complaint conflicts with code (935 words). Recap concern is editorially valid but quotation mismatches make it ungrounded under current rules. Preserve exact spans; code owns numeric checks. |
| 38 | `embed_image_queries` | 6923 | 0 | Asset concepts + image catalog embeddings | Image-query vectors | Retrieval step; not a text-generation LLM call. |
| 39 | `select_campaign_images` | 18753 | 1674 | Visual packets + candidates + drafts | Five images and alt text | Selection completes, but warning reconciliation is absent. Attachment is not rights approval: check actual selected record and resolve only satisfied dependencies. |

## Completion priorities

1. Make code authoritative for measured numeric constraints; reject unsupported verifier length assertions.
2. Preserve compliant content during minimal repairs, including citation format and observe-mode wording.
3. Ground findings using source IDs and exact spans; recheck invalid references/quotes without treating them as proven copy defects.
4. Track typed production dependencies with resolved/unresolved state and evidence, reconcile after image selection.
5. Show text validation and publication readiness separately. Unknown URLs and bylines must not be invented.
6. Review asset differentiation and audience usefulness; a passing compliance check is not an editorial quality assessment.

Final blog word count using the application algorithm: 935. The later model length complaint is incorrect under that algorithm. This is not a claim that the blog is free of other violations.
