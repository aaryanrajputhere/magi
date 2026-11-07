# Campaign c3378804 recovery

The initial 31 recorded requests measured 39.04% cached input (222,838 /
570,819 tokens). Cache reuse worked; the generation failure was an application
citation-membership rejection rather than a cache or provider error.

The blog writer returned a hybrid ID:
`7ebb4833-c358-4e50-aa8a-47e4ee921cb3`. It combines parts of valid supplied IDs
but identifies no supplied passage. The writer response parsed successfully,
then Python correctly rejected it before saving or content review. The draft
also needed length/content review; correcting citations alone must not bypass it.

The approved linkedin-1 hook contains 7.4 hours, but its saved body omitted it.
The router did not protect the exact approved-claim passage, and validation did
not independently enforce factual hook fulfillment.

Changes:

- New shared-evidence writer requests bind source_ids to exact supplied chunk IDs
  through the schema update after the cached source blocks. Fixed base schemas
  and existing membership checks remain intact.
- Existing malformed citation results get at most one citation-only correction
  against an enum of allowed IDs. No draft fields can be rewritten by that
  correction. Its run ID and original writer run remain linked. A subsequent
  full content review is mandatory; uncorrectable IDs still fail closed.
- Numeric approved hook facts must occur in the actual body with their units.
  Metadata alone, a different statistic, or a different unit cannot satisfy it.
  Semantic meaning and citations are still reviewed against originals.
- Router version quality-v4 preserves the exact supporting approved claim from
  the existing selected pool for both writing and validation. Examples and
  number-only matches are not treated as factual support. Missing support becomes
  a publication blocker rather than an instruction to invent a number.
- Editorial revisions keep their explicit user instructions; the new mechanical
  numeric-hook requirement applies to ordinary generation, not editorial overrides.

144 focused regression tests passed. Targeted recovery uses the recorded failed
blog response and citation correction instead of regenerating all saved drafts.
The campaign is leased during recovery, a before-state backup is retained in the
application data volume, and non-target drafts are preserved. Remaining publication
and quality findings remain needs_review; generating a draft is not publication
approval.

## Recovery result

Targeted recovery succeeded: all five assets are saved, the generation error is
cleared, and the campaign is needs_review rather than generation_error. The blog
is 960 words, has valid source IDs, retains the approved statistic, and passed its
content validation. Citation-only correction run:
`04a14f87-12cc-495e-9742-2b06748e5ba6`.

LinkedIn 1 now opens with the 7.4-hour claim and its full report citation and passed
content validation. Publication blockers remain explicit: destination URLs,
citation/link placement decision, date/claim-age confirmation and image review.
The blog also retains byline, hero-image and human-review requirements. These were
not waived to call the recovery successful.

Compared with the recovery before-state backup, linkedin-2, linkedin-3,
landing-page-1 and the approved plans are byte-equivalent as decoded JSON objects.
The initial 39.04% cache figure excludes the targeted recovery requests and should
not be compared directly with a refreshed inspector total that includes them.
