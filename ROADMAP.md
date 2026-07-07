# Nutricore Roadmap

High-level direction. The **detailed, source-of-truth plan lives in
[`docs/stages/`](docs/stages/README.md)** (staged, one branch at a time); this file is the
bird's-eye view and the list of things deliberately *not* being built.

## What Nutricore is

A **personal** nutrition tracker on Telegram — capture meals with minimal friction
(text/photo → КБЖУ via OpenAI) and chat about food. It is **not** a multi-user product.

**Ecosystem boundary:** `my-health` = brain / data / guardrails; **nutricore = capture +
chat surface.** Nutrition/food coaching uses nutricore's own OpenAI; medical/health
questions go to my-health via the `/consult` relay. These never mix, and no medical logic
or medical data lives in the bot.

## Current status (2026-07)

**Shipped & working:**
- AI meal-logging: text/photo → foods + calories/macros, confirm → save. Photo path
  hardened (base64 to OpenAI, atomic draft, retries) and self-healing against OpenAI model
  deprecation (owner can pick a new model in-chat; choice persisted).
- Access control (open / whitelist / closed, default **whitelist**) with a silent gate.
- Secured REST API (`X-API-Token`, fail-closed) + Telegram webhook secret.
- `/consult` relay to the my-health hub.
- Subscription gate + `/grant_sub` admin grant (owner never blocked).
- Debug substrate: `ai_call_logs` table with retention purge.
- **Deployed live** on the home Mac mini (headless, Telegram polling) via the
  openclaw-setup `claw-deploy` menu; Postgres data on a host bind mount.

**Scaffolded but not built (the "intelligence" layer):** goals, remaining-budget replies,
statistics, weight tracking, settings, reminders/digests, coaching — these are the staged
plan below. The domain models/CRUD/REST exist; the logic does not yet.

## Plan — see [`docs/stages/`](docs/stages/README.md)

| Stage | Scope | Status |
|-------|-------|--------|
| 0 | Unfreeze hygiene (consult relay, drift fixes, access control, secured API) | ✅ done |
| 1 | Foundations: `/goal` wizard, remaining-budget replies, statistics | ⛔ next |
| 2 | Weight tracking + settings | ⛔ planned |
| 3 | Retention: reminders, streaks, weekly digest | ⛔ planned |
| 4 | AI coaching: "what to eat", insights, voice logging | ⛔ planned |
| 90 | my-health indicators in chat (reverse integration) | 🧊 deferred |

Stages ship one at a time, each on its own branch, verified end-to-end by driving the bot
in polling mode before moving on.

## Enhancements (outside the locked stages)

- **Product lookup for packaged food** — return a packaged product's *actual* КБЖУ
  (barcode → Open Food Facts, or web-search identification) instead of a vision estimate,
  with a source/confidence badge. Optional/opt-in. Spec:
  [`docs/photo-product-lookup.md`](photo-product-lookup.md); design:
  [`docs/decisions/ADR-0001`](decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md).
  - **Round 1 shipped (2026-07):** auto-trigger on a vision-read barcode → OFF lookup on a
    pluggable resolution pipeline, per-100g scaled to the vision portion, transparent
    source/confidence badge, cached in `product_caches`. **Round 2 (deferred):** A8 name
    search, A10 label OCR, A9 web_search (needs a Responses-API-migration ADR). Residuals in
    `_tech-debt.md` TD-011.
- **Persist inbound messages/content + reprocess** — store photo+caption on receipt so
  failed/dropped items aren't lost and can be re-analyzed after a fix. See `_tech-debt.md`
  TD-009.

## Technical debt

Tracked in [`_tech-debt.md`](_tech-debt.md). Current themes: poetry env stability (TD-001),
self-heal refactor/coverage (TD-007/008), inbound persistence (TD-009).

## Non-goals (explicitly out of scope)

This is a personal tool. The following — carried over from an earlier, product-oriented
draft of this roadmap — are **not** planned:

- Multi-user onboarding funnels, paywall/subscription polish for strangers.
- Companion mobile app, web dashboard, cross-platform sync.
- Third-party API/webhook platform, community/social features, enterprise/wellness
  programs, anonymized data marketplace.

If the audience ever changes, revisit — but until then these don't drive decisions.
