# ADR-0004 — Model providers per task + a weekly shutdown-date watch

**Status:** Accepted
**Date:** 2026-10-27
**Cross-reference:** [ADR-0002](ADR-0002-responses-api-migration.md) — split-client pattern (web search on the Responses API)

---

## Context

The whole bot (photo, text, barcode, label OCR, web search) ran on one model, `gpt-4o-mini`.
OpenAI set its shutdown for **2027-01-14** (`models.retrieve("gpt-4o-mini").shutdown_date`,
read 2026-10-27), three months out. The only defense in the code was reactive: a 404 at call
time opened a model picker in chat. Nothing told the owner *before* a model went away, and
nothing let a task run on a model from another company.

## Decisions

### §1 — A model is a reference `"<route>:<model>"`, chosen per task

- Routes: `openai` (OpenAI directly) and `openrouter` (any company via OpenRouter's
  OpenAI-compatible API — same `chat.completions` call, another `base_url`; no new dependency).
- A bare id means `openai`, so the existing `OPENAI_MODEL=gpt-4o-mini` and the stored
  `"openai_model"` setting keep working with no migration.
- Per task: `model:<task>` in `app_settings` (the admin's pick) → `MODEL_<TASK>` env →
  `OPENAI_MODEL`. Tasks: vision, text, barcode, label_ocr. Web search keeps its own pin.
- `app/services/model_registry.py` holds what each known model accepts (temperature,
  `max_tokens` vs `max_completion_tokens`, reasoning effort, a token floor for reasoning
  models). An unknown id gets the cautious default: no temperature, `max_completion_tokens`,
  the floor.
- OpenRouter calls always send `provider: {require_parameters: true, data_collection: "deny"}`
  — a meal photo never goes to a provider that keeps or trains on it.
- Embeddings stay on OpenAI directly: the 1536 dimension is fixed by the ADR-0003 migration.

### §2 — Only the admin switches models

A model failure no longer offers a picker to whoever sent the meal. A non-admin gets "temporarily
unavailable, the owner knows"; the admin gets one alert per breakage and the picker, which sets
the model **for the failed task**.

### §3 — A weekly check asks each company, not an aggregator

`celery_app.tasks.model_watch.check_models`, Monday 06:00 UTC, and on demand via `/models`.
For every model in use (the four tasks, web search, embeddings):

| Company | Source | Read |
|---|---|---|
| OpenAI | `models.retrieve(id)` (+ the dated snapshot for an alias; the earlier date wins) | `shutdown_date`, 404 = gone |
| OpenAI | `developers.openai.com/api/docs/deprecations.md` | the recommended replacement only |
| Anthropic | `platform.claude.com/docs/en/about-claude/model-deprecations.md` | the "Model status" table |
| Google | `ai.google.dev/gemini-api/docs/deprecations` (HTML) | the "Shutdown date" tables |
| OpenRouter | public `GET /models` | still listed; `expiration_date` as an extra signal |

An OpenRouter reference is also checked at its company (`openrouter:openai/gpt-4o-mini` hears
OpenAI's date — OpenRouter's own `expiration_date` was null for it). A model with no company
source (Qwen) is watched only through the OpenRouter list.

States: `ok`, `announced` (a date more than `MODEL_WATCH_WARN_DAYS` away, or deprecated with
no date), `soon`, `past_date`, `not_found`, `missing_from_list`, `call_failed`, `source_broken`.
The worst source wins. **A page whose table is gone is `source_broken`, never `ok`** — a
changed page must not read as "nothing announced".

### §4 — One message per news, recorded only once delivered

News = a non-ok state that differs from the stored one, or a date that moved earlier. An
announced date is told once even if it is far away, then again when it comes within the warning
window. All news of a run goes into one message to the admins. The state (`model_watch:<ref>` in
`app_settings`) is saved **only if the message went out**, so an undelivered alert is retried
next week. A model back to `ok`, or no longer used, is forgotten, so a new problem alerts again.

## Consequences

- No migration; no new required env. `OPENROUTER_API_KEY` is optional (without it, OpenRouter
  models are not offered).
- The three company pages are parsed by shape; tests run on trimmed copies taken 2026-10-27
  (`tests/fixtures/model_watch/`). When a page changes, the admin gets "could not check", and
  the parser needs an update.

## Deferred

- A native Claude adapter (the Anthropic SDK directly) — only if Claude wins the eval.
- `app/api/v1/ai.py` is not wired into `app/main.py`; it still assumes one model.
- What to do with the `gpt-4o-mini` default once a winner is chosen (switch `OPENAI_MODEL`
  in the code before 2027-01-14).
