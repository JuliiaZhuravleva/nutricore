# Stage 90 (DEFERRED) — Hub reverse integration

**Covers:** S9.
**Status:** documented only — deferred by decision until the tracker fundamentals ship.

## Goal

Surface personal health indicators from the **my-health** hub (weight, activity, sleep,
heart rate, labs/blood, nutrition trends, and coarsened mood) inside the Telegram chat,
so the owner can ask "как мой вес за месяц?" or open a Health menu and see hub data.

This applies to the **owner only** — other users have no hub.

## Why it's net-new

my-health today exposes exactly **one** wire outward: `POST /consult` (natural-language
Q&A, loopback, `X-Consult-Token`). It has **no read endpoints** and **no outbound push**
to nutricore. The only existing hub↔bot data flow is *inbound to the hub* (my-health
pulls `GET /meals/export` from nutricore). So surfacing indicators back in chat requires
new work.

## Two candidate approaches

### A. Lean on `/consult` (NL) — cheapest
Route indicator questions through the already-built consult relay. "Как мой вес за
месяц?" already returns a natural-language answer the hub composes from the same data.
- **Pros:** zero my-health change; reuses the built path; respects the psyche
  choke-point automatically (the hub coarsens on the way out).
- **Cons:** unstructured text only — no menus, no charts, no reliable fields.

### B. New `GET /indicators` on the hub — structured
Add a token-guarded, **loopback-bound** GET endpoint to the hub's existing FastAPI app
(`src/health_vault/copilot/service.py`), composing the hub's read functions
(`analytics.py`: `weight_trend`, `activity_trend`, `nutrition_trend`; `labs/report.py`:
`lab_overview`). The bot renders menus/charts from the structured response.
- **Pros:** structured data → rich Telegram UX (buttons, PNG charts).
- **Cons:** more work; spans both repos; must carefully re-apply the guardrails.

## Hard constraints (either approach)

- **Psyche choke-point is mandatory.** Any subjective/mood/BDI signal must pass through
  the hub's `psyche_context` coarsening. **BDI item 9 (self-harm) must never leave the
  hub** and must never reach any LLM.
- **Loopback + co-location.** The hub binds `127.0.0.1` only; both services must run on
  the same machine (the Mac mini). No health data crosses a machine boundary.
- **No medical storage in the bot.** The bot renders what the hub returns and stores
  none of it — the architecture invariant holds.

## Recommendation
When picked up: start with **A** (no hub change, immediate value), and only build **B**
if structured menus/charts prove worth the cross-repo cost.

## Reference (my-health)
- `src/health_vault/copilot/service.py` — where a read endpoint would live.
- `src/health_vault/analytics.py`, `src/health_vault/labs/report.py` — data functions.
- `src/health_vault/mental_health/context.py` — mandatory psyche gate.
- `src/health_vault/config.py` — token/bind settings pattern.
