# Nutricore — staged enhancement plan

This directory holds the staged plan for unfreezing the Nutricore Telegram bot and
turning it from an AI meal-logger into a real **personal** nutrition tracker.

## Background

The bot was frozen for a long time. Today the only working flow is AI meal-logging
(text/photo → calories/macros via OpenAI) behind a subscription gate. The main menu
advertises **📊 Статистика**, **⚖️ Мой вес**, and **⚙️ Настройки**, but Статистика is a
"🚧 in development" stub and the other two buttons have no handlers at all — the menu
currently *lies* to the user. The domain is fully scaffolded (models / schemas / CRUD /
REST for meals, body metrics, activity, analysis reports), but every "intelligence"
layer — statistics, aggregation, goals, scheduled tasks, `app/services/analysis.py` —
is an empty file.

Separately, the owner runs a sibling project **my-health**, a local hub that holds all
medical data behind a hard trust boundary. The ecosystem invariant is:

> **my-health = brain / data / guardrails; nutricore = capture + chat surface.**

A consult relay (`/consult` → hub copilot) is the live link between the two.

## Locked decisions

- **Audience: personal tool** (primarily the owner). Onboarding and the subscription
  gate stay lightweight — no multi-user funnel, no paywall polish. The owner is never
  blocked by the subscription check.
- **Reverse integration (health indicators from my-health): deferred** — documented in
  Stage 90, not built yet.
- **Phasing: foundations → weight+settings → retention → AI coaching**, then the
  deferred hub stage.

## Boundary rule

Nutrition / food coaching uses nutricore's **own** OpenAI (Stage 4). Medical / health
questions go to my-health via `/consult` (Stage 0's consult relay). These never mix,
and no medical logic or medical data storage lives in the bot.

## Stage index

| Stage | Doc | Scope | Status |
|-------|-----|-------|--------|
| 0 | (this round) | Unfreeze hygiene: commit consult relay, fix drift | 🟡 in progress |
| 00 | [00-user-scenarios.md](00-user-scenarios.md) | Personas + journeys | ✅ agreed |
| 1 | [01-foundations.md](01-foundations.md) | Goals, remaining-budget, statistics | ⛔ not started |
| 2 | [02-weight-and-settings.md](02-weight-and-settings.md) | Weight tracking + settings | ⛔ not started |
| 3 | [03-retention.md](03-retention.md) | Reminders, streaks, weekly digest | ⛔ not started |
| 4 | [04-ai-coaching.md](04-ai-coaching.md) | "What to eat", insights, voice logging | ⛔ not started |
| 90 | [90-deferred-hub-integration.md](90-deferred-hub-integration.md) | my-health indicators in chat | 🧊 deferred |

Stages are implemented one at a time, each on its own branch, verified end-to-end by
driving the bot in polling mode before moving on.
