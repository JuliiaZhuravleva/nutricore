# Stage 1 — Foundations: goals, remaining-budget, statistics

**Covers:** S1, S2, S3.
**Depends on:** Stage 0 (green test suite, meal-save path fixed).
**Why first:** everything downstream (budgets, stats, retention digests, coaching)
needs a stored daily target and meal aggregation, neither of which exists today.

## Owner decision (2026-10-28) — monitor first, no goal wizard yet

> «Пока просто мониторим; после сбора определённого количества данных исходить из
> дефолтных значений и понемногу комфортно двигать их в сторону целевых.»

So the `/goal` wizard below (sex/age/height/activity → BMR/TDEE) is **not** the next
step. Order instead: (1) statistics without a goal — shipped 2026-10-28
(📊 Сегодня / Вчера / 7 дней); (2) once enough days are logged, propose a starting
target from her own observed intake (a default, not a calculation she must fill in);
(3) move it toward the target in small, comfortable steps. "Enough data" and the step
size are still to be agreed. Several goals at once are possible later (see Stage 2).

## Current state

- Meals are persisted (`crud_meal.create` from `confirm_meal`) with
  calories/proteins/fats/carbs, but there is **no aggregation anywhere** — no
  `func.sum`, no per-user query, no date-range filter in any CRUD.
- `app/services/analysis.py` is an **empty file**.
- `show_statistics` ([telegram.py:306](../../app/services/telegram.py#L306)) is a stub
  that replies "🚧 in development".
- `User.target_metrics` (JSON, commented "For storing KBJU norms") exists and is unused
  — the natural home for the computed goal, so **no migration is needed**.

## Work

### 1. Goal math — `app/services/nutrition_goals.py` (new)
Pure functions, fully unit-testable:
- `bmr(sex, age, height_cm, weight_kg)` — Mifflin-St Jeor.
- `tdee(bmr, activity_level)` — standard activity multipliers.
- `daily_target(tdee, goal_type, rate)` — apply deficit/surplus; derive macro split
  (e.g. protein g/kg, remainder carbs/fat) → `{calories, protein, fats, carbs}`.

### 2. `/goal` wizard — `app/services/telegram.py`
A `ConversationHandler` collecting sex, age, height, weight, activity, goal type, goal
weight → compute target → store in `User.target_metrics` (plus the raw profile inputs
so Stage 4 can recompute). Register alongside the existing conversation handler in
`create_bot_application()`.

### 3. Aggregation — `app/services/analysis.py` + `app/crud/crud_meal.py`
- Add query methods to `crud_meal`: `get_by_user_and_range(db, user_id, start, end)`
  and a daily/weekly totals helper (sum calories/proteins/fats/carbs).
- Implement `analysis.py` to compose those into `daily_summary(user, date)` and
  `weekly_summary(user, week)` returning consumed vs target vs remaining.

### 4. Remaining-budget reply (S2) — `confirm_meal`
After the meal saves, call `daily_summary` and append the remaining line to the success
reply.

### 5. Statistics view (S3) — `show_statistics`
Replace the stub with today's totals vs goal + remaining, and a 7-day summary. Prompt
the user to run `/goal` first if no target is set.

*(Optional: fill `app/api/v1/stats.py` — empty file exists — for REST parity. Not
required for the bot and can be skipped.)*

## Verification
- Unit tests for `nutrition_goals` (known BMR/TDEE reference values) and for the
  aggregation helpers (seed meals in the SQLite test DB, assert sums).
- Run the bot in polling: `/goal` → confirm the computed target; log a meal → confirm
  the remaining-budget reply; open 📊 → confirm daily + weekly numbers.

## Out of scope
Weight, settings, reminders, coaching — later stages.
