# Consult relay — reference

**Status: SHIPPED.** Implemented in [`app/services/telegram.py`](../app/services/telegram.py)
(the `/consult` handler); tests in `tests/test_consult_relay.py`. This is the
endpoint-contract reference, not a build brief.

## What it is

The `/consult` command is a **thin relay**: a user asks a health question → the bot
forwards the text to the `my-health` hub's copilot → shows the answer. The bot does **no
medical reasoning, stores no medical data, and must not call its own OpenAI on this path**
(OpenAI stays for food parsing only).

> **Role invariant (do not break):** my-health = brain / data / guardrails;
> nutricore = capture + chat surface. All medical reasoning and the mental-health
> guardrails live in the hub. See [product-philosophy.md](product-philosophy.md).

## Endpoint contract (owned by the hub)

my-health exposes a **loopback-only** endpoint, so the bot must run on the same machine
(the Mac mini) to reach it.

- **Request:** `POST http://127.0.0.1:8787/consult`
  - Header **`X-Consult-Token: <shared-secret>`** — a custom header, **not** `Authorization: Bearer`.
  - Body: `{"question": "<user text>", "psyche": "off" | "coarse" | "full"}` — `psyche` is
    optional; omit it for the hub's default (`coarse`). The bot never sends `full`.
- **Response 200:** `{"answer": str, "crisis_hint": str | null, "psyche_resolution": str}`
- **Status codes:** `403` invalid/missing token · `503` endpoint disabled (no token on the
  hub) · `502` hub model/DB failed. All three surface as a friendly bot message.

## Crisis rule (load-bearing)

If the response has a non-null **`crisis_hint`**, show it **FIRST**, prominently, before
`answer`. It is the hub's deterministic local crisis path (e.g. BDI item 9 never reaches an
LLM); the bot only **displays** what the hub returns (e.g. a helpline number). Never
hardcode psyche logic or a crisis number in the bot — surface `crisis_hint` verbatim.

## Configuration

Two settings (empty → `/consult` is disabled), supplied via env / 1Password at runtime,
never committed:

- `MYHEALTH_CONSULT_URL` — e.g. `http://127.0.0.1:8787/consult`
- `CONSULT_TOKEN` — shared secret; must match the hub's `COPILOT_CONSULT_TOKEN`

The paired meals-export side (`GET /meals/export`) uses `EXPORT_API_TOKEN` (the hub calls
it `NUTRICORE_API_KEY`) — that is how the hub ingests meals for context.

## Notes

- **Answer language.** The hub's copilot answers in English; the bot's UI is Russian. The
  relay currently passes the answer through as-is (labelled as descriptive, not medical
  advice).
- **Co-location.** The `127.0.0.1` endpoint requires both services on the same machine.
