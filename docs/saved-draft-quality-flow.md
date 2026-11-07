# Saved draft quality and publication checks

The generation pipeline retains the original approved plan and original brand text.
One context route selects the original passages for writing, validation and images.

## Validation and minimal repair

Validation cites a server-created source paragraph reference. The server reconstructs
the quotation and checks the source ID; regenerated quote punctuation cannot cause
false grounding failures. One failed-grounding retry is permitted, without repairing
copy from an invalid finding. Numeric limits measured by code remain authoritative.
CTA bounds are recognized only from explicit original numeric wording; unknown CTA
forms remain subject to semantic review rather than guessed numerical rules.

Repairs return exact text anchors, ranges, replacements and the current content hash.
Only validated, non-overlapping patches are applied. If an offset is miscounted, a
unique exact anchor on the same hash can resolve it. Stale hashes and ambiguous
anchors retain the original draft. No-op repairs stop. There are at most two repair
rounds; histories record the base hashes, patch text and model run IDs.

## Publication dependencies

Typed records separate destination URL, author, publication date, image attachment,
image review, permission, missing evidence and legacy items. The record keeps its
source reference, resolution status and evidence. Duplicate requirements are grouped.
A current attachment resolves missing attachment only; rights and placement can
remain pending. A supplied URL resolves its dependency only after it appears in copy.
Old warning strings are read conservatively, never treated as confirmed approvals.

The asset preview displays text validation separately from publication readiness.
The Publication details editor accepts user-confirmed URLs, author/byline and dates.
Updating details requires rechecking; it never edits the approved campaign plan.

## Existing drafts

POST /api/campaigns/{id}/recheck-drafts accepts version and optional asset_ids.
It checkpoints each result, preserves images, isolates per-draft failures and returns
the full saved campaign. The UI offers whole-campaign and single-draft actions.
PUT /api/campaigns/{id}/drafts/{asset}/publication accepts version and confirmed
publication details. Version checks reject stale changes. Active rechecks block edits.

No existing or generated asset is claimed ready when text checks fail or publication
requirements remain unresolved. Readiness is an application check, not permission to
publish externally. Missing authors, URLs and permissions are never fabricated.

## Saved campaign verification (2026-11-07)

Campaign `a956ea45-53a7-4179-bc9a-c4d1db254611` was rechecked without
regenerating assets. LI-01, LI-02, LI-03 and LP-01 pass text validation.
BLOG-01 retains a failed finding about replacing its closing recap with a practical
next step after the bounded repair rounds. It is not marked compliant.

The final deployed reconciliation made no model calls. Repeated LI-01 destination
warnings collapsed to one requirement. All five saved image attachments and the
complete approved plans compare equal to the pre-recheck snapshot. Publication
requirements remain explicit; no author, destination URL or permission was invented.
The backend and frontend were rebuilt and restarted successfully.

Verification: 45 focused backend regression tests passed, frontend TypeScript
checking passed, and lint passed for the new patch/publication helpers and tests.
This is focused verification, not a claim that the entire repository test suite passes.

Planning clarifications now return structured text, a reason, and actionable
choices instead of a question string. The planning prompt requires inspecting
supplied originals and active answers first, distinguishes quote availability
from clearance, and offers omission or a pending-clearance planning placeholder
only when consistent with explicit requirements. These routes do not establish
publication permission. Missing essential values can still use text questions.

Question cards accept a selected choice with optional details or a custom answer.
The full response is stored as a campaign decision for subsequent planning. The
agent preserves structured choices when publishing the question; legacy saved
string questions remain readable. The plan does not repeat the question form
when a pending chat question already exists. Previously saved text questions are
not retroactively assigned model-generated alternatives.
