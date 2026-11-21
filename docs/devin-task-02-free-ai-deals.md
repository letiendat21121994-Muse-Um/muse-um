# Devin Task 02 — Research free AI support deals for this project

> **For the Devin Bot.** Work on branch `devin/free-ai-deals-research` and
> open a pull request — do not push directly to `main`. Research only:
> do not submit any application, do not enter personal information, and
> do not create any account.

## Context

muse-um is an open-source multi-account Muse orchestration tool (Relay +
Panel + muse-client daemon), built by an individual developer in Vietnam.
No company, no student status. The goal: find free AI-related support
deals (credits, free tiers, programs) that can help develop this project
over the coming month.

## Already have — do NOT re-report these as new

- Devin/Poke giveaway: $69 in Devin Bot credits (redeemed, expires 7 days
  after redemption) + a free month of Devin Max.
- NVIDIA Developer Program: individual signup already in progress.
- Oracle Cloud Always Free: account already exists, A1 VM being created.

Mention them only as a one-line "already have" list in the report.

## Scope

Search for and verify, on each vendor's own official page, free AI deals
relevant to an individual developer building an open-source dev tool:

- AI coding tools with a free tier (editors, agents, code review).
- Free AI API credits or free-tier API access (LLM APIs).
- Cloud credits programs open to individuals (not only startups).
- Open-source support programs (free hosted services for OSS projects).
- Free tiers for hosting / CI / storage the project can actually use.

For each candidate record: program name, exactly what it offers (amount
or credits + duration), eligibility (individual? startup? student? any
country restriction?), how to apply (link), deadline or expiry if any,
and a verdict (worth applying: yes/no + one-line reason).

Rules:

- Only include a deal after verifying it on the vendor's own page; mark
  anything you could not verify as unverified.
- Do not present expired deals as available. Do not include referral-only
  offers.
- If a deal needs a company or student status, say so clearly as the
  blocker instead of listing it as available.
- Prioritise deals usable within days (no long application review) and
  that fit: individual, Vietnam, open-source project, no company.

## Deliverable

A report file `docs/free-ai-deals-2026-11-21.md` in the repo (English,
with a short Vietnamese summary at the top), opened as a pull request.
Include a summary table ordered by value, one row per candidate, with a
link per row to the vendor's own page.

## Acceptance criteria

- [ ] Report file exists with at least 8 verified candidates (or an honest
      explanation if fewer exist).
- [ ] Every amount, duration, and eligibility claim cites the vendor's own
      page (link per row).
- [ ] No expired or unverifiable-as-free deal presented as available.
- [ ] One-line "already have" list (Devin/Poke, NVIDIA Developer Program,
      Oracle Always Free).
- [ ] PR opened, no push to main.

## Out of scope

- Applying for anything, creating accounts, entering personal data.
- Changing any code.
