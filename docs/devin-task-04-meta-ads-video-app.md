# Devin Task 04 — Build Meta Ads video-to-campaign app (MVP end-to-end)

> **For the Devin Bot (Victor).** Work on branch `devin/meta-video-ads-app` and
> open a pull request — do not push directly to `main`. Build a complete
> end-to-end MVP first (upload several videos → separate analyses and copy →
> review → configure one campaign → publish multiple ads → inspect
> performance), then polish. Do not create any account, do not connect to
> the owner's Meta Ads account, do not submit any application, do not enter
> personal information, and do not buy anything. No API keys, access tokens,
> or secrets in the repo, in browser code, or in logs. Work in Vietnamese
> with the owner when messaging him; keep technical terms in English.
> Briefly state your architecture and assumptions in the PR, then implement.
> Ask questions only when a missing decision prevents meaningful progress.

## Context

The owner is an individual advertiser in Vietnam running Meta Ads with a
**purchase-conversion objective for e-commerce** (hair products, US market).
This task builds on Task 03 (research report in PR #4,
`docs/meta-ads-ai-tools-2026-11-21.md`, and the Package A alerts demo app
in PR #5). PR #4 and PR #5 are still open and unmerged — **do not depend
on them being merged, do not merge them yourself, and do not treat PR #5
as the base**. Read PR #4's report and PR #5's demo only as reference; this
is a new, larger, standalone app.

Build it as a new standalone app inside this repo at
`apps/meta-video-ads/` so it does not touch muse-um core (Relay, Panel,
daemon / muse-client, hooks, `PROTOCOL.md`). Do not change muse-um code
unless you justify it in the PR.

Main workflow: upload one or multiple videos → AI analyzes each video
independently → AI generates unique ad copy for each video → user reviews
and edits → app configures one shared campaign with multiple ads → user
publishes selected ads → dashboard displays performance. Support both
single-video and batch workflows.

Interface in Vietnamese by default, with optional English support. Default
app timezone `Asia/Ho_Chi_Minh`, initial display currency VND. Respect each
connected ad account's actual timezone and currency when configuring
campaigns and reporting results.

## 1. Video uploads and creative library

- Drag-and-drop upload of one or multiple videos at once.
- Video validation and configurable upload limits.
- Per-video upload progress, thumbnail, filename, and preview.
- Queue showing upload and processing status.
- Actions to remove videos, retry failures, and add more videos before
  publishing.
- Persistent creative library with search and filtering.
- Secure media storage and controlled access. Handle long-running
  operations through background jobs with controlled concurrency. One
  failed video must not block the entire batch.

## 2. Independent AI video analysis

Analyze each video independently using a multimodal AI provider that
supports video, or a documented combination of frame analysis and audio
transcription. Extract: product/service and main selling points; spoken
dialogue and visible text; customer problems, benefits, offers, and calls
to action; visual style and opening hook; suggested audience, clearly
identified as an inference; potentially unsupported claims or content
requiring review.

Shared batch context the user can provide: product name and description,
landing page URL, price and promotion, brand voice and content language,
target market and campaign goal. Allow per-video overrides.

Clearly distinguish information observed in the video from user-provided
information and AI assumptions. Do not invent product benefits, discounts,
testimonials, or performance claims. Do not attribute another video's
content to the current video. Save each analysis so users can revise ad
copy without unnecessarily reprocessing the video.

## 3. AI-generated ad content

Generate 3–5 distinct ad-copy variations for each video. Each variation:
primary text, headline, description, recommended call to action, suggested
audience and supporting rationale, creative improvement suggestions (e.g.
stronger opening hook, clearer offer). Include concise, benefit-led, and
storytelling options where appropriate.

Users must be able to edit, regenerate, compare, select, and save
variations. Regeneration must preserve manually edited content unless the
user chooses to replace it. Keep every video, analysis, copy variation,
and published ad explicitly linked in the database and interface. Default
to one selected variation per video. Allow additional variations, with
each selected video-and-copy combination becoming a separate ad. Always
display the resulting total ad count.

## 4. Bulk review and editing

Review table with one row per video and expandable copy variations.
Include: video preview and filename; analysis and processing status;
primary text, headline, description, and call to action; destination URL
and tracking parameters; selected variations and intended ad set;
validation issues and publishing status.

Allow users to: edit individual ads; select/deselect videos and
variations; apply shared settings to selected rows; exclude incomplete
items and continue with ready items; save the batch as a draft and resume
later. Make "Apply to selected" and "Apply to all" explicit. Bulk changes
must not silently overwrite individual edits.

## 5. Campaign setup

Guided setup covering campaign, ad-set, and ad settings. Support:
connected Meta ad account and eligible business assets; campaign objective
and campaign name; budget type, amount, schedule, and spending controls;
audience location, age, and other supported targeting options; placements;
optimization and conversion settings when available; Facebook Page and
Instagram identity where supported; selected videos and copy variations;
destination URLs and tracking parameters.

Use the current supported Meta Marketing API. Verify valid fields,
objectives, and configuration combinations against official documentation.
Do not assume every feature is available for every account.

Campaign structure — create all selected ads within one shared campaign.
Offer two structures: (1) one campaign → one shared ad set → multiple
video ads; (2) one campaign → multiple ad sets grouped by audience or
another user-selected configuration → multiple video ads. Default to one
shared ad set when settings are compatible. Validate grouping rules and
explain conflicts before publishing. Configure campaign-level settings
once. Apply budgets at the campaign or ad-set level according to the
selected configuration. Do not multiply the intended budget by the number
of uploaded videos or imply that every ad receives an equal allocation.
Use clear, editable naming conventions so each ad can be traced to its
source video and copy variation.

## 6. Review, bulk publishing, and recovery

Before publishing, show a consolidated review: the campaign being created
or used; ad sets and their settings; selected videos and copy variations;
total number of ads; budget scope, amounts, and schedule; targeting,
placements, destinations, and tracking settings; validation errors and
excluded items. Publish only when the user explicitly clicks
"Publish selected ads."

Track progress for each item separately: Queued; Uploading media; Creating
creative; Creating ad; Submitted to Meta; Failed. Distinguish successful
API submission from Meta approval and active delivery. Display actual Meta
IDs and available review/delivery statuses. Store successful object IDs as
each step completes. Prevent duplicate campaigns, ad sets, creatives, and
ads during retries, repeated clicks, or interrupted requests.

If a batch partially fails: preserve successful items; identify failed or
incomplete steps; retry only the necessary steps; do not recreate
successful objects; show actionable error messages. Provide a completion
summary listing successful ads, failures, and retry actions. Do not
promise an atomic publishing operation if the API cannot provide one.

## 7. Reporting dashboard

Dashboard for campaigns and ads published through the app. Also support
importing existing account campaigns where permissions allow, with a clear
source label. Display: total spend; impressions and reach; clicks, CTR,
CPC, CPM; results and cost per result, with the result type identified;
purchases, conversion value, and ROAS when relevant tracking data exists.

Provide: date-range filters; account, campaign, ad-set, status, and batch
filters; performance trend charts; drill-down tables from campaign to
ad set to individual ad; creative previews and ad copy; side-by-side
comparisons of videos and copy variations; links from reports to the
source video and generated content; CSV export and manual refresh.
Support performance views by shared campaign, ad set, individual ad,
source video, and copy variation.

Clearly label metric definitions, including the click type used for
click-based metrics. Display the last successful sync time, reporting
timezone, currency, and attribution context where available. Treat
unavailable metrics as unavailable rather than zero. Do not combine
different currencies into an unlabeled total. For aggregated reporting:
sum only additive metrics; recalculate ratios from their underlying
totals; do not directly average CTR, CPC, CPA, or ROAS; do not sum reach
across ads; clearly identify metrics that cannot be reliably aggregated.
AI summaries may explain performance patterns and suggest next steps;
each summary must reference the underlying metrics and acknowledge limited
data. Do not automatically change budgets or campaigns based on
recommendations.

## 8. UX and application structure

Main areas: (1) Dashboard, (2) Creative library, (3) Batch upload and
processing queue, (4) Video analysis and ad-copy editor, (5) Bulk review
table, (6) Campaign creation wizard, (7) Publishing progress and batch
results, (8) Campaign and ad detail pages, (9) Integrations and account
settings. Clean, responsive SaaS design with readable tables, useful
charts, clear navigation, and strong loading, empty, error, and success
states. Understandable Vietnamese labels. Show clear progress for video
analysis, bulk publishing, and reporting sync. Preserve completed work
when users navigate away or resume a session.

## 9. Technical requirements

Preferred stack: TypeScript, Next.js, Tailwind CSS, PostgreSQL, secure
object storage for videos, a durable background-job system. Explain any
substitution in the PR.

Implement: authentication and user-level data isolation; secure media
storage and controlled access; persistent videos, analyses, copy
variations, batch drafts, campaign mappings, and reporting snapshots;
server-side Meta authentication and API requests; encrypted token storage
and secure secret handling; appropriate handling of expired or revoked
connections; background jobs for analysis, publishing, and reporting
sync; controlled concurrency, pagination, rate-limit handling, and
bounded retries; idempotent publishing and resumable batch operations;
activity logs for publishing and campaign changes. Never expose API keys
or access tokens in browser code or logs. Use clear service boundaries
for AI analysis, Meta operations, storage, and reporting so providers can
be replaced without rewriting the application.

## 10. Demo and live modes

Provide a clearly labeled demo mode with realistic sample videos,
analyses, campaign drafts, and reporting data when credentials are
unavailable. Keep demo and live data separate. Demo mode must demonstrate
the complete batch workflow, including partial failures and retries.
Never represent simulated publishing or sample reporting as a successful
live API operation. Make it clear which integrations are configured and
what is required to enable live operation.

## 11. Delivery and validation

Deliver: working source code and database migrations; an environment
variable example without secrets (`.env.example`); local setup and
deployment instructions; instructions for connecting the AI provider and
Meta account; an explanation of applicable Meta permissions, app review,
and account prerequisites; a usable demo workflow; tests covering critical
behavior.

Prioritize tests for: user data isolation; correct mapping between videos,
analyses, copy variations, and ads; campaign configuration validation;
budget handling across batch sizes; duplicate-publication prevention;
partial-failure recovery; reporting calculations and aggregation.

Verify current API behavior using official documentation. Clearly identify
integrations that remain unverified because credentials or account access
are missing.

Finish with a concise summary of what works, how to run the application,
which credentials are required, what has been tested, and any remaining
limitations.

## Acceptance criteria

- [ ] End-to-end MVP works in demo mode: upload several sample videos →
      separate analyses and copy per video → review/edit selected
      variations → configure one campaign → publish (simulated, clearly
      labeled as demo) multiple ads → inspect performance on the dashboard.
- [ ] One failed video does not block the batch; partial-failure retry
      does not recreate successful objects (idempotency keys / stored Meta
      IDs checked in tests).
- [ ] Budget is not multiplied by video count; total ad count is always
      shown; each ad traces to its source video + copy variation.
- [ ] Reporting ratios are recalculated from totals (no averaged CTR/CPC/
      CPA/ROAS, no summed reach); unavailable metrics show as unavailable,
      not zero; currencies are never combined unlabeled.
- [ ] Demo and live data are separate; demo publishing is never presented
      as a live Meta success.
- [ ] Tests pass for the prioritized areas above; README explains setup,
      `.env.example`, Meta permissions/app review, and what is unverified.
- [ ] App lives in `apps/meta-video-ads/`; muse-um core untouched; PR
      opened, no push to main; no secrets committed.

## Out of scope

- Connecting to the owner's real Meta Ads account, creating real campaigns
  or ads, or spending any budget.
- Buying anything, starting any paid trial, or submitting a Meta app
  review application.
- Merging PR #4 or PR #5, or changing muse-um Relay/Panel/daemon code.
- A production deployment or a hosted database/storage account — local
  run + deployment instructions only.
