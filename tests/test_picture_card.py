"""The day card as a picture (stage 5, slice D): the job client, the delivery, the
report, and the sidecar's own rules. Chrome itself is drawn only where CHROME is set."""

import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from telegram import InputMediaPhoto
from telegram.error import BadRequest, NetworkError

from app.models.meal import Meal
from app.services import stats_screen
from app.services.card_delivery import CardDelivery, fits_caption
from app.services.favorites import Screen
from app.services.picture_renderer import ERR_HOLD, PictureRenderer

PNG = b"\x89PNG\r\n\x1a\nfake"
LOADER = b"\x89PNG\r\n\x1a\nloader"
ZONE = ZoneInfo("Asia/Tbilisi")
REPORT = {"view": "day", "title": "Сегодня", "theme": "day"}
RENDER = Path(__file__).resolve().parent.parent / "render"


@pytest.fixture
def root(tmp_path):
    for name in ("in", "out"):
        (tmp_path / name).mkdir()
    (tmp_path / "heartbeat").write_text(str(int(time.time())))
    return tmp_path


def _renderer(root):
    return PictureRenderer(root, poll=0.005)


def _draw(root, suffix=".png", body=PNG):
    """The sidecar's part: answer every queued job."""
    for job in (root / "in").glob("*.json"):
        (root / "out" / f"{job.stem}{suffix}").write_bytes(body)
        job.unlink()


# --- the job client -----------------------------------------------------------


def test_a_fresh_heartbeat_is_a_renderer_an_old_or_missing_one_is_not(root):
    assert _renderer(root).alive()
    assert not PictureRenderer(root, clock=lambda: time.time() + 31).alive()
    (root / "heartbeat").unlink()
    assert not _renderer(root).alive()


def test_no_sidecar_means_no_job_and_no_ticket(root):
    (root / "heartbeat").unlink()
    assert _renderer(root).request(REPORT) is None
    assert list((root / "in").iterdir()) == []


def test_a_job_directory_that_cannot_be_written_is_no_ticket(root):
    (root / "in").rmdir()
    assert _renderer(root).request(REPORT) is None


def test_the_job_is_queued_under_its_sha_and_the_png_comes_back(root):
    renderer = _renderer(root)
    ticket = renderer.request(REPORT)
    job = root / "in" / f"{ticket.name}.json"
    assert json.loads(job.read_text()) == REPORT
    _draw(root)
    assert asyncio.run(ticket.wait(1)) == PNG
    assert renderer.request(REPORT).name == ticket.name
    assert list((root / "in").iterdir()) == []  # drawn once


def test_a_new_template_version_draws_anew(root):
    renderer = _renderer(root)
    old = renderer.request(REPORT).name
    (root / "engine").write_text("0123456789abcdef")
    assert renderer.request(REPORT).name != old


def test_a_failed_card_is_not_asked_again_until_the_hold_passes(root):
    now = [time.time()]
    renderer = PictureRenderer(root, clock=lambda: now[0], poll=0.005)
    ticket = renderer.request(REPORT)
    _draw(root, ".err", b"template: no title")
    assert asyncio.run(ticket.wait(1)) is None and ticket.failed
    assert renderer.request(REPORT) is None
    now[0] += ERR_HOLD + 1
    (root / "heartbeat").write_text(str(int(now[0])))
    assert renderer.request(REPORT) is not None


def test_a_sidecar_that_stops_mid_wait_ends_the_wait_at_once(root):
    ticket = _renderer(root).request(REPORT)
    (root / "heartbeat").unlink()
    started = time.monotonic()
    assert asyncio.run(ticket.wait(30)) is None and ticket.failed
    assert time.monotonic() - started < 1


def test_a_half_written_png_is_not_a_card(root):
    ticket = _renderer(root).request(REPORT)
    _draw(root, body=b"\x89PN")
    assert asyncio.run(ticket.wait(0.02)) is None and not ticket.failed


# --- the delivery -------------------------------------------------------------


def _message(photo=False, message_id=7):
    sent = SimpleNamespace(
        photo=[SimpleNamespace(file_id="drawn")],
        chat=SimpleNamespace(id=42),
        message_id=message_id + 1,
        edit_media=AsyncMock(return_value=SimpleNamespace(photo=None)),
        reply_text=AsyncMock(),
        reply_photo=AsyncMock(),
        delete=AsyncMock(),
    )
    return SimpleNamespace(
        photo=[object()] if photo else None,
        chat=SimpleNamespace(id=42),
        message_id=message_id,
        reply_text=AsyncMock(),
        reply_photo=AsyncMock(return_value=sent),
        edit_media=AsyncMock(return_value=SimpleNamespace(photo=None)),
        delete=AsyncMock(),
        sent=sent,
    )


def _query(message):
    return SimpleNamespace(message=message, edit_message_text=AsyncMock())


def _screen(text="<b>📊 Сегодня</b>", picture=REPORT):
    return Screen(text, "buttons", picture=picture)


def _delivery(root, tmp_path_factory, **kw):
    assets = tmp_path_factory.mktemp("assets")
    (assets / "card_loading-day.png").write_bytes(LOADER)
    kw.setdefault("fast_wait", 0.02)
    kw.setdefault("slow_wait", 2)
    return CardDelivery(_renderer(root), loading_dir=assets, **kw)


async def _settle(delivery):
    while delivery._tasks:
        await asyncio.gather(*delivery._tasks)


def test_sidecar_stopped_the_bot_answers_in_text_with_no_wait(root, tmp_path_factory):
    (root / "heartbeat").unlink()
    delivery = _delivery(root, tmp_path_factory, fast_wait=30)
    message = _message()
    started = time.monotonic()
    asyncio.run(delivery.send(message, _screen()))
    assert time.monotonic() - started < 1
    message.reply_photo.assert_not_awaited()
    (text,), kwargs = message.reply_text.await_args
    assert text == "<b>📊 Сегодня</b>" and kwargs["reply_markup"] == "buttons"


def test_sidecar_stopped_a_tap_edits_the_text_screen_in_place(root, tmp_path_factory):
    (root / "heartbeat").unlink()
    message, query = _message(), None
    query = _query(message)
    asyncio.run(_delivery(root, tmp_path_factory).tap(query, _screen()))
    query.edit_message_text.assert_awaited_once()
    message.reply_photo.assert_not_awaited()


def test_a_screen_without_a_picture_stays_text(root, tmp_path_factory):
    message = _message()
    asyncio.run(_delivery(root, tmp_path_factory).send(message, _screen(picture=None)))
    message.reply_text.assert_awaited_once()
    assert list((root / "in").iterdir()) == []


def test_a_caption_over_the_limit_is_text_and_tags_do_not_count(root, tmp_path_factory):
    assert fits_caption("<b>" * 400 + "я" * 1024)
    assert not fits_caption("я" * 1025)
    assert not fits_caption("🙂" * 513)  # Telegram counts UTF-16 units
    message = _message()
    screen = _screen("я" * 1025)
    asyncio.run(_delivery(root, tmp_path_factory).send(message, screen))
    message.reply_text.assert_awaited_once()
    assert list((root / "in").iterdir()) == []


def test_a_card_drawn_in_time_goes_straight_in_with_no_loader(root, tmp_path_factory):
    delivery = _delivery(root, tmp_path_factory)
    _renderer(root).request(REPORT)
    _draw(root)
    message = _message()
    asyncio.run(delivery.send(message, _screen()))
    (source,), kwargs = message.reply_photo.await_args
    assert source == PNG
    assert kwargs["caption"] == "<b>📊 Сегодня</b>" and kwargs["parse_mode"] == "HTML"
    assert kwargs["reply_markup"] == "buttons"
    message.sent.edit_media.assert_not_awaited()


def test_the_sent_card_is_reused_by_file_id(root, tmp_path_factory):
    delivery = _delivery(root, tmp_path_factory)
    _renderer(root).request(REPORT)
    _draw(root)

    async def twice():
        await delivery.send(_message(), _screen())
        again = _message()
        await delivery.send(again, _screen())
        return again

    assert asyncio.run(twice()).reply_photo.await_args.args == ("drawn",)


def test_a_slow_card_shows_the_loader_with_the_final_caption_then_swaps(
    root, tmp_path_factory
):
    delivery = _delivery(root, tmp_path_factory)
    message = _message()

    async def run():
        await delivery.send(message, _screen())
        (source,), kwargs = message.reply_photo.await_args
        assert source == LOADER
        assert kwargs["caption"] == "<b>📊 Сегодня</b>"
        assert kwargs["reply_markup"] == "buttons"
        _draw(root)
        await _settle(delivery)

    asyncio.run(run())
    (media,), kwargs = message.sent.edit_media.await_args
    assert isinstance(media, InputMediaPhoto)
    assert media.caption == "<b>📊 Сегодня</b>" and media.parse_mode == "HTML"
    assert kwargs["reply_markup"] == "buttons"
    assert not delivery._turns  # nothing stays behind per message


@pytest.mark.parametrize("how", ["err", "timeout", "sidecar gone", "swap refused"])
def test_a_loader_never_stays_it_turns_into_the_text_twin(root, tmp_path_factory, how):
    delivery = _delivery(root, tmp_path_factory, slow_wait=0.05)
    message = _message()
    if how == "swap refused":
        message.sent.edit_media.side_effect = BadRequest("wrong file")

    async def run():
        await delivery.send(message, _screen())
        if how == "err":
            _draw(root, ".err", b"boom")
        elif how == "sidecar gone":
            (root / "heartbeat").unlink()
        elif how == "swap refused":
            _draw(root)
        await _settle(delivery)

    asyncio.run(run())
    (text,), kwargs = message.sent.reply_text.await_args
    assert text == "<b>📊 Сегодня</b>" and kwargs["reply_markup"] == "buttons"
    message.sent.delete.assert_awaited_once()


def test_a_later_tap_wins_the_old_card_does_not_land(root, tmp_path_factory):
    delivery = _delivery(root, tmp_path_factory)
    message = _message(photo=True)
    query = _query(message)

    async def run():
        await delivery.tap(query, _screen())  # loader; the swap is waiting
        await delivery.tap(
            query, _screen("<b>📊 Вчера</b>", {**REPORT, "title": "Вчера"})
        )
        _draw(root)
        await _settle(delivery)

    asyncio.run(run())
    captions = [c.args[0].caption for c in message.edit_media.await_args_list]
    assert captions == ["<b>📊 Сегодня</b>", "<b>📊 Вчера</b>", "<b>📊 Вчера</b>"]


def test_a_tap_to_text_ends_the_waiting_card_too(root, tmp_path_factory):
    delivery = _delivery(root, tmp_path_factory)
    message = _message(photo=True)
    query = _query(message)

    async def run():
        await delivery.tap(query, _screen())
        await delivery.tap(query, _screen("<b>📊 7 дней</b>", picture=None))
        _draw(root)
        await _settle(delivery)

    asyncio.run(run())
    assert message.edit_media.await_count == 1  # only the loader
    message.reply_text.assert_awaited_once()
    message.delete.assert_awaited_once()


def test_photo_to_text_sends_new_and_deletes_old_never_edits_text(
    root, tmp_path_factory
):
    delivery = _delivery(root, tmp_path_factory)
    message = _message(photo=True)
    query = _query(message)
    asyncio.run(delivery.tap(query, _screen(picture=None)))
    query.edit_message_text.assert_not_awaited()
    message.reply_text.assert_awaited_once()
    message.delete.assert_awaited_once()


def test_text_to_photo_sends_new_and_deletes_old(root, tmp_path_factory):
    delivery = _delivery(root, tmp_path_factory)
    _renderer(root).request(REPORT)
    _draw(root)
    message = _message()
    asyncio.run(delivery.tap(_query(message), _screen()))
    message.reply_photo.assert_awaited_once()
    message.delete.assert_awaited_once()


def test_an_old_message_that_cannot_be_deleted_is_no_error(root, tmp_path_factory):
    message = _message(photo=True)
    message.delete.side_effect = BadRequest("message can't be deleted")
    asyncio.run(
        _delivery(root, tmp_path_factory).tap(_query(message), _screen(picture=None))
    )
    message.reply_text.assert_awaited_once()


def test_the_same_screen_tapped_again_while_loading_still_gets_its_card(
    root, tmp_path_factory
):
    delivery = _delivery(root, tmp_path_factory)
    message = _message(photo=True)
    query = _query(message)
    message.edit_media.side_effect = [
        SimpleNamespace(photo=None),
        BadRequest("Message is not modified"),
        SimpleNamespace(photo=None),
    ]

    async def run():
        await delivery.tap(query, _screen())
        await delivery.tap(query, _screen())
        _draw(root)
        await _settle(delivery)

    asyncio.run(run())
    assert message.edit_media.await_count == 3
    message.reply_text.assert_not_awaited()


@pytest.mark.parametrize("error", [BadRequest("no rights"), NetworkError("down")])
def test_a_photo_that_does_not_go_is_answered_in_text(root, tmp_path_factory, error):
    delivery = _delivery(root, tmp_path_factory)
    message = _message()
    message.reply_photo.side_effect = error
    asyncio.run(delivery.send(message, _screen()))
    message.reply_text.assert_awaited_once()


def test_a_tap_on_a_message_too_old_to_see_is_edited_by_id(root, tmp_path_factory):
    query = _query(SimpleNamespace(chat=SimpleNamespace(id=42), message_id=1))
    asyncio.run(_delivery(root, tmp_path_factory).tap(query, _screen()))
    query.edit_message_text.assert_awaited_once()


def test_the_loading_pictures_ship_for_both_looks():
    assets = Path(__file__).resolve().parent.parent / "app" / "assets"
    for look in (stats_screen.LOOK_DAY, stats_screen.LOOK_EVENING):
        assert (assets / f"card_loading-{look}.png").read_bytes().startswith(PNG[:8])


# --- the report ---------------------------------------------------------------


def _meal(hour, minute=0, **kw):
    nutrients = {"foods": ["тофу"], "food_groups": ["anchor"], **kw}
    return Meal(
        description="тофу",
        meal_time=datetime(2026, 10, 30, hour - 4, minute, tzinfo=UTC),
        calories=512,
        nutrients=nutrients,
        ai_analysis=nutrients,
    )


def _card(meals, **kw):
    kw.setdefault("tab", stats_screen.TODAY)
    return stats_screen.day_screen(meals, date(2026, 10, 30), zone=ZONE, **kw)


def test_the_report_says_what_was_and_holds_no_number_or_weight():
    picture = _card([_meal(8, 40), _meal(19, 50)], opened=True).picture
    assert picture["view"] == "day" and picture["title"] == "Сегодня"
    assert picture["meals"] == [
        {"t": "8:40", "h": pytest.approx(8.667, abs=0.01)},
        {"t": "19:50", "h": pytest.approx(19.833, abs=0.01)},
    ]
    assert picture["anchor"]
    text = json.dumps(picture, ensure_ascii=False)
    for absent in ("512", "ккал", "kcal", "calories", "вес", "weight", "📊"):
        assert absent not in text


def test_a_meal_without_a_time_is_not_on_the_line():
    meal = _meal(8)
    meal.meal_time = None
    assert _card([meal]).picture["meals"] == []


def test_the_picture_names_only_what_was_ticked():
    picture = _card([_meal(8)], ticked=("Витамин D",)).picture
    assert picture["routines"] == [{"name": "Витамин D", "done": True}]
    assert _card([_meal(8)]).picture["routines"] == []


@pytest.mark.parametrize(
    "tab, title, note",
    [
        (stats_screen.TODAY, "Похоже, не всё записано?", True),
        (stats_screen.YESTERDAY, "За этот день записей нет", False),
    ],
)
def test_an_empty_day_draws_the_same_words_as_its_caption(tab, title, note):
    screen = _card([], tab=tab)
    assert screen.picture["empty"] and screen.picture["emptyTitle"] == title
    assert title in screen.text
    assert ("emptyNote" in screen.picture) == note


def test_a_week_has_no_picture():
    day = date(2026, 10, 30)
    assert stats_screen.week_screen([(day, [_meal(8)])], ZONE).picture is None


@pytest.mark.parametrize(
    "hour, look",
    [(5, "evening"), (6, "day"), (19, "day"), (20, "evening"), (23, "evening")],
)
def test_the_evening_look_follows_the_clock(hour, look):
    assert stats_screen._look(datetime(2026, 10, 30, hour)) == look


# --- the sidecar's own rules --------------------------------------------------


@pytest.fixture(scope="module")
def watcher():
    spec = importlib.util.spec_from_file_location("watcher", RENDER / "watcher.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["watcher"] = module
    spec.loader.exec_module(module)
    return module


def _sample(name):
    return json.loads((RENDER / "samples" / f"{name}.json").read_text())


def test_the_page_cannot_be_escaped_from_a_user_name(watcher):
    page = watcher.build_page(_sample("injection"))
    report = page[page.index("window.REPORT") :]
    report = report[: report.index("</script>")]
    assert "<" not in report and "&" not in report


def test_the_template_puts_text_in_as_text_only():
    template = (RENDER / "templates" / "card.html").read_text()
    uses = [line for line in template.splitlines() if "innerHTML" in line]
    assert len(uses) >= 3  # the scan sees them
    for line in uses:
        for value in re.findall(r"innerHTML = ([^;]+);", line):
            assert value == '""' or value.startswith("svg("), line


def test_the_newest_job_goes_first(watcher, tmp_path):
    old, new = tmp_path / "a.json", tmp_path / "b.json"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1000, 1000))
    assert watcher.newest_job(tmp_path, set()) == new
    assert watcher.newest_job(tmp_path, {new}) == old


def test_a_job_that_will_not_draw_leaves_an_err_and_goes(
    watcher, tmp_path, monkeypatch
):
    def refuse(report, warm=None):
        raise watcher.RenderError("template: no title")

    monkeypatch.setattr(watcher, "render", refuse)
    job = tmp_path / ("a" * 64 + ".json")
    job.write_text("{}")
    watcher.process(job, tmp_path)
    assert "no title" in (tmp_path / ("a" * 64 + ".err")).read_text()
    assert not job.exists()


@pytest.mark.parametrize("name", ["docker-compose.prod.yml", "docker-compose.yml"])
def test_the_sidecar_is_cut_off_from_everything_but_the_jobs(name):
    compose = (RENDER.parent / name).read_text()
    render = compose[
        compose.index("\n  render:\n") : compose.index("\n  telegram_bot:\n")
    ]
    bot = compose[compose.index("\n  telegram_bot:\n") :]
    for line in (
        "network_mode: none",
        "read_only: true",
        "cap_drop:\n      - ALL",
        "mem_limit: 512m",
        "volumes:\n      - render_jobs:/render\n",
    ):
        assert line in render
    assert "env_file" not in render and "ports" not in render
    assert "- render_jobs:/render" in bot and "      - render\n" in bot
    assert re.search(r"^volumes:\n(  \w+:\n)*  render_jobs:", compose, re.M)


chrome = os.environ.get("CHROME")


@pytest.mark.skipif(not chrome, reason="no CHROME: real drawing did not run")
@pytest.mark.parametrize(
    "name, code",
    [("day", 0), ("evening", 0), ("empty_ticked", 0), ("injection", 0), ("broken", 1)],
)
def test_real_chrome_draws_the_samples_and_refuses_broken(name, code, tmp_path):
    out = tmp_path / "card.png"
    done = subprocess.run(
        [
            sys.executable,
            RENDER / "watcher.py",
            "once",
            RENDER / "samples" / f"{name}.json",
            out,
        ],
        env={**os.environ, "RENDER_WARM": "0"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert (done.returncode != 0) == bool(code), done.stderr
    assert out.exists() != bool(code)
