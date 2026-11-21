# Devin Task 03 — Research AI tools for Meta Ads: alerts, purchase-conversion optimisation, creatives & campaign setup

> **For the Devin Bot.** Work on branch `devin/meta-ads-ai-tools-research` and
> open a pull request — do not push directly to `main`. Research only:
> do not create any account, do not connect to any Meta Ads account, do not
> submit any application, do not enter personal information, and do not
> buy anything.

## Context

The owner is an individual advertiser in Vietnam running Meta Ads with a
**purchase-conversion objective for e-commerce** (hair products, US market).
He wants to build a new app — separate from muse-um, but using the same
research-first approach — that helps him run Meta Ads better: catch
problems early, spend budget where it converts, and create better ads
faster.

This task is research only. Its output decides what the app will do:
the report must end with **feature packages the owner can choose from**,
not just a list of tools.

## Scope — research these three groups

### 1. Alerting, monitoring & optimisation for purchase-conversion ads

Tools / features that watch a Meta Ads account and warn or act when
something changes, for example:

- CPA / cost-per-purchase spike, ROAS drop, CTR / CPM / CPC drift,
  conversion-rate drop, frequency too high / ad fatigue, budget pacing
  (spending too fast / too slow), Learning Limited, ads disapproved or
  rejected, Pixel / Conversions API events missing or degraded, checkout
  / purchase events dropping.
- Automated rules: pause a losing adset, scale a winning one, shift
  budget, send an alert (email / Slack / WhatsApp / in-app).
- Optimisation suggestions: budget reallocation across adsets, bid /
  budget changes, audience / placement changes, creative refresh timing.

For each candidate record what it monitors, how fast it alerts
(real-time / hourly / daily), what actions it can take automatically
vs. only suggest, and what Meta access it needs (Ads Manager, Meta
Marketing API, Conversions API).

### 2. AI tools for creatives, content & ad copy on Meta

Tools that help create Meta ads faster / better, for example:

- AI image / video ad generation, product-photo to ad, background
  removal / replacement, UGC-style video, avatars / voiceover.
- Ad copy: primary text, headlines, hooks, CTAs, in Vietnamese and in
  English (the owner advertises to the US market, so English copy
  matters; Vietnamese UI / support is a plus).
- Creative variations & testing: many variants from one idea, Advantage+
  creative / enhancements, automatic cropping / format adaptation
  (Feed / Reels / Stories).
- Creative analysis: which hook / visual / copy drives purchases,
  creative fatigue detection.

### 3. AI tools for campaign setup & management on Meta

Tools that help set up and run campaigns, for example:

- Meta's own Advantage+ sales campaigns, Advantage+ audience,
  Advantage+ creative, automated A/B testing, Campaign Budget
  Optimisation (CBO) / Advantage campaign budget.
- Third-party campaign builders: create campaign / adset / ad from a
  product URL or a brief, audience targeting suggestions, budget
  recommendations, bulk creation, automated reporting.

Start with **Meta's own native AI tools first** (Advantage+, Meta AI,
Andromeda where relevant), verified on Meta's own pages, then
third-party tools. Clearly separate native Meta features from
third-party tools in the report.

## For each candidate record

- Tool / feature name, vendor, link to the vendor's own official page.
- Exactly what it does, in one or two lines, and which group above it
  belongs to (alerts / creative / setup — a tool can belong to more
  than one).
- Pricing: free tier / free trial / paid plan amounts in USD as stated
  on the official page, and what is paywalled (per ad account, per
  spend, per seat, per creative).
- How it connects to Meta: official Meta integration, Meta Marketing
  API / Graph API, Conversions API, or manual export / CSV only.
  Note any Meta app review / permission the app would need to do the
  same thing itself.
- Eligibility / limits: Vietnam availability if stated, language
  support (Vietnamese / English), minimum ad spend if any, Meta-only
  or multi-channel.
- Verdict: worth using / worth copying as a feature in our own app /
  not worth it, with a one-line reason.

Rules:

- Only include a tool after verifying it on the vendor's own official
  page; mark anything you could not verify as unverified.
- Do not present discontinued, renamed, or merged products as
  available. If a tool was acquired / shut down / folded into another
  product (for example a standalone tool now inside Devin or Meta),
  say so instead of listing it as a separate option.
- Do not include referral-only offers.
- Do not invent prices, limits, or Meta API permissions. If the
  official page does not state a number, say "not stated on the
  official page" instead of guessing.
- Prioritise tools usable by an individual advertiser (no enterprise
  contract, no agency minimum) and relevant to purchase conversion,
  not just brand awareness or lead generation.

## Deliverable

A report file `docs/meta-ads-ai-tools-2026-11-21.md` in the repo
(English, with a short Vietnamese summary at the top), opened as a
pull request. It must contain:

1. A summary table ordered by practical value for this advertiser,
   one row per candidate, with a link per row to the vendor's own
   official page.
2. Per-tool details (the fields above).
3. **Feature packages for the owner to choose from** — this is the
   main decision output. Propose 3–4 packages, for example:
   - **Package A — Alerts only (MVP):** real-time alerts on CPA /
     ROAS / fatigue / disapproval / pixel problems, no automatic
     actions.
   - **Package B — Alerts + Optimisation:** A plus budget
     reallocation suggestions, pause / scale rules, daily
     optimisation report.
   - **Package C — Alerts + Optimisation + Creative AI:** B plus AI
     creative / copy generation and creative-fatigue detection.
   - **Package D — Full Meta Ads copilot:** C plus AI campaign
     setup, bulk creation, A/B testing and automated reporting.
   For each package list: the exact features, which researched tools
   / Meta APIs it reuses or replaces, what data and Meta permissions
   it needs (Marketing API scopes, Conversions API, ad-account
   access), build complexity (S / M / L with a one-line reason),
   and risks / limits (for example: needs Meta app review, cannot
   act without the owner's approval, creative quality still needs
   human review).
   Do not force one package as "the answer" — present them so the
   owner can pick one, or mix features across packages.

## Acceptance criteria

- [ ] Report file exists with at least 6 verified candidates in group
      1 (alerts / optimisation), at least 6 in group 2 (creative /
      content), and at least 4 in group 3 (setup / management), or an
      honest explanation if fewer exist.
- [ ] Meta's own native AI tools (Advantage+ and related) are covered
      first and verified on Meta's own pages, clearly separated from
      third-party tools.
- [ ] Every price, limit, and Meta-connection claim cites the
      vendor's own official page (link per row); unverified claims
      are marked unverified, not presented as facts.
- [ ] Feature packages section exists with 3–4 packages, each with
      features, data / permissions needed, build complexity, and
      risks / limits.
- [ ] Short Vietnamese summary at the top of the report.
- [ ] PR opened, no push to main.

## Out of scope

- Creating accounts, connecting to the owner's Meta Ads account,
  entering personal data, or contacting any vendor.
- Buying anything or starting any paid trial.
- Changing any code in muse-um, or building the Meta Ads app itself
  — this task is research and feature-package design only.
