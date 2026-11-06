"""The simulator's world: a state folder, a clock, the fakes and the wire.

``boot`` must run before any ``app`` import: it moves into the state folder (so no
``.env`` of the checkout is read), seeds the settings with placeholders (so no real key
is), points every session at the folder's own SQLite file, and patches the clock, the
model, the transcription and the network. Nothing here changes ``app/``.
"""

from __future__ import annotations

import datetime as _dt_module
import fcntl
import json
import logging
import os
import pickle
import re
import sys
import time as _time_module
import types
import zlib
from datetime import UTC, timedelta
from pathlib import Path

DB_FILE = "world.db"
STATE_FILE = "sim.json"
MEMORY_FILE = "ptb.pickle"
START = "2026-11-06T08:00:00+00:00"  # a Friday, 12:00 in Tbilisi
BOT_ID = 1
FIRST_PERSON_ID = 100_001
LOCK_FILE = ".lock"
# How long a command waits for another one on the same world before it gives up.
LOCK_WAIT_SECONDS = float(os.environ.get("SIM_LOCK_WAIT", "60"))

_REAL_DT = _dt_module.datetime
_REAL_DATE = _dt_module.date
_real_monotonic = _time_module.monotonic

# Every setting the app reads from the environment that could reach the outside or a
# real key: given here, so the shell's own values never count.
_ENV = {
    "POSTGRES_SERVER": "sim.invalid",
    "POSTGRES_USER": "sim",
    "POSTGRES_PASSWORD": "sim",
    "POSTGRES_DB": "sim",
    "POSTGRES_PORT": "5432",
    "REDIS_HOST": "sim.invalid",
    "REDIS_PORT": "6379",
    "SECRET_KEY": "sim-secret",
    "TELEGRAM_BOT_TOKEN": "1:sim",
    "OPENAI_API_KEY": "sim-no-key",
    "TZ": "UTC",
}
_UNSET = (
    "WHISPER_URL",
    "CUSTOM_EMOJI",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_ADMIN_IDS",
    "ALLOWED_TELEGRAM_IDS",
    "BOT_ACCESS_MODE",
)


# --- the clock ------------------------------------------------------------------------


class Clock:
    """The world's time. Within one command it runs as real time does (an album's
    wait still ends); between commands it stands still until ``clock`` or ``advance``.
    """

    def __init__(self):
        self.base = _REAL_DT.fromisoformat(START)
        self.started = _real_monotonic()

    def set(self, moment) -> None:
        self.base = moment.astimezone(UTC)
        self.started = _real_monotonic()

    def now(self):
        return self.base + timedelta(seconds=_real_monotonic() - self.started)


CLOCK = Clock()


class _Same(type):
    """A real datetime is one of these, so ``isinstance`` in the app still holds."""

    def __instancecheck__(cls, obj):
        return isinstance(obj, cls.__mro__[1])

    def __subclasscheck__(cls, sub):
        return issubclass(sub, cls.__mro__[1])


class SimDatetime(_REAL_DT, metaclass=_Same):
    @classmethod
    def now(cls, tz=None):
        moment = CLOCK.now()
        if tz is None:
            return moment.astimezone(UTC).replace(tzinfo=None)
        return moment.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return CLOCK.now().astimezone(UTC).replace(tzinfo=None)

    @classmethod
    def today(cls):
        return cls.utcnow()


class SimDate(_REAL_DATE, metaclass=_Same):
    @classmethod
    def today(cls):
        return CLOCK.now().astimezone(UTC).date()


def _proxy(module, **overrides):
    proxy = types.ModuleType(module.__name__)
    proxy.__dict__.update(module.__dict__)
    proxy.__dict__.update(overrides)
    return proxy


_DATETIME = _proxy(_dt_module, datetime=SimDatetime, date=SimDate)
_TIME = _proxy(
    _time_module,
    monotonic=lambda: CLOCK.now().timestamp(),
    time=lambda: CLOCK.now().timestamp(),
)


def _install_clock() -> None:
    """Every app module's ``datetime``, ``date`` and ``time`` names, to the world's."""
    swaps = {
        id(_REAL_DT): SimDatetime,
        id(_REAL_DATE): SimDate,
        id(_dt_module): _DATETIME,
        id(_time_module): _TIME,
    }
    for name, module in list(sys.modules.items()):
        if module is None or not (
            name == "app" or name.startswith(("app.", "celery_app."))
        ):
            continue
        for attr, value in list(vars(module).items()):
            if id(value) in swaps:
                setattr(module, attr, swaps[id(value)])


def parse_span(text: str) -> timedelta:
    """``40m``, ``2h``, ``1d``, ``1h30m``."""
    parts = re.findall(r"(\d+)\s*([dhms])", text.strip().lower())
    if not parts or "".join(n + u for n, u in parts) != re.sub(r"\s", "", text.lower()):
        raise SystemExit(
            f"error: not a span of time: {text!r} (try 40m, 2h, 1d, 1h30m)"
        )
    unit = {"d": "days", "h": "hours", "m": "minutes", "s": "seconds"}
    span = timedelta()
    for number, letter in parts:
        span += timedelta(**{unit[letter]: int(number)})
    return span


# --- the state ------------------------------------------------------------------------


class State:
    """What the world remembers besides its database: the people, the chats as a
    phone would show them, the clock, the files sent.

    Message numbers count per chat, as in Telegram's private chats: everyone's chat
    starts at #1, so Julia's #12 and Anna's #12 both exist. A message is kept under
    its key ``"<chat>:<number>"``."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.path = folder / STATE_FILE
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data.get("next_message"), dict):
                raise SystemExit(
                    f"error: {folder} was made by an older simulator "
                    "(one message counter for all chats); start a new folder"
                )
        else:
            self.data = {
                "clock": START,
                "current": None,
                "people": {},
                "next_message": {},
                "next_file": 1,
                "next_update": 1,
                "messages": {},
                "keyboards": {},
                "files": {},
                "model_calls": [],
                "errors": [],
            }
        self.data.setdefault("errors", [])
        self.data.setdefault("next_file", 1)
        self.events: list = []  # what this command changed, in order

    def save(self) -> None:
        self.data["clock"] = CLOCK.now().isoformat()
        text = json.dumps(self.data, ensure_ascii=False, indent=1)
        self.path.write_text(text, encoding="utf-8")

    # ids
    def next_message(self, chat_id: int) -> int:
        counters = self.data["next_message"]
        number = counters.get(str(chat_id), 1)
        counters[str(chat_id)] = number + 1
        return number

    def next_file(self) -> int:
        number = self.data["next_file"]
        self.data["next_file"] = number + 1
        return number

    def next_update(self) -> int:
        number = self.data["next_update"]
        self.data["next_update"] = number + 1
        return number

    # people
    @property
    def people(self) -> dict:
        return self.data["people"]

    def person(self, name: str | None = None) -> tuple[str, dict]:
        name = name or self.data["current"]
        if name is None:
            raise SystemExit("error: nobody yet: start with `new <name>`")
        if name not in self.people:
            known = ", ".join(self.people) or "nobody"
            raise SystemExit(f"error: no person {name!r} (known: {known})")
        return name, self.people[name]

    def name_of(self, chat_id: int) -> str:
        for name, person in self.people.items():
            if person["id"] == int(chat_id):
                return name
        return str(chat_id)

    def owners(self) -> list:
        return [p["id"] for p in self.people.values() if p.get("owner")]

    # messages
    @staticmethod
    def key(chat_id: int, number: int) -> str:
        return f"{int(chat_id)}:{int(number)}"

    def message(self, chat_id: int, number: int) -> dict | None:
        """Message #number of this chat; another chat's #number is another one."""
        return self.data["messages"].get(self.key(chat_id, number))

    def by_key(self, key: str) -> dict:
        return self.data["messages"][key]

    def add_message(self, **fields) -> dict:
        self.data["messages"][self.key(fields["chat"], fields["id"])] = fields
        return fields

    def chat(self, chat_id: int) -> list:
        return sorted(
            (m for m in self.data["messages"].values() if m["chat"] == int(chat_id)),
            key=lambda m: m["id"],
        )


# --- the fakes ------------------------------------------------------------------------

_SPLIT = re.compile(r"\s*,\s*|\s+и\s+|\s*;\s*|\s*\n\s*")


def dishes_of(words: str) -> list:
    """A text read as food: split on commas and «и», nothing else guessed."""
    return [
        part.strip(" .!") for part in _SPLIT.split(words or "") if part.strip(" .!")
    ]


def numbers_of(name: str) -> dict:
    """Fixed plausible numbers for one dish: the same name always gets the same."""
    seed = zlib.crc32(name.casefold().encode("utf-8"))
    grams = 150 + (seed % 7) * 25  # 150..300 g
    kcal = 120 + (seed // 7 % 30) * 10  # 120..410 kcal
    protein = round(kcal * (0.10 + (seed % 5) * 0.03) / 4, 1)
    fats = round(kcal * (0.20 + (seed % 3) * 0.05) / 9, 1)
    carbs = round(max(kcal - protein * 4 - fats * 9, 0) / 4, 1)
    return {"grams": grams, "kcal": kcal, "p": protein, "f": fats, "c": carbs}


def nutrition_of(words: str) -> dict:
    names = dishes_of(words) or ["еда"]
    each = [numbers_of(n) for n in names]
    grams = sum(n["grams"] for n in each)
    return {
        "foods": names,
        "calories": float(sum(n["kcal"] for n in each)),
        "protein": round(sum(n["p"] for n in each), 1),
        "fats": round(sum(n["f"] for n in each), 1),
        "carbs": round(sum(n["c"] for n in each), 1),
        "portion": f"{grams} г",
        "portion_grams": float(grams),
        "food_items": [{"name": n, "groups": []} for n in names],
    }


class ModelTouched(RuntimeError):
    """The simulator never reaches a model: a call that tries is a finding."""


def _install_fakes(state: State) -> None:
    import httpx
    from openai._base_client import AsyncAPIClient, SyncAPIClient

    import app.services.telegram as tg
    from app.core.config import settings
    from app.services import capture, voice
    from app.services.openai_service import OpenAIService
    from app.services.product_lookup_service import ResolutionResult

    def touched(where: str):
        state.data["model_calls"].append(where)
        state.events.append(("model", where))
        return ModelTouched(f"sim: a model call was attempted ({where})")

    async def no_async_model(self, cast_to, options, **_kw):
        raise touched(getattr(options, "url", "openai"))

    def no_sync_model(self, cast_to, options, **_kw):
        raise touched(getattr(options, "url", "openai"))

    AsyncAPIClient.request = no_async_model
    SyncAPIClient.request = no_sync_model

    async def no_async_network(self, request):
        raise httpx.ConnectError(f"sim: no network ({request.url.host})")

    def no_sync_network(self, request):
        raise httpx.ConnectError(f"sim: no network ({request.url.host})")

    httpx.AsyncHTTPTransport.handle_async_request = no_async_network
    httpx.HTTPTransport.handle_request = no_sync_network

    async def analyze(*, kind, payload, input_ref, telegram_id):
        return nutrition_of(payload)

    async def resolve(payload, *, telegram_id, input_ref, caption=None, more_images=()):
        seen = state.data["files"].get(input_ref, {}).get("content", "")
        nutrition = nutrition_of(seen or caption or "")
        return ResolutionResult(
            source="vision",
            confidence_tier="low",
            nutrition=nutrition,
            description=None,
            portion_grams=nutrition["portion_grams"],
            signals={"portion_grams": nutrition["portion_grams"]},
        )

    async def hear(audio, *, paid=None, local_only=False, prompt=voice.FOOD_PROMPT):
        words = audio.decode("utf-8", "replace").removeprefix("SIMVOICE:")
        if not words.strip():
            raise voice.NoSpeech()
        return voice.Heard(words, voice.LOCAL, "sim")

    async def no_portion(self, name):
        raise touched("suggest_portion")

    tg._analyze_and_parse = analyze
    tg.resolve_meal_nutrition = resolve
    voice.hear = hear
    OpenAIService.suggest_portion = no_portion
    tg._schedule_personal_food_save = lambda **_: None
    capture.ALBUM_WAIT_SECONDS = 0  # a photo here is one photo, never an album
    settings.WHISPER_URL = ""
    settings.CUSTOM_EMOJI = None
    settings.AUDIO_DIR = str(state.folder / "voice")
    settings.RENDER_DIR = str(state.folder / "render")


def _rebind_bound_defaults() -> None:
    """Column defaults that hold the real ``datetime.utcnow`` itself (bound when the
    model was imported, so ``_install_clock`` cannot reach them), to the world's.
    Defaults written as ``lambda: datetime.datetime.now(UTC)`` look the name up at
    call time and need nothing."""
    from sqlalchemy.schema import ColumnDefault

    from app.models import User

    def utcnow():
        return SimDatetime.utcnow()

    columns = User.__table__.c
    ColumnDefault(utcnow)._set_parent_with_dispatch(columns.created_at)
    ColumnDefault(utcnow)._set_parent_with_dispatch(columns.updated_at)
    ColumnDefault(utcnow, for_update=True)._set_parent_with_dispatch(columns.updated_at)


class _ErrorLog(logging.Handler):
    """An error the app logs and goes on from (a handler that crashed, a meal read
    that failed) is written down: the command prints it and exits 4."""

    def __init__(self, state: State):
        super().__init__(logging.ERROR)
        self.state = state

    def emit(self, record) -> None:
        try:
            text = f"{record.name}: {record.getMessage()}"
        except Exception:  # a message that does not format is still an error
            text = f"{record.name}: {record.msg!r}"
        self.state.data["errors"].append(text)
        self.state.events.append(("error", text))


def _install_logging(state: State) -> None:
    """The app's own handler still prints to stderr; this one only counts errors."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _ErrorLog):
            root.removeHandler(handler)
    root.addHandler(_ErrorLog(state))
    root.setLevel(os.environ.get("SIM_LOG", "WARNING"))
    for noisy in ("telegram", "httpx", "httpcore", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_LOCKS: dict = {}


def _lock(folder: Path) -> None:
    """One command at a time on a world: two at once would each load ``sim.json``
    and the last to save would win. Held until the process ends."""
    if folder in _LOCKS:
        return
    handle = open(folder / LOCK_FILE, "w")
    deadline = _real_monotonic() + LOCK_WAIT_SECONDS
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if _real_monotonic() >= deadline:
                handle.close()
                raise SystemExit(
                    f"error: world busy: another sim command is running on {folder} "
                    f"(waited {LOCK_WAIT_SECONDS:g}s)"
                )
            _time_module.sleep(0.05)
    _LOCKS[folder] = handle


def apply_people(state: State) -> None:
    """The owners are the settings' admins; everyone else came in by an invitation."""
    from app.core.config import settings

    settings.TELEGRAM_ADMIN_IDS = ",".join(str(i) for i in state.owners())
    settings.ALLOWED_TELEGRAM_IDS = ""
    settings.BOT_ACCESS_MODE = "whitelist"
    settings.TELEGRAM_BOT_TOKEN = _ENV["TELEGRAM_BOT_TOKEN"]


def _import_everything() -> None:
    """Every module the app may import at call time, imported now, so the clock
    reaches it too."""
    import importlib
    import pkgutil

    import app

    for info in pkgutil.walk_packages(app.__path__, "app."):
        if info.name == "app.main" or info.name.startswith("app.api"):
            continue
        importlib.import_module(info.name)


def boot(folder: str | Path) -> State:
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    _lock(folder)
    os.chdir(folder)  # pydantic reads ``.env`` from here: the state folder has none
    for key in _UNSET:
        os.environ.pop(key, None)
    os.environ.update(_ENV)
    _time_module.tzset()

    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    import app.db.session as session_module

    engine = create_engine(
        f"sqlite:///{folder / DB_FILE}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _busy(connection, _record):
        connection.execute("PRAGMA busy_timeout = 5000")

    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session_module.engine = engine
    session_module.SessionLocal = factory

    from app.db.base import Base

    Base.metadata.create_all(engine)  # before the rest: some modules read at import
    _import_everything()
    for name, module in list(sys.modules.items()):
        if name.startswith(("app.", "celery_app.")) and hasattr(module, "SessionLocal"):
            module.SessionLocal = factory

    state = State(folder)
    _install_logging(state)
    CLOCK.set(_REAL_DT.fromisoformat(state.data["clock"]))
    _install_clock()
    _rebind_bound_defaults()
    _install_fakes(state)
    apply_people(state)
    return state


# --- PTB's memory between commands ---------------------------------------------------


def _conversations(application) -> list:
    from telegram.ext import ConversationHandler

    found = []
    for handlers in application.handlers.values():
        found += [h for h in handlers if isinstance(h, ConversationHandler)]
    return found


def restore_memory(state: State, application) -> None:
    """What the bot keeps in memory (a wait for a name, a conversation's step): as if
    the process had lived on. Lost on a real restart; kept here, or every command would
    be one."""
    path = state.folder / MEMORY_FILE
    if not path.exists():
        return
    try:
        memory = pickle.loads(path.read_bytes())
    except Exception as e:  # a memory that does not load is a restart, not a crash
        print(f"note: bot memory not restored ({type(e).__name__})")
        return
    for user_id, data in memory.get("user_data", {}).items():
        application._user_data[user_id].update(data)
    for chat_id, data in memory.get("chat_data", {}).items():
        application._chat_data[chat_id].update(data)
    for handler, saved in zip(_conversations(application), memory.get("conv", [])):
        handler._conversations.update(saved)


def save_memory(state: State, application) -> None:
    def keepable(mapping) -> dict:
        kept = {}
        for key, value in mapping.items():
            try:
                pickle.dumps(value)
            except Exception:
                continue
            kept[key] = value
        return kept

    memory = {
        "user_data": {k: keepable(v) for k, v in application.user_data.items()},
        "chat_data": {k: keepable(v) for k, v in application.chat_data.items()},
        "conv": [keepable(h._conversations) for h in _conversations(application)],
    }
    (state.folder / MEMORY_FILE).write_bytes(pickle.dumps(memory))
