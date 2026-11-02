# Access control — bot modes + whitelist

Cross-cutting feature (not a numbered stage). OpenClaw-style switchable access
modes, gating the whole bot. Shipped: config-driven modes + a global gate.
Deferred: a runtime `/mode` admin command.

## Modes (`BOT_ACCESS_MODE`)

| Mode | Who may use the bot |
|------|---------------------|
| `open` | everyone |
| `whitelist` *(default)* | admins (`TELEGRAM_ADMIN_IDS`) + `ALLOWED_TELEGRAM_IDS` + active rows of `access_grants` |
| `closed` | admins only (maintenance) |

- **Default is `whitelist`** so an unconfigured bot never answers strangers.
- Admins (the owner) are **always** allowed, in every mode.
- Unknown/typo'd mode values fall back to `whitelist` (safe) with a warning.
- Requests from non-allowed users are **silently dropped** — no reply, so the
  bot doesn't advertise its existence.

## Config

- `BOT_ACCESS_MODE` — `open | whitelist | closed` (default `whitelist`).
- `ALLOWED_TELEGRAM_IDS` — comma-separated ids allowed in whitelist mode
  (admins are always allowed regardless). Tolerant of malformed entries.

Both in `app/core/config.py`; ids parsed via the shared tolerant `_parse_id_list`
helper (a bad token is skipped + logged, never raised — this is on the hot path).

## Implementation

- `settings.access_mode` / `settings.allowed_ids` — normalized/parsed accessors.
- `is_user_allowed(telegram_id)` in `app/services/telegram.py` — the mode logic.
- `access_gate` — a `TypeHandler(Update, …)` registered at **group=-1** in
  `create_bot_application()`, so it runs before every other handler. For a
  non-allowed user it raises `ApplicationHandlerStop` (drops the update silently).

Tests: `tests/test_access_control.py` (mode matrix, malformed-id tolerance, and
the gate raising/​passing).

## Deferred — runtime `/mode` command

An admin-only `/mode <open|whitelist|closed>` to switch at runtime (OpenClaw-style
toggle) without a restart. Needs persistence of the current mode (e.g. a small
settings row or a JSON file) since env vars are read-only at runtime. Not built yet.

## Grants: people invited by hand

`/invite` (owner only) opens Telegram's own picker; a client without it can forward a message
from the person or send their contact. A pick writes a row in `access_grants` and a long
subscription, so the invited person never meets the word «подписка». `/users` lists them, and
closes or returns access on one button. Closing never deletes records.

- **Checked after the settings.** Admins and `ALLOWED_TELEGRAM_IDS` are decided with no query,
  so they keep working with the database down.
- **Fails closed.** A grant that cannot be read means no entry for that update, and an error
  in the log.
- **`whitelist` only.** `closed` stays admins only even for a grant holder (the invite screen
  says so), and `open` needs no grant.
- **Settings win.** Someone in the settings cannot be closed by a button; the screen says the
  list on the server has to change.
- **Closed means quiet.** `may_receive` also stops what the bot does unasked: automatic reads,
  lines owed after a failure, the week summary.
- **No token in the link.** Entry is tied to the id, so a forwarded link lets nobody else in.
