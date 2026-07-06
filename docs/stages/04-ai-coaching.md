# Stage 4 — AI coaching

**Covers:** S7.
**Depends on:** Stage 1 (remaining budget), Stage 0 (`ai_analysis` stored per meal).
**Why:** the LLM is already in the stack — this is nutricore's differentiator versus
text-only or photo-only trackers. All of it stays in the **food/nutrition domain** on
nutricore's own OpenAI; medical questions go to `/consult` (Stage 0), never here.

## Work

### 1. "What should I eat?" (remaining-macro suggestions)
New `OpenAIService` method: given the day's remaining calorie + macro budget (from
Stage 1's `daily_summary`), return 3–4 concrete meal ideas that fit. New bot handler /
button to trigger it.

### 2. Ask-anything + insights over meal history
- A handler that answers free-form questions about the user's own logged meals
  (e.g. "how's my protein this week?") by feeding aggregated history to the model.
- Optional weekly insight generation ("consistently short on protein", "weekends run
  +600 kcal") — can piggyback on the Stage 3 weekly digest.
- Reuse the per-meal `ai_analysis` JSON added in Stage 0 as richer context.

### 3. Voice-message logging
Handle Telegram voice messages: download → transcribe → feed the text into the existing
meal-parse path (`process_meal_input`). Decide transcription backend (local whisper
service vs OpenAI) as an implementation detail; keep it behind a small helper.

## Files
- `app/services/openai_service.py` — new methods (suggestions, insights).
- `app/services/telegram.py` — new handlers (suggestion trigger, ask-anything, voice).

## Verification
- Mock the OpenAI client in unit tests (assert prompt composition includes the
  remaining budget / history, and the reply is rendered).
- Run the bot: with a partially-logged day, ask for a suggestion and confirm it targets
  the remaining macros; send a voice note and confirm it logs a meal.

## Boundary reminder
This path must **not** touch `/consult` and must **not** answer medical questions. Keep
the split explicit: nutrition coaching = OpenAI here; health/medical = my-health hub.
