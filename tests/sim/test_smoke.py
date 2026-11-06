"""The chat simulator, run as an agent runs it: one process per command, one world
folder carried between them."""

import argparse
import fcntl
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tests.sim import world

REPO = Path(__file__).resolve().parents[2]


def _clean_env() -> dict:
    """What ``scripts/sim.sh`` gives the process: no key, no setting of the shell."""
    return {
        "HOME": os.environ.get("HOME", "/tmp"),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(REPO),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _run(argv: list, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=REPO,
        env=env or _clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _sim(folder: Path, *args, code=0) -> str:
    done = _run([sys.executable, "-m", "tests.sim.cli", str(folder), *args])
    out = done.stdout + done.stderr
    assert done.returncode == code, out
    assert "Traceback" not in out, out
    return out


def _number(out: str, text: str) -> int:
    """The number of the new or changed bot message whose first line is ``text``."""
    found = re.search(r"^[+~] #(\d+) bot[^\n]*:\n  " + re.escape(text), out, re.M)
    assert found, out
    return int(found.group(1))


def test_a_dish_starred_and_logged_from_the_star_list(tmp_path):
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner")
    out = _sim(w, "new", "Anna")
    assert "👋 Anna теперь в боте." in out  # the owner hears, in her own chat
    assert "[in Julia's chat]" in out
    _sim(w, "tap", "Всё")

    out = _sim(w, "say", "гречка с курицей и салат")
    assert "> #" in out and "you: гречка с курицей и салат" in out
    assert "Записано: гречка с курицей и салат." in out
    assert "[✏️ Исправить]" in out

    out = _sim(w, "tap", "🍲 Блюда")  # a reply-keyboard key
    assert "[Салат · 1 раз]" in out
    _sim(w, "tap", "Салат")
    out = _sim(w, "tap", "⭐ В избранное")
    assert "[✓ ⭐ В избранном]" in out
    assert re.search(r"^~ #\d+ bot \(edited\):$", out, re.M), out  # edited in place

    _sim(w, "advance", "2h")
    out = _sim(w, "tap", "⭐ Избранное")
    assert "[Салат]" in out
    out = _sim(w, "tap", "Салат")
    assert "Записано: салат." in out
    assert "toast: Записано" in out

    out = _sim(w, "day")
    assert "2 records" in out, out
    assert "гречка с курицей, салат" in out
    assert "precision=exact source=sent" in out
    lines = [line.strip() for line in out.splitlines()]
    assert any(line.startswith("14:00  салат") for line in lines), out  # Tbilisi

    out = _sim(w, "chat", "--last", "2")
    assert out.splitlines()[0] == "chat of Anna (11 messages):"
    # the reply keyboard keeps its rows
    assert "keyboard: [🍽 Добавить приём пищи] [⭐ Избранное] | [📊 Статистика]" in out

    # every periodic push runs at the world's clock: Sunday 22:00 is the week card
    _sim(w, "clock", "2026-11-08T21:59")
    assert "nothing was due" in _sim(w, "tick")
    out = _sim(w, "advance", "15m", "--tick")
    assert "week card: {'sent': 1" in out, out
    assert "📊 Неделя, 2–8 ноября" in out

    state = json.loads((w / world.STATE_FILE).read_text(encoding="utf-8"))
    assert state["model_calls"] == []
    with sqlite3.connect(w / world.DB_FILE) as db:
        rows = dict(db.execute("SELECT telegram_id, settings FROM users").fetchall())
    anna = json.loads(rows[world.FIRST_PERSON_ID + 1] or "{}")
    assert "timezone" not in anna  # D1: no zone chosen stays absent


def test_a_zone_voice_photo_and_clock(tmp_path):
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner", "--zone", "Europe/Berlin")
    assert "Europe/Berlin" in _sim(w, "clock", "21:00")
    out = _sim(w, "voice", "борщ")
    assert "🎤 voice: борщ" in out and "Записано: борщ." in out
    out = _sim(w, "photo", "омлет и кофе")
    assert "Записано: омлет и кофе." in out
    out = _sim(w, "day")
    assert "21:00  борщ" in out and "омлет, кофе" in out, out
    out = _sim(w, "clock", "2026-11-01T10:00", code=1)
    assert "2026-11-01 10:00 is in the past; time only goes forward" in out
    out = _sim(w, "clock", "08:00", code=1)
    assert "08:00 is in the past; time only goes forward" in out

    # a person's row is stamped with the world's clock, not the machine's
    _sim(w, "advance", "3d")
    _sim(w, "new", "Anna")
    with sqlite3.connect(w / world.DB_FILE) as db:
        stamps = db.execute(
            "SELECT created_at, updated_at FROM users WHERE telegram_id = ?",
            (world.FIRST_PERSON_ID + 1,),
        ).fetchone()
    assert stamps[0].startswith("2026-11-09 20:"), stamps
    assert stamps[1].startswith("2026-11-09 20:"), stamps


def test_reply_tap_on_a_message_and_numbers_per_chat(tmp_path):
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner")
    out = _sim(w, "new", "Anna")
    assert "> #1 you: /start" in out  # every chat counts from #1, as in Telegram
    _sim(w, "tap", "Всё")
    out = _sim(w, "say", "гречка")
    logged = _number(out, "Записано: гречка.")

    out = _sim(w, "reply", str(logged), "ещё хлеб")
    assert f"you ↩ #{logged}: ещё хлеб" in out
    assert "Добавлено к приёму" in out
    assert f"~ #{logged} bot" in out and "Записано: гречка и хлеб." in out

    out = _sim(w, "tap", "Время", "--msg", str(logged))
    assert f"> tap [🕐 Время] on #{logged}" in out
    assert "🕐 Время приёма" in out

    out = _sim(w, "as", "Julia")
    assert "now acting as Julia" in out and "clock: Fri 2026-11-06 12:00" in out
    assert "error: no message #" in _sim(w, "reply", str(logged), "x", code=1)
    out = _sim(w, "chat")
    assert out.splitlines()[0] == "chat of Julia (3 messages):"
    assert "#3 bot (silent):\n  👋 Anna теперь в боте." in out
    _sim(w, "as", "Anna")
    assert "🕐 Время приёма" in _sim(w, "chat")


_DELETE = """
import sys
from tests.sim import cli, world
state = world.boot(sys.argv[1])
chat, number = state.people["Julia"]["id"], int(sys.argv[2])

async def steps(application):
    await application.bot.delete_message(chat, number)

cli.drive(state, steps)
cli._print_events(state)
state.save()
"""


def test_a_deleted_message_is_marked_and_loses_its_buttons(tmp_path):
    """What R1/R4 read: a new reminder deletes the last one."""
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner")
    logged = _number(_sim(w, "say", "суп"), "Записано: суп.")

    done = _run([sys.executable, "-c", _DELETE, str(w), str(logged)])
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert f"- #{logged} bot (" in out and "DELETED" in out, out
    assert "[✏️ Исправить]" not in out  # its buttons went with it

    out = _sim(w, "chat")
    assert re.search(rf"^#{logged} bot \([^)]*DELETED\):$", out, re.M), out
    assert "[✏️ Исправить]" not in out
    out = _sim(w, "tap", "Исправить", code=1)
    assert "no button 'Исправить'" in out
    out = _sim(w, "tap", "Исправить", "--msg", str(logged), code=1)
    assert f"#{logged} was deleted" in out


def test_a_missing_button_names_the_ones_there(tmp_path):
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner")
    done = subprocess.run(
        [sys.executable, "-m", "tests.sim.cli", str(w), "tap", "Нет такой"],
        cwd=REPO,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 1
    assert "no button 'Нет такой'" in done.stderr
    assert "keyboard: [🍽 Добавить приём пищи] [⭐ Избранное] | " in done.stderr


_GUARD = """
import asyncio, sys
from tests.sim import world
state = world.boot(sys.argv[1])
from openai import AsyncOpenAI
import httpx

async def main():
    try:
        await AsyncOpenAI(api_key="x").chat.completions.create(model="m", messages=[])
    except world.ModelTouched as e:
        print("model:", e)
    try:
        async with httpx.AsyncClient() as client:
            await client.get("https://example.com")
    except httpx.ConnectError as e:
        print("network:", e)

asyncio.run(main())
print("recorded:", state.data["model_calls"])
"""


def test_the_world_refuses_a_model_and_the_network(tmp_path):
    """The known positive for ``model_calls == []`` above: a call that tries is
    caught and written down, not quietly made."""
    done = subprocess.run(
        [sys.executable, "-c", _GUARD, str(tmp_path / "world")],
        cwd=REPO,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "model: sim: a model call was attempted (/chat/completions)" in done.stdout
    assert "network: sim: no network (example.com)" in done.stdout
    assert "recorded: ['/chat/completions']" in done.stdout


# The real food reading put back: the world must refuse its model call and exit 3.
_MODEL = """
import sys
from tests.sim import cli, world
fakes = world._install_fakes

def fakes_but_the_real_reading(state):
    import app.services.telegram as tg
    real = tg._analyze_and_parse
    fakes(state)
    tg._analyze_and_parse = real

world._install_fakes = fakes_but_the_real_reading
sys.exit(cli.main([sys.argv[1], "say", "суп"]))
"""

# A function the app calls on every text, made to raise: the app logs it and goes on.
_CRASH = """
import sys
from tests.sim import cli, world
fakes = world._install_fakes

def fakes_and_a_crash(state):
    from app.services import meal_card
    fakes(state)

    def has_slot(db, telegram_id):
        raise RuntimeError("sim: injected crash")

    meal_card.has_slot = has_slot

world._install_fakes = fakes_and_a_crash
sys.exit(cli.main([sys.argv[1], "say", "суп"]))
"""


def test_a_model_call_through_the_cli_exits_3(tmp_path):
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner")
    done = _run([sys.executable, "-c", _MODEL, str(w)])
    out = done.stdout + done.stderr
    assert done.returncode == 3, out
    assert "!! A MODEL CALL WAS ATTEMPTED AND REFUSED" in done.stdout, out
    state = json.loads((w / world.STATE_FILE).read_text(encoding="utf-8"))
    assert state["model_calls"], state["model_calls"]


def test_an_error_the_app_swallows_exits_4(tmp_path):
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner")
    done = _run([sys.executable, "-c", _CRASH, str(w)])
    out = done.stdout + done.stderr
    assert done.returncode == 4, out
    assert "!! APP ERROR:" in done.stdout and "sim: injected crash" in out, out
    state = json.loads((w / world.STATE_FILE).read_text(encoding="utf-8"))
    assert state["errors"], state
    # the next command exits 0 again: what counts is an error during this command
    assert "chat of Julia" in _sim(w, "chat")


def test_a_busy_world_is_refused_not_overwritten(tmp_path):
    w = tmp_path / "world"
    _sim(w, "new", "Julia", "--owner")
    with open(w / world.LOCK_FILE, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        env = {**_clean_env(), "SIM_LOCK_WAIT": "0.3"}
        done = _run([sys.executable, "-m", "tests.sim.cli", str(w), "chat"], env)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "error: world busy" in done.stderr
    assert "chat of Julia" in _sim(w, "chat")  # free again once the other ends


def test_sim_sh_runs_a_command(tmp_path):
    done = subprocess.run(
        [
            str(REPO / "scripts" / "sim.sh"),
            str(tmp_path / "w"),
            "new",
            "Julia",
            "--owner",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = done.stdout + done.stderr
    assert done.returncode == 0, out
    assert "new: Julia (id 100001, owner, no zone chosen)" in out
    assert "Что делаем?" in out


def _wire(tmp_path, kind: str):
    from tests.sim import cli

    state = world.State(tmp_path)
    state.add_message(
        id=1,
        chat=7,
        sender="bot",
        kind=kind,
        text="старое",
        html=False,
        buttons=None,
        reply_to=None,
        silent=False,
        date=0,
        edited=False,
        deleted=False,
    )
    return cli._wire_class()(state), cli._Refused


@pytest.mark.parametrize(
    "kind, method, params, refusal",
    [
        ("photo", "editMessageText", {"text": "x"}, "no text in the message"),
        ("text", "editMessageCaption", {"caption": "x"}, "no caption in the message"),
        ("text", "editMessageMedia", {"media": "{}"}, "no media in the message"),
    ],
)
def test_the_wire_refuses_an_edit_telegram_refuses(
    tmp_path, kind, method, params, refusal
):
    wire, refused = _wire(tmp_path, kind)
    with pytest.raises(refused, match=refusal):
        wire._result(method, {"chat_id": 7, "message_id": 1, **params})
    assert wire.state.message(7, 1)["kind"] == kind  # nothing changed


def test_the_wire_allows_the_edits_telegram_allows(tmp_path):
    wire, _ = _wire(tmp_path, "photo")
    wire._result("editMessageCaption", {"chat_id": 7, "message_id": 1, "caption": "y"})
    assert wire.state.message(7, 1)["text"] == "y"
    wire, _ = _wire(tmp_path, "text")
    wire._result("editMessageText", {"chat_id": 7, "message_id": 1, "text": "y"})
    assert wire.state.message(7, 1)["text"] == "y"
    assert wire.state.message(8, 1) is None  # another chat's #1 is another message


def test_tap_prefers_an_exact_label_on_an_older_message(tmp_path, monkeypatch, capsys):
    from tests.sim import cli

    state = world.State(tmp_path)
    state.people["Julia"] = {"id": 7, "owner": True}
    state.data["current"] = "Julia"
    for number, label in ((1, "Салат"), (2, "Салат · 1 раз")):
        state.add_message(
            id=number,
            chat=7,
            sender="bot",
            kind="text",
            text="",
            html=False,
            buttons=[[{"text": label, "callback_data": f"cb{number}"}]],
            reply_to=None,
            silent=False,
            date=0,
            edited=False,
            deleted=False,
        )
    monkeypatch.setattr(cli, "drive", lambda *_: None)
    cli.cmd_tap(state, argparse.Namespace(label="Салат", msg=None))
    assert "> tap [Салат] on #1" in capsys.readouterr().out
    cli.cmd_tap(state, argparse.Namespace(label="1 раз", msg=None))
    assert "> tap [Салат · 1 раз] on #2" in capsys.readouterr().out


def test_sim_sh_usage_names_every_command():
    from tests.sim.cli import _parser

    script = (REPO / "scripts" / "sim.sh").read_text(encoding="utf-8")
    assert os.access(REPO / "scripts" / "sim.sh", os.X_OK)
    import argparse

    (sub,) = [
        a for a in _parser()._actions if isinstance(a, argparse._SubParsersAction)
    ]
    commands = sub.choices
    assert len(commands) == 12
    for name in commands:
        assert f"#   {name} " in script, name
    done = subprocess.run(
        [str(REPO / "scripts" / "sim.sh")], capture_output=True, text=True
    )
    assert done.returncode == 2 and "Usage:" in done.stderr


@pytest.mark.parametrize(
    "words, dishes",
    [
        ("гречка с курицей и салат", ["гречка с курицей", "салат"]),
        ("омлет, кофе и хлеб.", ["омлет", "кофе", "хлеб"]),
        ("", []),
    ],
)
def test_food_text_splits_on_commas_and_i(words, dishes):
    assert world.dishes_of(words) == dishes
    assert world.numbers_of("салат") == world.numbers_of("Салат")


def test_spans():
    assert world.parse_span("1h30m").total_seconds() == 5400
    with pytest.raises(SystemExit):
        world.parse_span("soon")
