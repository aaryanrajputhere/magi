# Decisions behind Magi

I built Magi to turn a marketer's brief into brand-aware campaign drafts. I chose retrievable brand evidence, editable memory and explicit approval before generation so the marketer can understand and control the output. The hardest part was conflict resolution across retrieved documents. The AI coding tool initially got that architecture wrong, and I had to redirect it. I improved parts of the workflow, but cost and latency are still the first areas I would work on next.

## Brand layer: retrieve evidence, preserve its origin

The brand layer imports the supplied Markdown documents into SQLite, retaining original text, relative paths and content hashes. Section-aware chunks retain headings and line ranges so retrieved guidance can be checked against its source. Images and briefs remain separate inputs: an image catalog supports asset selection, while a campaign brief expresses the requested work rather than becoming permanent brand policy.

Retrieval combines keyword search and semantic embeddings using reciprocal-rank fusion. Keywords help find exact product names and prescribed phrases; embeddings help when a brief describes the same idea differently. Fusion avoids having to calibrate two unrelated score scales. SQLite keeps the prototype's persistence and retrieval infrastructure local and straightforward to run.

I chose retrieval over putting the entire folder into every prompt to reduce repeated context and make source selection visible. Campaign-specific questions retrieve evidence, which is reviewed and compacted into topic and channel contexts for writing. Original passages and references remain available in saved artifacts. Compaction is a writing aid; it must not silently decide which conflicting rule wins.

This trades completeness for bounded work. A successful search does not establish that all relevant guidance was found. Whole-document section expansion can also make review expensive. The Sources view and process inspector expose what entered the pipeline, but they cannot prove retrieval recall. The architecture therefore needs evaluation of missed evidence as well as draft quality.

A concrete gap in the recorded review was that both X guidance searches returned product and messaging passages without retrieving the X playbook. Search completion was therefore insufficient evidence of channel coverage. I would add an explicit coverage check against the document inventory: identify applicable channel guides, retrieve their governing sections when ranking misses them, and report missing or ambiguous guidance. This should work across brands rather than depend on hardcoded sample filenames. Context should include relevant scope and exceptions without expanding every matched document indiscriminately.

## Memory: small, editable editorial preferences

Memory persists across campaigns for the same brand. It stores editorial preferences, such as a channel-specific style preference, separately from source documents, campaign history and product facts. Current instructions and source restrictions take priority. A temporary exception for one post should not rewrite the brand's lasting preferences.

Users can add, edit and forget entries directly. Chat-based extraction proposes lasting preferences from the latest user message and must supply an exact supporting quotation. The application checks that quotation, filters campaign-only intent and validates references to existing entries. When unsure, the extractor is instructed to save nothing. This deliberately favors missing a preference over accumulating inferred instructions that the user never intended.

The limits—50 entries per brand and 500 characters per entry—keep memory reviewable and prompt overhead bounded. Revision checks prevent an extraction from silently overwriting a concurrent edit. Stored events record before/after values and originating references. Forgetting removes an active preference; historical chats and model requests still retain their original contents. Event history is stored but not yet exposed in the memory panel.

This is a modest preference system, not an unrestricted autobiographical memory. It offers useful continuity without treating every correction, claim or generated sentence as something to remember.

## Conflicting documents: compare scope before choosing a winner

Contradictions can occur between documents or between a requested campaign decision and a source rule. The campaign review pools retrieved evidence across queries, extracts campaign decisions and source rules, and compares their applicability. Rules need their conditions and exceptions: a product-page image rule may differ from a role-page rule, and permission to describe a product as generally available does not grant permission to ignore imagery or posting restrictions.

An independent challenge checks the review against original evidence for omissions and incorrect interpretations. Supplemental evidence also needs review. Invalid references or incomplete batches cannot establish readiness, and the resulting plan is verified before approval. A changed plan requires fresh approval before generation.

I avoid treating retrieval rank or a newer-looking filename as sufficient authority. Source authority inspection is a separate workflow; campaign review does not require a confirmed authority map. Where confirmed precedence exists, the resolver can use it conservatively. Without adequate evidence or clear scope, a material issue should remain unresolved and become a focused clarification question. A user answer is a recorded campaign decision, not evidence that a source restriction never existed.

The guarantee is limited: the system reviews retrieved evidence using model judgments. It cannot certify that no relevant contradiction exists elsewhere in the corpus, or that every apparently supported interpretation is correct.

## Where AI led the work wrong

The AI coding tool initially steered conflict resolution toward separate checks over individual retrieval groups. That missed conflicts when the requested action and the governing restriction appeared in different groups. I had to redirect the design toward a shared campaign-wide evidence pool and a comparison that can consider rules across retrievals. Extraction can still be batched, but those batches must not become isolated boundaries for conflict reasoning.

It also defaulted to GPT-4.1-mini across operations instead of distinguishing routine writing from harder conflict analysis. I wanted a more capable model for conflict review, with that choice configured separately. The current implementation has separate writing and review model settings; the quality and cost tradeoff still needs measured evaluation.

The tool also introduced unnecessary operations, ran some independent calls sequentially, and passed more data to the model than the task needed. I had to review and tighten the supplied context myself. These choices increased latency and token overhead without a clear benefit. Context selection and execution efficiency remain areas I want to improve: pass the evidence each stage needs, preserve governing scope and exceptions, and parallelize independent work with bounded concurrency.

## What I left out, and why

The main unfinished work is systematic cost and latency optimization. I focused on getting the campaign workflow, retrieval and conflict handling working, and improved some inefficient paths along the way. I did not complete a benchmark pipeline to establish which model and execution strategy work best for each stage. The current model settings are implementation choices, not the outcome of a controlled comparison.

Some calls still receive more context than they need, some operations could be removed or combined, and independent work could run in parallel. I have not measured those changes consistently across representative campaigns, so I cannot yet quantify the savings or their effect on quality. Request logging provides the raw material for that evaluation, but it is not a substitute for a repeatable benchmark.

I also left out publishing, scheduling and broader platform integrations to keep the assessment focused on creating and editing drafts. With more time, I would prioritize measuring and improving the existing pipeline's cost, speed and reliability before adding those capabilities.

## What I would do with two more weeks

**Week one: build a repeatable benchmark pipeline.** Create a versioned evaluation set from the three supplied briefs, an unseen brief, typical edits, known conflicts and compatible controls. Record expected outcomes and supporting evidence. Run the same cases against different models for planning, conflict review, writing and memory extraction. Hold prompts, source snapshots and generation settings consistent within each comparison, and save the model and configuration with every result.

The benchmark would capture per-stage and end-to-end latency, input/output tokens, cached tokens, estimated cost, failures and retries. It would report unpriced steps as unknown and track external research costs separately. Repeated runs and separate cold-cache and warm-cache results would help distinguish stable improvements from one-off variation. Quality checks would cover conflict recall and false positives, citation validity, instruction adherence, draft quality and whether an edit changes only its intended asset. Writing quality would include human review rather than relying only on another model's score.

**Week two: optimize against that baseline.** Compare models by task and choose the lowest-cost configuration that meets the quality requirements within acceptable latency. Test smaller evidence contexts, fewer calls, reuse of unchanged extraction and bounded parallel execution as separate changes so their effects are visible. Keep conflict reasoning connected across retrieval groups and verify that trimming context preserves governing rules and exceptions. Include research-enabled cases to measure whether the additional evidence justifies the extra time and cost.

Produce a comparison report showing quality, cost and latency together, including regressions and tradeoffs. Make the benchmark rerunnable after prompt, retrieval or model changes so future optimization is based on evidence. The goal is a faster, more cost-efficient campaign workflow with measured quality, rather than choosing one model for every operation or assuming that more calls improve the result.
