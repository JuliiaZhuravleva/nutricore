# Stage 00 — User scenarios

Agreed personas and journeys that drive the staged plan. This is a **personal tool**,
so the scenarios are written for a single primary user (the owner) rather than a
multi-user product.

## Personas

- **P1 — Owner (primary).** The main user. Sets their own nutrition goal, logs meals
  with minimal friction, and wants the bot to do the math and nudge them. Also runs the
  my-health hub, so the `/consult` relay is available to them.
- **P2 — Admin.** Grants/revokes subscriptions via `/grant_sub` (already built). In
  practice the same person as P1.

*The multi-user "Tracker" persona is intentionally out of scope — this is a personal
tool, not a product for strangers.*

## Journeys

### S1 — Set goal (lightweight)
The user runs a short `/goal` wizard: sex, age, height, weight, activity level, goal
type (lose / maintain / gain), and goal weight. The bot computes a daily calorie +
macro target (Mifflin-St Jeor BMR → TDEE × activity → deficit/surplus by goal) and
stores it. This is the foundation — statistics, remaining-budget replies, and coaching
all need a stored target.

### S2 — Log a meal + remaining budget
The user logs a meal the way they do today (free-text or photo → AI parse → confirm →
save). After saving, the bot replies with what's left for the day, e.g.
`осталось сегодня: 640 ккал / 38 г белка`. Every log becomes a useful reply instead of
a bare "saved".

### S3 — Statistics
`📊 Статистика` shows today's totals versus the goal plus what remains, and a weekly
summary (last 7 days). Replaces the current "🚧 in development" stub.

### S4 — Weight tracking
`⚖️ Мой вес` lets the user log their weight and shows a 7-day moving-average trend
(filtering daily water-weight noise), the goal weight, and a projected arrival date,
plus a progress chart rendered as an image. Replaces a currently-dead button.

### S5 — Settings
`⚙️ Настройки` lets the user edit their goal/targets, reminder times, units, and
timezone, persisted in `User.settings`. Replaces a currently-dead button.

### S6 — Retention nudges
The bot sends meal-time reminder DMs, tracks a logging streak with a streak-save nudge,
and pushes a weekly digest. This is the bot's structural advantage over an app — native
push into a chat the user already has open.

### S7 — AI coaching
The user can ask "что съесть, чтобы добить белок?" (meal suggestions that fit the
remaining macro budget), ask free-form questions over their meal history, receive
weekly insights ("consistently short on protein", "weekends run +600 kcal"), and log
meals by voice message. Uses nutricore's own OpenAI, food/nutrition domain only.

### S8 — Consult relay (already built)
The user asks a free-text health question; the bot relays it to my-health's `/consult`
endpoint and shows the answer. If the hub returns a crisis hint, it is shown **first**,
prominently, before the answer. No medical logic, medical storage, or OpenAI call lives
on this path.

### S9 — Health indicators from my-health (deferred)
The owner wants personal health indicators (weight, activity, labs, nutrition trends)
from the my-health hub surfaced in Telegram. Deferred — see
[90-deferred-hub-integration.md](90-deferred-hub-integration.md).

## Boundary rule

Nutrition/food coaching → nutricore's own OpenAI (S7). Medical/health questions →
my-health via `/consult` (S8). Never mixed; no medical data stored in the bot.
