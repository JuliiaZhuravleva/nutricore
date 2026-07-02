# Task brief — consult relay (Phase 5, Step B2)

**Status:** ready to implement in-repo (not yet built). This is a spec, not code.

## Goal

Give the bot a **consult** path: a user asks a health question → the bot **relays** the text to the
`my-health` hub's copilot → shows the answer. The bot is a **thin relay**: no medical logic, no
medical storage, and it must **not** call its own OpenAI on this path (OpenAI stays for food parsing
only, in `app/services/openai_service.py`).

Role invariant (do not break): **my-health = brain/data/guardrails; nutricore = capture + chat
surface.** All medical reasoning and the mental-health guardrails live in the hub. Background:
my-health `docs/ARCHITECTURE.md` (hub-and-spoke) and `docs/phase5-unfreeze.md` (Step B).

## The endpoint contract (already built on the hub)

my-health exposes a loopback-only endpoint (`health copilot-serve`):

- **Request:** `POST http://127.0.0.1:8787/consult`
  - Header: **`X-Consult-Token: <shared-secret>`**  ← note: a custom header, **not** `Authorization: Bearer`.
  - Body: `{"question": "<user text>", "psyche": "off" | "coarse" | "full"}` — `psyche` is optional;
    omit it to use the hub's default (`coarse`). Do **not** send `full` from the bot.
- **Response 200:** `{"answer": str, "crisis_hint": str | null, "psyche_resolution": str}`
- **Status codes:** `403` = missing/invalid token · `503` = endpoint disabled (no token configured
  on the hub) · `502` = the hub's model/DB failed. Handle all three with a friendly bot message.

The hub binds to `127.0.0.1` only, so the bot must run on the **same machine** (the Mac mini) to
reach it — see my-health `docs/MIGRATION.md` / `docs/phase5-unfreeze.md` Step C.

## Crisis rule (load-bearing — implement carefully)

If the response has a non-null **`crisis_hint`**, show it **FIRST**, prominently, **before**
`answer`. It is the hub's deterministic local crisis path (BDI item 9 never reaches any LLM); the
bot only **displays** what the hub returns (e.g. the helpline `8-800-2000-122`). Never hardcode
psyche logic or a crisis number in the bot — always surface `crisis_hint` verbatim when present.

## Where the code goes (mirror existing patterns)

### 1. Config — `app/core/config.py`
Add two settings next to the existing `EXPORT_API_TOKEN` (same pydantic `BaseSettings` class):

```python
# Consult relay → my-health hub (loopback). Empty URL/token → the /consult command is disabled.
MYHEALTH_CONSULT_URL: Optional[str] = None   # e.g. "http://127.0.0.1:8787/consult"
CONSULT_TOKEN: Optional[str] = None          # shared secret; matches the hub's COPILOT_CONSULT_TOKEN
```

Add to `.env.example`:

```bash
# Consult relay (my-health hub integration)
MYHEALTH_CONSULT_URL=http://127.0.0.1:8787/consult
CONSULT_TOKEN=your_consult_token_here
```

Both come from env/1Password at runtime — never commit real values.

### 2. Handler — `app/services/telegram.py`
Add an async handler and register it in `create_bot_application()` as a direct `CommandHandler`
(mirror `grant_subscription`, which is registered via
`application.add_handler(CommandHandler("grant_sub", grant_subscription))`). Use the existing
`httpx` dependency for the outbound call. Sketch:

```python
import httpx
from app.core.config import settings

async def consult(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    question = " ".join(context.args) if context.args else ""
    if not question:
        await update.message.reply_text("Задай вопрос так: /consult <вопрос>")
        return
    if not settings.MYHEALTH_CONSULT_URL or not settings.CONSULT_TOKEN:
        await update.message.reply_text("Консультации сейчас недоступны.")
        return
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                settings.MYHEALTH_CONSULT_URL,
                json={"question": question},
                headers={"X-Consult-Token": settings.CONSULT_TOKEN},
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError:
        await update.message.reply_text("Не удалось получить ответ. Попробуй позже.")
        return

    # Crisis path FIRST — deterministic, from the hub, before the model answer.
    if data.get("crisis_hint"):
        await update.message.reply_text(f"⚠️ {data['crisis_hint']}")
    await update.message.reply_text(
        (data.get("answer") or "Пустой ответ.")
        + "\n\n_описательно, не медицинская консультация_"
    )
    # NOTE: no OpenAI call anywhere on this path.
```

Register it:

```python
application.add_handler(CommandHandler("consult", consult))
```

If you want to gate it, reuse the existing `@subscription_required` decorator — a product choice.

### 3. Tests — `tests/test_consult_relay.py` (new, offline)
Mirror `tests/test_export_meals.py` + the SQLite `conftest.py` fixtures. **Mock `httpx`** (e.g.
`respx`, or monkeypatch `httpx.AsyncClient.post`) — no real network. Cover:

- disabled when `MYHEALTH_CONSULT_URL`/`CONSULT_TOKEN` unset → friendly "unavailable" message;
- happy path → the `answer` text is sent; the request carried the `X-Consult-Token` header and
  `{"question": ...}` body;
- **`crisis_hint` present → it is sent FIRST**, before `answer`;
- hub error (403/502/`HTTPError`) → a friendly failure message, no crash;
- **the OpenAI service is never called** on this path (assert the openai_service methods aren't hit).

Verify: `poetry run pytest` + `poetry run black --check . && poetry run isort --check-only . && poetry run flake8`.

## Notes & sibling tasks

- **Answer language.** The hub's copilot now answers in **English**; this bot's UI is Russian. Decide
  whether to relay the English answer as-is (simplest, recommended) or wrap/label it. Product choice,
  not a blocker.
- **Secrets (by hand).** `CONSULT_TOKEN` must match the hub's `COPILOT_CONSULT_TOKEN`. Put both in
  1Password/env; the export side needs `EXPORT_API_TOKEN` (hub calls it `NUTRICORE_API_KEY`).
- **Merge the meals export.** The `feat/meals-export` branch (`2e0056d`, `GET /meals/export`) is the
  other half of the integration (my-health tech-debt #31) — review → merge → deploy so the hub can
  ingest meals.
- **Co-location.** The `127.0.0.1` endpoint means both services must run on the same machine (Mac
  mini). See my-health `docs/phase5-unfreeze.md` Step C.
