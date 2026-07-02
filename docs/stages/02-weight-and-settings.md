# Stage 2 — Weight tracking + Settings

**Covers:** S4, S5.
**Depends on:** Stage 0 (`User.settings` in schema), Stage 1 (goal/target stored).
**Why:** the ⚖️ Мой вес and ⚙️ Настройки buttons exist on the main keyboard but have
**no registered handlers** — pressing them today does nothing. This stage makes the
menu honest.

## Current state

- `main_keyboard` ([telegram.py:51](../../app/services/telegram.py#L51)) shows
  `⚖️ Мой вес` and `⚙️ Настройки`, but `CHOOSING_ACTION` in `create_bot_application()`
  only wires "Добавить прием пищи" and "Статистика".
- `BodyMetric` model + `crud_body_metric` exist (weight + JSON metrics), but CRUD has
  only basic get/create — no per-user, latest, or range query.
- `User.settings` (JSON column) exists in the model/DB; usable through the schema after
  Stage 0.

## Work

### 1. Weight tracking (S4)
- Add query methods to `app/crud/crud_body_metric.py`: `get_by_user`, `latest_for_user`,
  `get_by_user_and_range`.
- New handler in `app/services/telegram.py`: prompt for weight → validate → store via
  `crud_body_metric.create`. Then show:
  - 7-day **moving average** (not raw last value) to filter daily noise.
  - Goal weight (from `target_metrics`) and a **projected date** from the trend slope.
  - A progress **chart** rendered to PNG and sent as a photo.
- New chart util under `app/utils/` (matplotlib; render to an in-memory buffer).

### 2. Settings (S5)
- New handler: view/edit goal & targets, reminder times, units (kg/lb, kcal), timezone
  — persisted to `User.settings` via `crud_user.update` (now settings-aware).
- A small `ConversationHandler` or inline-keyboard menu; keep it light.

### 3. Wire both buttons
Register the two handlers in the `CHOOSING_ACTION` state so the buttons work.

## Verification
- Unit tests: moving-average + projected-date math (pure functions), settings
  round-trip through `crud_user.update`.
- Run the bot: log weight twice → confirm trend line + chart; change reminder time and
  units → confirm they persist and are read back.

## Notes
- Smart-scale / health-API integration is **out of scope** (poor Telegram fit) — the
  user types their weight. Mi Scale data, if ever wanted, would arrive via the my-health
  hub, not directly (Stage 90).
