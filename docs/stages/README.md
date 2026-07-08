# Nutricore — staged enhancement plan

This directory holds the staged plan for unfreezing the Nutricore Telegram bot and
turning it from an AI meal-logger into a real **personal** nutrition tracker.

> **This table is the source of truth for build order + stage status.** ROADMAP.md
> carries a compact mirror; if they ever disagree, this file wins.

## Background

The bot was frozen for a long time; Stage 0 (below) unfroze it. Shipped and working
today: AI meal-logging (text/photo → calories/macros via OpenAI, with a packaged-food
Open Food Facts lookup), access control, the secured REST API, and the `/consult` relay
— see [ROADMAP.md](../../ROADMAP.md) for the full list. Still **scaffolded but not built**:
the "intelligence" layer — statistics, aggregation, goals, scheduled analysis
(`app/services/analysis.py`) — plus the **⚖️ Мой вес** / **⚙️ Настройки** menu buttons,
which have no handlers yet. The domain is fully scaffolded (models / schemas / CRUD / REST
for meals, body metrics, activity, analysis reports); the logic on top is the staged plan
below.

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
| 0 | — | Unfreeze hygiene: consult relay, drift fixes, access control, secured API | ✅ done |
| 00 | [00-user-scenarios.md](00-user-scenarios.md) | Personas + journeys | ✅ agreed |
| 1 | [01-foundations.md](01-foundations.md) | Goals, remaining-budget, statistics | ⛔ not started |
| 2 | [02-weight-and-settings.md](02-weight-and-settings.md) | Weight tracking + settings | ⛔ not started |
| 3 | [03-retention.md](03-retention.md) | Reminders, streaks, weekly digest | ⛔ not started |
| 4 | [04-ai-coaching.md](04-ai-coaching.md) | "What to eat", insights, voice logging | ⛔ not started |
| 90 | [90-deferred-hub-integration.md](90-deferred-hub-integration.md) | my-health indicators in chat | 🧊 deferred |
| — | [access-control.md](access-control.md) | Bot access modes (open/whitelist/closed) + whitelist | ✅ shipped (runtime `/mode` deferred) |

Stages are implemented one at a time, each on its own branch, verified end-to-end by
driving the bot in polling mode before moving on.
