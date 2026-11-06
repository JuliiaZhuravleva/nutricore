"""The chat simulator's commands: ``python -m tests.sim.cli <state-dir> <command> ...``.

Run it through ``scripts/sim.sh``; usage is in ``tests/sim/README.md``. Every command
loads the world from the state folder, does one thing through the real application
(``create_bot_application()``, real handlers, a fake Telegram underneath) and prints
only what changed.
"""

from __future__ import annotations

import argparse
import asyncio
import html
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tests.sim import world
from tests.sim.world import BOT_ID, CLOCK, FIRST_PERSON_ID

# --- the wire -------------------------------------------------------------------------


def _wire_class():
    from tests.test_capture_router import Wire

    class SimWire(Wire):
        """Telegram for the world: every chat as a phone shows it, kept in the state.
        Refuses what Telegram refuses: an edit or delete of a message that is not
        there, an edit that changes nothing, a text edit of a photo, a caption or
        media edit of a text."""

        def __init__(self, state: world.State):
            super().__init__()
            self.state = state

        async def do_request(self, url, method, request_data=None, **_kwargs):
            if method == "GET" or "/file/" in url:
                file_id = url.rsplit("/", 1)[-1]
                sent = self.state.data["files"].get(file_id, {})
                prefix = "SIMVOICE:" if sent.get("kind") == "voice" else "SIMPHOTO:"
                return 200, (prefix + sent.get("content", "")).encode("utf-8")
            name = url.rsplit("/", 1)[-1]
            params = dict(request_data.parameters) if request_data else {}
            self.calls.append((name, params))
            try:
                result = self._result(name, params)
            except _Refused as refused:
                body = {"ok": False, "error_code": 400, "description": str(refused)}
                return 400, json.dumps(body).encode()
            return 200, json.dumps({"ok": True, "result": result}).encode()

        def _result(self, name, params):
            if name == "getMe":
                return {
                    "id": BOT_ID,
                    "is_bot": True,
                    "first_name": "bot",
                    "username": "b",
                }
            if name == "getFile":
                file_id = params.get("file_id", "f")
                return {
                    "file_id": file_id,
                    "file_unique_id": file_id,
                    "file_path": f"sim/{file_id}",
                }
            handler = getattr(self, "_" + name, None)
            return handler(params) if handler else True

        # sends
        def _new(self, params, kind, text):
            state = self.state
            chat = int(params["chat_id"])
            markup = _markup(params.get("reply_markup"))
            keys = state.data["keyboards"]
            before = keys.get(str(chat))
            if markup.get("keyboard") is not None:
                keys[str(chat)] = [
                    [_label(b) for b in row] for row in markup["keyboard"]
                ]
            if markup.get("remove_keyboard"):
                keys.pop(str(chat), None)
            keyboard_changed = keys.get(str(chat)) != before
            reply = _markup(params.get("reply_parameters")).get("message_id")
            message = state.add_message(
                id=state.next_message(chat),
                chat=chat,
                sender="bot",
                kind=kind,
                text=text or "",
                html=params.get("parse_mode") == "HTML",
                buttons=markup.get("inline_keyboard"),
                reply_to=reply or params.get("reply_to_message_id"),
                silent=bool(params.get("disable_notification")),
                date=_now_ts(),
                edited=False,
                deleted=False,
            )
            state.events.append(("message", state.key(chat, message["id"]), "new"))
            if keyboard_changed:
                state.events.append(("keyboard", chat))
            return _as_telegram(message)

        def _sendMessage(self, params):
            return self._new(params, "text", params.get("text"))

        def _sendPhoto(self, params):
            return self._new(params, "photo", params.get("caption"))

        def _sendDocument(self, params):
            return self._new(params, "document", params.get("caption"))

        # edits
        def _target(self, params) -> dict:
            message = self.state.message(
                int(params.get("chat_id", 0)), int(params.get("message_id", 0))
            )
            if message is None or message["deleted"] or message["sender"] != "bot":
                raise _Refused("Bad Request: message to edit not found")
            return message

        def _edit(self, params, *, text=None, kind=None, buttons_only=False):
            message = self._target(params)
            before = (message["text"], message["buttons"], message["kind"])
            buttons = _markup(params.get("reply_markup")).get("inline_keyboard")
            if not buttons_only:
                message["text"] = text if text is not None else message["text"]
                message["html"] = params.get("parse_mode") == "HTML"
                message["kind"] = kind or message["kind"]
            message["buttons"] = buttons  # an edit without a keyboard drops it
            if (message["text"], message["buttons"], message["kind"]) == before:
                raise _Refused(
                    "Bad Request: message is not modified: specified new message "
                    "content and reply markup are exactly the same"
                )
            message["edited"] = True
            key = self.state.key(message["chat"], message["id"])
            self.state.events.append(("message", key, "edited"))
            return _as_telegram(message)

        def _editMessageText(self, params):
            if self._target(params)["kind"] != "text":
                raise _Refused("Bad Request: there is no text in the message to edit")
            return self._edit(params, text=params.get("text", ""))

        def _editMessageCaption(self, params):
            if self._target(params)["kind"] == "text":
                raise _Refused(
                    "Bad Request: there is no caption in the message to edit"
                )
            return self._edit(params, text=params.get("caption", ""))

        def _editMessageMedia(self, params):
            if self._target(params)["kind"] == "text":
                raise _Refused("Bad Request: there is no media in the message to edit")
            media = _markup(params.get("media"))
            return self._edit(params, text=media.get("caption"), kind="photo")

        def _editMessageReplyMarkup(self, params):
            return self._edit(params, buttons_only=True)

        # deletes
        def _deleteMessage(self, params):
            chat = int(params.get("chat_id", 0))
            message = self.state.message(chat, int(params.get("message_id", 0)))
            if message is None or message["deleted"]:
                raise _Refused("Bad Request: message to delete not found")
            message["deleted"] = True
            key = self.state.key(chat, message["id"])
            self.state.events.append(("message", key, "deleted"))
            return True

        def _deleteMessages(self, params):
            ids = params.get("message_ids") or []
            ids = json.loads(ids) if isinstance(ids, str) else ids
            for number in ids:
                try:
                    self._deleteMessage({**params, "message_id": number})
                except _Refused:
                    pass
            return True

        def _answerCallbackQuery(self, params):
            if params.get("text"):
                alert = bool(params.get("show_alert"))
                self.state.events.append(("toast", params["text"], alert))
            return True

    return SimWire


class _Refused(Exception):
    pass


def _markup(value) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _label(button) -> str:
    return button.get("text", "") if isinstance(button, dict) else str(button)


def _now_ts() -> int:
    return int(CLOCK.now().timestamp())


def _as_telegram(message: dict) -> dict:
    """The message as Telegram describes it: for the bot's own return values and for
    a tap's ``callback_query.message``."""
    sender = (
        {"id": BOT_ID, "is_bot": True, "first_name": "bot", "username": "b"}
        if message["sender"] == "bot"
        else {"id": message["chat"], "is_bot": False, "first_name": "person"}
    )
    body = {
        "message_id": message["id"],
        "date": message["date"],
        "chat": {"id": message["chat"], "type": "private"},
        "from": sender,
    }
    if message["kind"] == "photo":
        body["photo"] = [
            {"file_id": "sim-card", "file_unique_id": "c", "width": 9, "height": 9}
        ]
        body["caption"] = message["text"]
    else:
        body["text"] = message["text"]
    if message.get("buttons"):
        body["reply_markup"] = {"inline_keyboard": message["buttons"]}
    if message["edited"]:
        body["edit_date"] = _now_ts()
    return body


# --- showing it -----------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")


def _plain(message: dict) -> str:
    text = message["text"] or ""
    if message.get("html"):
        text = html.unescape(_TAG.sub("", text))
    return text


def _render(state: world.State, message: dict, mark: str = "") -> str:
    """One message as an agent reads it: number, who, flags, text, then buttons."""
    flags = []
    if message["sender"] == "person":
        head = f"{mark}#{message['id']} you"
        if message.get("reply_to"):
            head += f" ↩ #{message['reply_to']}"
        if message.get("deleted"):
            head += " (DELETED)"
        kind = {"voice": "🎤 voice: ", "photo": "📷 photo: "}.get(message["kind"], "")
        return f"{head}: {kind}{message['text']}"
    if message.get("reply_to"):
        flags.append(f"↩ #{message['reply_to']}")
    if message["kind"] != "text":
        flags.append(message["kind"])
    if message.get("silent"):
        flags.append("silent")
    if message.get("edited"):
        flags.append("edited")
    if message.get("deleted"):
        flags.append("DELETED")
    head = f"{mark}#{message['id']} bot"
    if flags:
        head += " (" + ", ".join(flags) + ")"
    lines = [head + ":"]
    lines += ["  " + line for line in _plain(message).splitlines()]
    if message.get("deleted"):
        return "\n".join(lines)  # its buttons went with it
    for row in message.get("buttons") or []:
        lines.append("  " + " ".join(f"[{_label(b)}]" for b in row))
    return "\n".join(lines)


def _keyboard_line(state: world.State, chat: int) -> str | None:
    """The reply keyboard on one line, its rows apart: ``[a] [b] | [c]``."""
    rows = state.data["keyboards"].get(str(chat))
    if not rows:
        return None
    return "keyboard: " + " | ".join(
        " ".join(f"[{label}]" for label in row) for row in rows
    )


def _print_events(state: world.State) -> None:
    """What the command changed, each message once, in its final state."""
    current = state.person()[1]["id"] if state.data["current"] else None
    done = set()
    marks = {}
    for event in state.events:
        if event[0] == "message":
            marks.setdefault(event[1], set()).add(event[2])
    for event in state.events:
        if event[0] == "toast":
            print(f"toast{' (alert)' if event[2] else ''}: {event[1]}")
        elif event[0] == "keyboard":
            line = _keyboard_line(state, event[1]) or "keyboard: removed"
            if event[1] != current:
                line = f"[in {state.name_of(event[1])}'s chat] " + line
            print(line)
        elif event[0] == "model":
            print(f"!! A MODEL CALL WAS ATTEMPTED AND REFUSED: {event[1]}")
        elif event[0] == "error":
            print(f"!! APP ERROR: {event[1]}")
        elif event[0] == "say":
            print(_render(state, state.by_key(event[1]), "> "))
        elif event[0] == "message" and event[1] not in done:
            done.add(event[1])
            message = state.by_key(event[1])
            kinds = marks[event[1]]
            mark = "+ " if "new" in kinds else ("- " if kinds == {"deleted"} else "~ ")
            text = _render(state, message, mark)
            if message["chat"] != current:
                text = f"[in {state.name_of(message['chat'])}'s chat] " + text
            print(text)
    if not state.events:
        print("(no change)")


# --- driving the application ----------------------------------------------------------


async def _settle() -> None:
    import app.services.telegram as tg

    for _ in range(50):
        reads = list(tg._meal_reads)
        if not reads:
            return
        await asyncio.gather(*reads, return_exceptions=True)


def drive(state: world.State, steps) -> None:
    """``steps(application)`` inside one run of the real application."""
    import app.services.telegram as tg

    async def run():
        tg._card_delivery._screens.clear()
        application = tg.create_bot_application()
        wire = _wire_class()(state)
        application.bot._request = (wire, wire)
        await application.initialize()
        world.restore_memory(state, application)
        await application.start()
        try:
            await steps(application)
            await _settle()
        finally:
            world.save_memory(state, application)
            await application.stop()
            await application.shutdown()

    asyncio.run(run())


def _person_dict(name: str, person: dict) -> dict:
    return {
        "id": person["id"],
        "is_bot": False,
        "first_name": name,
        "language_code": "ru",
    }


def _send(state: world.State, kind: str, shown: str, **fields) -> None:
    """A message from the current person, through the application."""
    from telegram import Update

    name, person = state.person()
    number = state.next_message(person["id"])
    message = {
        "message_id": number,
        "date": _now_ts(),
        "chat": {"id": person["id"], "type": "private", "first_name": name},
        "from": _person_dict(name, person),
        **fields,
    }
    reply_to = fields.get("reply_to_message", {}).get("message_id")
    state.add_message(
        id=number,
        chat=person["id"],
        sender="person",
        kind=kind,
        text=shown,
        reply_to=reply_to,
        date=message["date"],
        edited=False,
        deleted=False,
    )
    state.events.append(("say", state.key(person["id"], number)))
    update = {"update_id": state.next_update(), "message": message}

    async def steps(application):
        await application.process_update(Update.de_json(update, application.bot))

    drive(state, steps)


def _tap_update(state: world.State, message: dict, data: str) -> dict:
    name, person = state.person()
    return {
        "update_id": state.next_update(),
        "callback_query": {
            "id": str(state.data["next_update"]),
            "from": _person_dict(name, person),
            "chat_instance": "sim",
            "data": data,
            "message": _as_telegram(message),
        },
    }


# --- commands -------------------------------------------------------------------------


def _user(db, telegram_id: int):
    from app.models import User

    return db.query(User).filter_by(telegram_id=telegram_id).one_or_none()


def _zone_of(state: world.State) -> ZoneInfo:
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.settings_screen import user_zone

    if state.data["current"] is None:
        return ZoneInfo(settings.DEFAULT_TIMEZONE)
    with SessionLocal() as db:
        return user_zone(_user(db, state.person()[1]["id"]))


def _clock_line(state: world.State) -> str:
    zone = _zone_of(state)
    local = CLOCK.now().astimezone(zone)
    return (
        f"clock: {local:%a %Y-%m-%d %H:%M} {zone.key} "
        f"({CLOCK.now().astimezone(UTC):%H:%M} UTC)"
    )


def cmd_new(state, args):
    from app.db.session import SessionLocal
    from app.models import User
    from app.services import invites

    if args.name in state.people:
        raise SystemExit(f"error: {args.name!r} exists; `as {args.name}` to switch")
    if args.zone:
        try:
            ZoneInfo(args.zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise SystemExit(f"error: unknown zone {args.zone!r}")
    owners = state.owners()
    if not args.owner and not owners:
        raise SystemExit(
            "error: someone has to invite them: make the owner first (--owner)"
        )
    telegram_id = FIRST_PERSON_ID + len(state.people)
    state.people[args.name] = {"id": telegram_id, "owner": bool(args.owner)}
    state.data["current"] = args.name
    world.apply_people(state)
    with SessionLocal() as db:
        if not args.owner:
            invites.invite(
                db, invites.Person(telegram_id, first_name=args.name), owners[0]
            )
        if args.zone:
            user = _user(db, telegram_id) or User(telegram_id=telegram_id)
            user.settings = {**(user.settings or {}), "timezone": args.zone}
            db.add(user)
            db.commit()
    role = "owner" if args.owner else f"invited by {state.name_of(owners[0])}"
    zone = args.zone or "no zone chosen"
    print(
        f"new: {args.name} (id {telegram_id}, {role}, {zone}); "
        f"now acting as {args.name}"
    )
    start = {"text": "/start", "entities": [_command_entity("/start")]}
    _send(state, "text", "/start", **start)


def _command_entity(text: str) -> dict:
    return {"type": "bot_command", "offset": 0, "length": len(text.split()[0])}


def cmd_as(state, args):
    state.person(args.name)
    state.data["current"] = args.name
    print(f"now acting as {args.name}")
    print(_clock_line(state))


def cmd_say(state, args):
    fields = {"text": args.text}
    if args.text.startswith("/"):
        fields["entities"] = [_command_entity(args.text)]
    _send(state, "text", args.text, **fields)


def cmd_reply(state, args):
    _, person = state.person()
    target = state.message(person["id"], args.number)
    if target is None:
        raise SystemExit(f"error: no message #{args.number} in this chat")
    _send(
        state, "text", args.text, text=args.text, reply_to_message=_as_telegram(target)
    )


def _file(state, kind: str, content: str) -> str:
    file_id = f"sim-{kind}-{state.next_file()}"
    state.data["files"][file_id] = {"kind": kind, "content": content}
    return file_id


def cmd_voice(state, args):
    file_id = _file(state, "voice", args.transcript)
    voice = {
        "file_id": file_id,
        "file_unique_id": file_id,
        "duration": max(1, len(args.transcript.split())),
        "mime_type": "audio/ogg",
        "file_size": 2000 + len(args.transcript.encode()),
    }
    _send(state, "voice", args.transcript, voice=voice)


def cmd_photo(state, args):
    file_id = _file(state, "photo", args.seen)
    sizes = [
        {
            "file_id": file_id + "-s",
            "file_unique_id": file_id + "s",
            "width": 90,
            "height": 90,
        },
        {"file_id": file_id, "file_unique_id": file_id, "width": 900, "height": 900},
    ]
    state.data["files"][file_id + "-s"] = state.data["files"][file_id]
    fields = {"photo": sizes}
    shown = args.seen
    if args.caption:
        fields["caption"] = args.caption
        shown += f" | caption: {args.caption}"
    _send(state, "photo", shown, **fields)


def _tiers(wanted: str) -> tuple:
    """How a label is matched, the strictest first: exact, any case, a substring."""
    norm = " ".join(wanted.split())
    return (
        lambda label: label == norm,
        lambda label: label.casefold() == norm.casefold(),
        lambda label: norm.casefold() in label.casefold(),
    )


def _hits(labels: list, test) -> list:
    return [label for label in labels if test(" ".join(label.split()))]


def _match(labels: list, wanted: str) -> list:
    for test in _tiers(wanted):
        found = _hits(labels, test)
        if found:
            return found
    return []


def cmd_tap(state, args):
    from telegram import Update

    _, person = state.person()
    chat = state.chat(person["id"])
    if args.msg is not None:
        candidates = [m for m in chat if m["id"] == args.msg]
        if not candidates:
            raise SystemExit(f"error: no message #{args.msg} in this chat")
        if candidates[0]["deleted"]:
            raise SystemExit(f"error: #{args.msg} was deleted")
    else:
        candidates = [
            m for m in reversed(chat) if m.get("buttons") and not m["deleted"]
        ]
    # A stricter match on an older message beats a looser one on a newer message.
    for test in _tiers(args.label):
        for message in candidates:
            buttons = [b for row in message.get("buttons") or [] for b in row]
            found = _hits([_label(b) for b in buttons], test)
            if len(set(found)) > 1:
                raise SystemExit(
                    f"error: {args.label!r} matches several buttons on "
                    f"#{message['id']}: " + ", ".join(f"[{f}]" for f in found)
                )
            if not found:
                continue
            button = next(b for b in buttons if _label(b) == found[0])
            if "callback_data" not in button:
                print(f"[{found[0]}] opens {button.get('url', 'something outside')}")
                return
            update = _tap_update(state, message, button["callback_data"])
            print(f"> tap [{found[0]}] on #{message['id']}")

            async def steps(application):
                await application.process_update(
                    Update.de_json(update, application.bot)
                )

            drive(state, steps)
            return
    if args.msg is None:
        keys = [
            k for row in state.data["keyboards"].get(str(person["id"]), []) for k in row
        ]
        found = _match(keys, args.label)
        if len(set(found)) == 1:
            print(f"> keyboard [{found[0]}]")
            _send(state, "text", found[0], text=found[0])
            return
    latest = next((m for m in candidates), None)
    shown = _render(state, latest) if latest else "no message with buttons in this chat"
    keyboard = _keyboard_line(state, person["id"]) or "keyboard: none"
    raise SystemExit(f"error: no button {args.label!r}.\n{shown}\n{keyboard}")


def _parse_local(state, text: str) -> datetime:
    zone = _zone_of(state)
    now_local = CLOCK.now().astimezone(zone)
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        hour, minute = (int(x) for x in text.split(":"))
        return now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"error: not a time: {text!r} (2026-11-06T21:00 or 21:00)")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return moment


def cmd_clock(state, args):
    if args.when:
        moment = _parse_local(state, args.when)
        if moment < CLOCK.now() - timedelta(seconds=1):
            raise SystemExit(
                f"error: {moment:%Y-%m-%d %H:%M} is in the past; time only goes forward"
            )
        CLOCK.set(moment)
    print(_clock_line(state))


def _tick_all(state, application, now) -> list:
    """Runs every push once at ``now``; returns lines for those that did something."""
    from app.db.session import SessionLocal
    from tests.sim.pushes import PUSHES

    async def run():
        said = []
        for name, push in PUSHES.items():
            with SessionLocal() as db:
                counts = await push(db, application.bot, now)
            if any(counts.values()) if isinstance(counts, dict) else counts:
                said.append(f"tick: {name}: {counts}")
        return said

    return run()


def cmd_tick(state, args):
    lines = []

    async def steps(application):
        lines.extend(await _tick_all(state, application, CLOCK.now()))

    drive(state, steps)
    print(_clock_line(state))
    for line in lines or ["tick: nothing was due"]:
        print(line)


def cmd_advance(state, args):
    from tests.sim.pushes import BEAT_MINUTES

    span = world.parse_span(args.span)
    start = CLOCK.now()
    end = start + span
    if args.tick:
        beats = []
        moment = start.replace(second=0, microsecond=0)
        moment -= timedelta(minutes=moment.minute % BEAT_MINUTES)
        while True:
            moment += timedelta(minutes=BEAT_MINUTES)
            if moment > end:
                break
            beats.append(moment)
        lines = []

        async def steps(application):
            for beat in beats:
                CLOCK.set(beat)
                for line in await _tick_all(state, application, beat):
                    local = beat.astimezone(_zone_of(state))
                    lines.append(f"{local:%a %H:%M} {line}")

        drive(state, steps)
        for line in lines:
            print(line)
        print(f"ticked {len(beats)} times (every {BEAT_MINUTES} min)")
    CLOCK.set(end)
    print(_clock_line(state))


def cmd_chat(state, args):
    name, person = state.person()
    messages = state.chat(person["id"])
    if args.last:
        messages = messages[-args.last :]
    print(f"chat of {name} ({len(state.chat(person['id']))} messages):")
    for message in messages:
        print(_render(state, message))
    keyboard = _keyboard_line(state, person["id"])
    if keyboard:
        print(keyboard)


def cmd_day(state, args):
    from sqlalchemy import select

    from app.crud.crud_meal_item import crud_meal_item
    from app.db.session import SessionLocal
    from app.models import Meal

    name, person = state.person()
    zone = _zone_of(state)
    day = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else CLOCK.now().astimezone(zone).date()
    )
    with SessionLocal() as db:
        user = _user(db, person["id"])
        meals = (
            db.scalars(
                select(Meal).where(
                    Meal.user_id == user.id,
                    Meal.deleted_at.is_(None),
                    Meal.merged_into_id.is_(None),
                )
            ).all()
            if user
            else []
        )
        rows = []
        for meal in meals:
            moment = meal.meal_time
            if moment.tzinfo is None:  # SQLite hands it back naive, written in UTC
                moment = moment.replace(tzinfo=UTC)
            local = moment.astimezone(zone)
            if local.date() != day:
                continue
            names = [i.name for i in crud_meal_item.live(db, meal.id)]
            flags = [
                f"precision={meal.time_precision}",
                f"source={meal.time_source}",
            ]
            if meal.time_phrase:
                flags.append(f"phrase={meal.time_phrase!r}")
            if meal.calories is not None:
                flags.append(f"{meal.calories:.0f} kcal")
            if meal.resolution_source:
                flags.append(f"via={meal.resolution_source}")
            if meal.is_draft:
                flags.append("DRAFT")
            if meal.reading_status not in (None, "done"):
                flags.append(f"reading={meal.reading_status}")
            label = ", ".join(names) or meal.description or "(no name)"
            rows.append((local, f"  {local:%H:%M}  {label}  [{' '.join(flags)}]"))
    print(f"day {day:%a %Y-%m-%d} of {name} ({zone.key}): {len(rows)} records")
    for _, line in sorted(rows):
        print(line)


# --- the entry point ------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sim.sh", description=__doc__)
    parser.add_argument("state", help="the world's folder; made on first use")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="a person: invited, or --owner")
    p.add_argument("name")
    p.add_argument("--owner", action="store_true")
    p.add_argument("--zone", help="IANA zone, e.g. Europe/Berlin; absent = none chosen")
    p.set_defaults(run=cmd_new)

    p = sub.add_parser("as", help="act as this person from now on")
    p.add_argument("name")
    p.set_defaults(run=cmd_as)

    p = sub.add_parser("say", help="send a text message")
    p.add_argument("text")
    p.set_defaults(run=cmd_say)

    p = sub.add_parser("voice", help="send a voice note heard as this text")
    p.add_argument("transcript")
    p.set_defaults(run=cmd_voice)

    p = sub.add_parser("photo", help="send a photo the fake vision reads as this text")
    p.add_argument("seen")
    p.add_argument("--caption")
    p.set_defaults(run=cmd_photo)

    p = sub.add_parser("tap", help="tap a button (inline, else the keyboard)")
    p.add_argument("label")
    p.add_argument("--msg", type=int, help="on this message, not the latest with it")
    p.set_defaults(run=cmd_tap)

    p = sub.add_parser("reply", help="reply to message N with text")
    p.add_argument("number", type=int)
    p.add_argument("text")
    p.set_defaults(run=cmd_reply)

    p = sub.add_parser("clock", help="show, or set to a local time (forward only)")
    p.add_argument("when", nargs="?", help="2026-11-06T21:00 or 21:00, person's zone")
    p.set_defaults(run=cmd_clock)

    p = sub.add_parser("advance", help="move the clock: 40m, 2h, 1d, 1h30m")
    p.add_argument("span")
    p.add_argument("--tick", action="store_true", help="tick at every beat on the way")
    p.set_defaults(run=cmd_advance)

    p = sub.add_parser("tick", help="run every periodic push due now")
    p.set_defaults(run=cmd_tick)

    p = sub.add_parser("chat", help="the person's chat as text")
    p.add_argument("--last", type=int)
    p.set_defaults(run=cmd_chat)

    p = sub.add_parser("day", help="the person's records of the local day")
    p.add_argument("date", nargs="?", help="YYYY-MM-DD, default today")
    p.set_defaults(run=cmd_day)
    return parser


_SENDS = ("say", "voice", "photo", "tap", "reply")


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    state = world.boot(args.state)
    before = len(state.data["model_calls"])
    errors_before = len(state.data["errors"])
    try:
        args.run(state, args)
        if state.events or args.command in _SENDS:
            _print_events(state)
    finally:
        state.save()
    if len(state.data["model_calls"]) > before:
        return 3  # a model call was tried: the run is not what it claims to be
    if len(state.data["errors"]) > errors_before:
        return 4  # the app logged an error and went on: a crash it swallowed
    return 0


if __name__ == "__main__":
    sys.exit(main())
