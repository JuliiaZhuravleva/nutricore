# Chat simulator

The real bot, driven one command at a time from the shell. It uses
`create_bot_application()`, the real handlers and a SQLite file, with a fake
Telegram underneath. It is meant for walking a persona through a scenario and
reading the chat as the person would see it. It never calls a model, never uses
the network and never reads a key.

```bash
W=/tmp/w                                  # the world: any folder, made on first use
./scripts/sim.sh $W new Julia --owner     # the owner (an admin)
./scripts/sim.sh $W new Anna              # invited by the owner; no zone chosen
./scripts/sim.sh $W say "гречка с курицей и салат"
./scripts/sim.sh $W tap "🍲 Блюда"        # a reply-keyboard key
./scripts/sim.sh $W tap "Салат"           # an inline button, latest message having it
./scripts/sim.sh $W tap "⭐ В избранное"
./scripts/sim.sh $W advance 2h
./scripts/sim.sh $W tap "⭐ Избранное"
./scripts/sim.sh $W tap "Салат"
./scripts/sim.sh $W day                   # time, name, precision, time_source
./scripts/sim.sh $W chat --last 5
```

## Commands

| command | does |
|---|---|
| `new <name> [--owner] [--zone Europe/Berlin]` | Adds a person and sends `/start`. The first person must be `--owner`; anyone else is invited by the owner. Without `--zone`, `settings["timezone"]` stays absent and the default zone applies. |
| `as <name>` | Acts as this person from now on. |
| `say "<text>"` | Sends a text message (a leading `/` makes it a command). |
| `voice "<transcript>"` | Sends a voice note that is heard as this text. |
| `photo "<seen>" [--caption ".."]` | Sends a photo that the fake vision reads as this text. |
| `tap "<label>" [--msg N]` | Taps an inline button on the latest message that has it, or on #N. Matching goes exact, then case-insensitive, then a unique substring, each across the whole chat (newest first) before the next: an exact `[Салат]` on an older message wins over `[Салат · 1 раз]` on a newer one. If no inline button matches, it falls back to a reply-keyboard key. |
| `reply N "<text>"` | Replies to message #N. |
| `clock [2026-11-06T21:00 \| 21:00]` | Shows the clock, or sets it in the person's zone. Time only goes forward. |
| `advance 40m\|2h\|1d\|1h30m [--tick]` | Moves the clock. With `--tick`, it ticks at every 15-minute beat on the way. |
| `tick` | Runs every periodic push due at the clock (the registry in `pushes.py`). Beat's `model-watch` is left out on purpose: it needs the network and the model companies' APIs. |
| `chat [--last N]` | Prints the person's chat. |
| `day [YYYY-MM-DD]` | Prints the person's live records for that local day. |

## Output

Each command prints only what it changed:

- `> #7 you: …` is what was sent.
- `+ #8 bot (↩ #7, silent):` is a new message from the bot.
- `~ #8 bot (edited):` is an edited message, shown as it looks now.
- `- #8 bot (… DELETED):` is a deleted message. Its buttons are gone, so they are not shown and `tap` does not find them.
- `toast: …` is a callback answer.
- `keyboard: [a] [b] | [c]` means the reply keyboard changed; ` | ` separates its rows.
- `[in Julia's chat] …` is a message that went to someone else.
- `!! APP ERROR: <logger>: <message>` is an error the app logged and went on from.
- `!! A MODEL CALL WAS ATTEMPTED AND REFUSED: …` is a model call the world stopped.

Buttons follow each message as `[label]` rows.

Message numbers count per chat, as in Telegram's private chats: everyone's chat starts
at #1, so Julia's #12 and Anna's #12 are different messages. `#N` in `chat`,
`reply N` and `tap --msg N` always means the current person's chat.

`chat` prints the whole chat in the same format, without the change marks.

## Exit codes

- `0`: the command ran.
- `1`: the command was refused (an unknown button, a clock moved back, a world busy with another command for over 60 s). The reason and the buttons that are there go to stderr.
- `2`: usage error.
- `3`: something tried to call a model. The world records every such attempt in `sim.json` under `model_calls`.
- `4`: the app logged an error (level ERROR or above) during the command and went on, for example a handler that crashed and answered «Не получилось». The world records each under `errors` in `sim.json`; the traceback goes to stderr.

## How it works

`tests/sim/world.py` sets up the world. Before any app import, it:

- moves into the state folder, so no `.env` is read;
- seeds placeholder settings;
- points every `SessionLocal` at `world.db`.

It then patches:

- **The clock.** Every `app.*` module's `datetime`, `date` and `time` are replaced. Within a command the clock runs in real time, so waits still end. Between commands it stands still. `users.created_at` / `updated_at` hold the real `datetime.utcnow` itself (bound at import), so `boot` points those column defaults at the world's clock too. What the clock does not reach: `server_default=func.now()`, which SQLite fills from the machine's time. Every such column today also has a Python `default`, which wins, so none is filled that way.
- **The model client** (OpenAI) and **httpx**. Both raise when touched.
- **Food reading, photo resolution and voice.** These are deterministic fakes. Text is split on commas and «и», and every dish gets fixed numbers derived from its name.

Other pieces:

- `cli.py` holds `SimWire`, a Telegram fake built on `tests/test_capture_router.Wire`. It keeps the chats in `sim.json` and refuses what Telegram refuses: an edit or delete of a missing message, an edit that changes nothing, a text edit of a photo, a caption or media edit of a text message.
- One command at a time per world: a command holds a lock on `<state>/.lock` and another one waits for it (up to 60 s, `SIM_LOCK_WAIT`), so parallel agents on one world do not overwrite each other.
- The bot's in-memory state (`user_data`, `chat_data`, conversation steps) is kept in `ptb.pickle`, as if one process had lived through all the commands.
- `pushes.py` is the registry `tick` runs. Today it holds only the week card. A new periodic push is one line there. Beat's `model-watch` is deliberately not in it (network and model APIs).

`tests/sim/test_smoke.py` runs the CLI in a temporary folder.
