# Stage 3 — Retention: reminders, streaks, weekly digest

**Covers:** S6.
**Depends on:** Stage 1 (aggregation for the digest), Stage 2 (`User.settings` reminder
times).
**Why:** a chat bot's structural advantage is native push into a conversation the user
already has open. This is the cheapest, highest-leverage retention lever available.

## Current state

- Celery worker + Redis broker are configured (`celery_app/celery_app.py`), but there
  is **no `beat_schedule`** and `celery_app/tasks/periodic.py` is an **empty file** — so
  zero scheduled tasks exist.
- Note the actual path is `celery_app/tasks/periodic.py` (a package), not a single
  `celery_app/tasks.py`.

## Work

### 1. Celery beat schedule
Add `beat_schedule` to `celery_app/celery_app.py` (e.g. hourly reminder sweep, a
nightly streak check, a weekly digest cron). `celery_beat` already runs in
docker-compose.

### 2. Send-to-Telegram helper
Celery workers have no bot context. Add a small helper that sends a message via the bot
token — either `telegram.Bot(token).send_message(...)` or a direct httpx call to the
Telegram HTTP API. Keep it isolated so tasks can be unit-tested with it mocked.

### 3. Tasks — `celery_app/tasks/periodic.py`
- **Meal-time reminders:** for each user, send a DM at their configured times
  (`User.settings`), skipping users who already logged for that slot.
- **Streaks:** track consecutive logging days (store the counter + last-log date in
  `User.settings`); an evening "streak-save" nudge if today has no log.
- **Weekly digest:** a scheduled push (e.g. Sunday evening) summarizing the week —
  reuse `analysis.weekly_summary` from Stage 1.

## Verification
- Unit test the streak counter (consecutive vs gap vs same-day) and the "who needs a
  reminder" selection with a seeded DB and a mocked sender.
- Trigger a task synchronously (`task.apply()`) in dev and confirm a real DM arrives.

## Notes
- Keep it light — over-notifying is a top churn driver. Respect user-configured times
  and a global quiet-hours guard.
