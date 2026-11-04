# Magi workspace design

## Direction and research status

Updated 2026-11-04 from the public [Magi website](https://www.magihq.com/), including its delivered HTML and CSS, and the product, solution, pricing and customer pages linked below. This is a design direction for the campaign workspace, not a replica of the marketing website or a specification of Magi's private application.

The reference combines editorial typography with a precise, structured interface: white and warm ivory surfaces, near-black text and actions, thin grey rules, compact monospace labels, and selective vivid pink accents. Replace the earlier violet-first direction with this neutral-and-pink system. Green remains a semantic success color rather than the application's overall identity.

Evidence levels used in this document:

- **Observed:** present in public page markup or published styles. CSS values are source evidence, not measurements of rendered pixels.
- **Recommended:** an adaptation for this project's denser, interactive workspace.
- **Unverified:** rendered desktop/mobile appearance, hover behavior, animation timing in practice, and keyboard operation. No browser was connected during this research; do not treat source inspection as visual QA.

This revision changes the design specification only. The current `frontend/src/styles.css` still contains a dark green sidebar and green-toned surfaces; it does not yet implement this direction.

## Reference research

### Visual system observed in source

The homepage uses a centered hero, a small boxed category label, a large light-weight sans-serif heading, a contrasting serif introduction, and paired filled/outlined calls to action. Later sections use left-aligned headings, bordered grids, outcome cards, illustrations and numbered agent descriptions. This creates hierarchy through type, space and rules rather than pervasive color.

Published palette values include canvas `#FAF9F5`, white `#FFFFFF`, near-black `#111111`, secondary text `#4B4B4B`, muted grey `#7C7C7C`, border grey `#B8B8B8`, and line grey `#CBCBCB`. The brand theme maps primary actions to near-black and its accent to pink `#FF70FF`, with softer pink `#FFA3FF` and light pink `#FFCCFF`. Additional badge hues exist, but should not become competing workspace themes.

The site's font declarations identify **DM Sans** for body/display, **DM Mono** for technical labels, and **LT Remark** for editorial remarks. The hero markup uses sans-serif at weight 300; supporting hero copy uses the remark family. This supersedes the earlier assumption that the main welcome heading should be Georgia.

The source defines an 80px/80px hero with -3.2px tracking at its base size, reducing to 66px below 1026px and 48px below 774px; a 96px variant exists at 1536px and above. Body type is generally 16px/21px, small body 14px, and navigation 14px. These are marketing-page sizes, not workspace defaults.

Layout tokens use 1140px content width, a 1100px home measure, 696px prose, fluid 20–50px gutters and generous section spacing. A 1400px content variant appears at very wide sizes. The hero's buttons explicitly use 44px height and 6px corners. The theme also defines a 40px global radius, so corner treatment is component-specific rather than uniformly rounded.

Category labels combine a small bordered square containing a colored dot with an adjoining outlined, uppercase monospace label. Logo grids share thin dividing lines. Homepage use-case markup offers channel tabs, pricing exposes segmented choices and a monthly/annual toggle, and agent/process sections use ordinal labels. These establish useful component patterns; their live interactions remain unverified.

Motion tokens are 120ms, 200ms and 320ms with `cubic-bezier(.16, 1, .3, 1)`. Reduced-motion rules set those durations to zero. Buttons declare hover, focus-visible, disabled and pressed styles. The presence of these rules does not establish that every interaction is accessible.

### Content and product patterns

The public pages repeatedly connect company context to guided production and human review. Their implications for this project are:

| Reference | Observed pattern | Workspace adaptation |
| --- | --- | --- |
| [Homepage](https://www.magihq.com/) | BrandOS connects context, agents, governance and output; sections pair short explanations with structured visuals. | Keep the brief, relevant context, execution state and asset preview legible as related parts of one workflow. |
| [Ideation Agent](https://www.magihq.com/product/ideation-agent) | Ideas carry source excerpts, relevance and audience context; users choose what becomes a draft. | Make evidence and scope available beside generated work; label review and generation states explicitly. |
| [Social solution](https://www.magihq.com/solutions/social) | Channel workflows distinguish individual voices and campaign context. | Keep brand, audience, channel and voice visible where they affect an asset. |
| [Pricing](https://www.magihq.com/pricing) | Segmented controls, comparable plans and explanatory detail support decisions. | Use tabs and clear field groups for real choices; keep advanced settings progressively disclosed. |
| [SciFin customer story](https://www.magihq.com/customers/scifin) | Structured challenge/approach/outcome storytelling and attributed results. | Present actual campaign results with labels and context; use this page's structure rather than importing its customer claims. |

These are design inputs, not a commitment to implement the public site's integrations, publishing, analytics, billing or agent capabilities. Only expose actions supported by this project.

## Workspace principles

1. **Make the next action clear.** One primary action per task region; secondary actions use an outline or quiet text treatment.
2. **Let content lead.** Use restrained chrome, readable copy, consistent alignment and useful whitespace.
3. **Keep context close.** Brand, brief, sources and relevant rules should be inspectable without losing the draft.
4. **Make human judgment explicit.** Generated, candidate, selected and approved are distinct states. Never infer authority from visual polish.
5. **Show real progress and outcomes.** Empty, running, failed, stale and complete states need precise language tied to actual data.

## Application tokens — recommended

These names are the application contract. Values derived from the reference are supplemented by workspace-specific interaction and accessibility choices.

| Role | CSS variable | Value | Use |
| --- | --- | --- | --- |
| Page | `--page` | `#FAF9F5` | Warm workspace canvas |
| Panel | `--surface` | `#FFFFFF` | Editing and inspection surfaces |
| Subtle surface | `--surface-subtle` | `#F9F9F9` | Quiet toolbars and grouped fields |
| Text | `--text` | `#111111` | Headings, labels and body |
| Secondary text | `--muted` | `#4B4B4B` | Descriptions and readable metadata |
| Decorative rule | `--border` | `#CBCBCB` | Panel separators and grid lines |
| Control outline | `--control-border` | `#7C7C7C` | Input boundaries where needed for contrast |
| Primary action | `--primary` | `#111111` | Filled action buttons |
| Primary hover | `--primary-hover` | `#333333` | Hover on primary actions |
| On primary | `--on-primary` | `#FFFFFF` | Text on filled actions |
| Brand accent | `--accent` | `#FF70FF` | Dots, small markers and selected details |
| Accent surface | `--accent-surface` | `#FFCCFF` | Selected navigation and evidence highlights |
| Focus | `--focus` | `#111111` | Keyboard outline on light surfaces |
| Success | `--success` | `#28734F` | Confirmed success with text/icon |
| Error | `--error` | `#B33A35` | Errors with text and recovery |
| Warning | `--warning` | `#805500` | Stale or attention states with text/icon |

Use near-black text on pink backgrounds. Pink is a brand marker and selection fill; do not use it for small text on white or as the only focus/error indicator. The reference's muted `#7C7C7C` is retained for control outlines, not normal small body copy. Verify contrast for every actual foreground/background pair; target 4.5:1 for normal text and 3:1 for large text and meaningful UI boundaries.

## Typography — recommended

Use a sans-serif display heading, a readable sans-serif interface, and monospace for brief section labels, step numbers and source line numbers. A serif introduction can add editorial contrast on the welcome view, but should not spread into forms, tables or technical inspectors.

| Role | Desktop | Mobile | Treatment |
| --- | --- | --- | --- |
| Welcome heading | 40px/44px | 30px/34px | Sans-serif, weight 300–400, -0.03em tracking |
| Page heading | 32px/38px | 26px/32px | Sans-serif, weight 400 |
| Section heading | 20px/26px | 20px/26px | Sans-serif, weight 500 |
| Introductory remark | 18px/26px | 17px/25px | Optional Georgia fallback, weight 400 |
| Body and inputs | 14px/21px | 16px/24px inputs | Sans-serif, weight 400 |
| Labels and navigation | 13px/18px | 14px/20px | Sans-serif, weight 500 |
| Metadata and eyebrow | 12px/16px | 12px/16px | Monospace for eyebrow; uppercase sparingly |

Keep the existing no-downloaded-fonts constraint: use `ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`, `ui-monospace, SFMono-Regular, Consolas, monospace`, and `Georgia, serif` for optional remarks. DM Sans/DM Mono are reference identities, not a requirement to fetch assets. Do not copy LT Remark from the website; use the fallback unless appropriately licensed assets are supplied. Avoid fixed-height text containers and allow long labels to wrap.

## Layout and spacing — recommended

Use the 4/8/12/16/24/32/48/64px scale. Apply 24–32px within major panels, 16px between related controls, 8px between a label and its input, and 32–48px between task regions. Workspace gutters should scale from 16px on small screens to 24–40px on desktop. Keep dense inspector rows compact without shrinking their text.

Desktop shell: a restrained light navigation rail, compact top bar with brand context and connection status, a short welcome/task heading, then chat and preview panels of equal width. Prefer white or ivory navigation with near-black text and pale pink selection. Use a 1px divider to separate navigation from the main canvas. Keep large black fields for occasional emphasis rather than the entire persistent sidebar.

The main workspace may grow to roughly 1400px where the viewport allows it; source reading and long prose should stay near a 65–75-character measure. Use `min-width: 0` for grid/flex children and wrap long source names, hashes and URLs.

| Width | Required behavior |
| --- | --- |
| 1000px and above | Rail visible; chat and preview side by side when usable widths permit |
| Below 1000px | Chat above preview; preserve brief and generation state |
| Below 850px in Sources | Document list above inspector |
| Below 640px | Hide desktop rail; show compact brand header and usable mobile workspace navigation; use one-column forms |

These breakpoints are project decisions retained from the earlier specification, not measurements of the reference. Collapse earlier if content overflows. Avoid page-level horizontal scrolling; local scrolling is allowed for code, source tables and tab strips with clear affordances.

## Components and interaction — recommended

- **Buttons:** Near-black primary, outlined secondary, and text tertiary. Default height 44px with 6–8px corners. Pair icon-only controls with accessible names. Use a dark hover treatment, visible focus and a subtle pressed state. Disable duplicate submissions while busy and retain the action label alongside progress text.
- **Panels:** White on ivory, 1px grey border, 12px corners and little or no shadow. Use separators instead of nested cards wherever grouping is already clear. Reserve larger corner radii for occasional editorial illustrations, not every control.
- **Eyebrows:** Adapt the bordered dot-and-label motif at a modest size for page context or workflow stages. Decorative dots are hidden from assistive technology; status dots require adjacent text.
- **Navigation:** Pale pink selected surface with near-black text and a persistent selected indicator. Hover, keyboard focus and selection must remain visually distinct. Identify the current destination programmatically.
- **Tabs:** Use a clear active underline or contrasting surface; keep text readable and selection independent of hover. Preserve arrow-key navigation, associated tab panels and focus behavior. Permit local horizontal scrolling on narrow screens.
- **Fields:** Visible labels, meaningful helper text, accessible error association and clear control boundaries. Unimplemented inputs remain disabled with explanatory copy. Do not replace labels with placeholders.
- **Empty states:** Explain what will appear and the supported next step. Never show decorative sample content as if it were generated or real campaign data.
- **Evidence:** Dark text on pale pink highlighting, with source attribution and a visible selected range. Preserve readability when several lines are highlighted.
- **Metrics:** Pair a number with its unit, period, attribution and actual data state. Do not transplant reference customer results into this workspace.

Use 120–200ms transitions for hover and selection, with up to 320ms for deliberate panel transitions. Animate opacity or transforms rather than layout where practical. Respect `prefers-reduced-motion`; remove decorative motion and keep state changes immediate and understandable. No animation may delay access to output or obscure failure states.

## Connection and workflow states

Connection states remain checking, connected and unavailable. Always pair indicator color with text. Errors expose a working retry button. Preserve the existing health request, timeout and cleanup behavior.

Keep connection status separate from campaign generation status. Show pending, running, complete and failed states only when supported by actual workflow data. Retain the user's input on failure and offer a relevant recovery action. Describe unavailable capabilities honestly; website claims do not establish functionality in this project.

## Source browser and policy inspection

Sources uses the shared application navigation and tokens. A brand selector precedes the document list and inspector. Stack the list above the inspector below 850px; show mobile workspace navigation below 640px.

Render source content as escaped text with line numbers. Use pale pink evidence highlighting in place of the earlier violet treatment. Keep four separate Document, Chunks, Metadata and Policies tabs. Technical IDs, hashes and model/run/version information belong in collapsible details.

Keep candidate labels explicit. Missing derived data is different from a completed empty extraction. Stale data remains inspectable but cannot jump to source lines. All inspection is read-only; this view exposes no ingestion or extraction actions.

The Policies tab separates source-grounded candidates from source metadata. Present rule scope, dates, conditions and exceptions explicitly; label null scope "Not stated". Evidence jumps use backend-computed line ranges and are disabled for stale batches. Candidate labels must never imply approval or authority. Four-tab navigation can scroll horizontally on narrow screens and must retain the existing arrow-key flow.

## Accessibility and customer brand boundary

Keep semantic headings, field labels, status and alert announcements. All interactive controls require visible keyboard focus: use a 2px near-black outline with an offset on light surfaces and a white outline on dark surfaces. Never communicate status through color alone. Preserve readable text, wrapping labels, 200% zoom usability and responsive layouts. Disabled controls must remain visibly disabled; essential explanations must not depend on hovering a disabled element.

Application tokens style the workspace shell only. Generated social posts, email, blog and landing-page previews must use the selected customer's brand rules inside their preview boundaries. Neither Magi pink nor the former violet/green shell palettes should leak into customer assets by default. Preserve a clear visual boundary between workspace tools and the rendered customer asset.

## Validation and implementation handoff

For a future implementation of this direction:

1. Consolidate shell colors, typography and spacing into shared tokens before migrating components. Replace hardcoded shell colors without replacing customer preview styles.
2. Apply the shell, navigation, heading and button hierarchy, then update campaign panels and inspectors consistently.
3. Preserve existing backend APIs, database behavior, campaign functionality, source semantics and health-check behavior.
4. Run `npm --prefix frontend run build` after frontend changes.
5. With a connected browser, inspect 1440px, 1024px, 768px and 390px layouts; verify keyboard focus, mobile navigation, tab arrow keys, disabled controls, long text, 200% zoom, reduced motion and each connection/workflow state.
6. Check text/control contrast, customer brand isolation, stale evidence restrictions and retry behavior before describing the redesign as verified.

This documentation-only revision does not constitute an implemented or visually tested redesign. No frontend build is required for the Markdown change itself.

## Research sources and traceability

Reviewed 2026-11-04. Public pages may change; hashed stylesheet URLs are supporting evidence for this review, not stable application dependencies.

- [Magi homepage](https://www.magihq.com/) — section composition, action hierarchy, brand narrative and rendered-element markup.
- [Ideation Agent](https://www.magihq.com/product/ideation-agent) — source traceability, scoped context and human-directed drafting.
- [Social solution](https://www.magihq.com/solutions/social) — voice, channel and campaign organization.
- [Pricing](https://www.magihq.com/pricing) — segmented choices, comparison structure and progressive explanation.
- [SciFin story](https://www.magihq.com/customers/scifin) — outcome-oriented information structure and customer-specific branding.
- [Font stylesheet](https://www.magihq.com/_next/static/immutable/chunks/2w5v4jibl6xqw.css) — DM Sans and DM Mono declarations.
- [Application/theme stylesheet](https://www.magihq.com/_next/static/immutable/chunks/1zxrw66r961kf.css) and [additional stylesheet](https://www.magihq.com/_next/static/immutable/chunks/2slqrqire_kmj.css) — published palette, semantic mappings, typography, breakpoints, component utilities and motion rules, read together with homepage classes and inline styles.
