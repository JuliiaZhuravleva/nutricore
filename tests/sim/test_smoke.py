"""The chat simulator, run as an agent runs it: one process per command, one world
folder carried between them."""

import json
import os
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


def _sim(folder: Path, *args, code=0) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "tests.sim.cli", str(folder), *args],
        cwd=REPO,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = done.stdout + done.stderr
    assert done.returncode == code, out
    assert "Traceback" not in out, out
    return out


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
    assert "keyboard: [🍽 Добавить приём пищи]" in out

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
    assert "time only goes forward" in _sim(w, "clock", "08:00", code=1) + out


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
    assert "keyboard: [🍽 Добавить приём пищи]" in done.stderr


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
