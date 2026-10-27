"""The weekly model check: sources, states, one message per news (ADR-0004).

Pages are trimmed copies taken 2026-10-27 (tests/fixtures/model_watch/). The
OpenAI side runs through the real SDK over an httpx MockTransport, so
``shutdown_date`` is read off a Model object exactly as the SDK builds it.
"""

import asyncio
import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.model_watch as mw
import app.services.model_watch_state as mws
from app.core.config import settings
from app.db.base import Base
from app.services import model_watch_sources as src

FIXTURES = Path(__file__).parent / "fixtures" / "model_watch"
TODAY = datetime.date(2026, 10, 27)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- parsers ----------------------------------------------------------------------


def test_anthropic_page_known_rows():
    rows = src.parse_anthropic(_fixture("anthropic_deprecations.md"))

    sonnet = rows["claude-sonnet-4-5-20250929"]
    assert sonnet.kind == "deprecated"
    assert sonnet.date == datetime.date(2026, 11, 30)
    assert sonnet.replacement == "claude-sonnet-5-5"
    # "Not sooner than …" is a floor, not an announced date.
    assert rows["claude-haiku-5-5"].kind == "ok"
    assert rows["claude-haiku-5-5"].date is None
    assert rows["claude-opus-4-1-20250805"].kind == "gone"
    # "To be announced": deprecated, no date yet.
    assert rows["claude-mythos-preview"].kind == "deprecated"
    assert rows["claude-mythos-preview"].date is None
    assert len(rows) >= 20


def test_google_page_known_rows():
    rows = src.parse_google(_fixture("google_deprecations.html"))

    lite = rows["gemini-3.1-flash-lite"]
    assert lite.kind == "deprecated"
    assert lite.date == datetime.date(2027, 5, 7)
    assert lite.replacement == "gemini-3.5-flash-lite"
    assert rows["gemini-3.8-flash"].kind == "ok"
    # "N/A" + an auto-routing replacement: the endpoint is not shutting down.
    assert rows["gemini-3.7-flash"].kind == "ok"
    assert len(rows) >= 10


@pytest.mark.parametrize(
    "parse, page",
    [
        (src.parse_anthropic, "# Model deprecations\n\nNo tables here any more.\n"),
        # The table survives but the column we read was renamed.
        (
            src.parse_anthropic,
            "| Model | State |\n| --- | --- |\n| claude-haiku-5-5 | Active |\n",
        ),
        (src.parse_google, "<html><body><p>Moved to a new page</p></body></html>"),
        (
            src.parse_google,
            "<table><tr><td>Model</td><td>Released</td></tr>"
            "<tr><td>gemini-3.8-flash</td><td>2026</td></tr></table>",
        ),
        (src.parse_openrouter, '{"error": "maintenance"}'),
        (src.parse_openrouter, '{"data": []}'),
    ],
)
def test_a_page_without_the_table_is_broken_not_silent(parse, page):
    """Silent-zero control: a changed page must not read as 'nothing announced'."""
    with pytest.raises(src.SourceBroken):
        parse(page)


def test_openai_replacement_matches_alias_through_its_snapshot():
    repl = src.parse_openai_replacements(_fixture("openai_deprecations.md"))

    assert repl["gpt-4o-mini-2024-07-18"] == "gpt-6-luna"
    # The page names only the snapshot; the alias finds it by date suffix.
    assert src.replacement_for(repl, "gpt-4o-mini") == "gpt-6-luna"
    assert src.replacement_for(repl, "gpt-6-luna") is None


@pytest.mark.parametrize(
    "date, state",
    [
        (TODAY + datetime.timedelta(days=200), "announced"),
        (TODAY + datetime.timedelta(days=90), "soon"),
        (TODAY + datetime.timedelta(days=1), "soon"),
        (TODAY, "past_date"),
        (None, "announced"),
    ],
)
def test_classify_by_days_left(date, state):
    signal = src.Signal("deprecated", "x", date=date)
    assert src.classify([signal], TODAY, 90).state == state


def test_classify_worst_source_wins():
    signals = [
        src.Signal("ok", "a"),
        src.Signal("deprecated", "b", date=TODAY + datetime.timedelta(days=300)),
        src.Signal("missing_from_list", "c"),
    ]
    finding = src.classify(signals, TODAY, 90)
    assert finding.state == "missing_from_list"
    assert finding.date == TODAY + datetime.timedelta(days=300)


def test_classify_all_ok_is_no_finding():
    finding = src.classify([src.Signal("ok", "a"), src.Signal("ok", "b")], TODAY, 90)
    assert finding.state == "ok"
    assert finding.date is None


# --- the check against fake services ----------------------------------------------


class _World:
    """What the internet says: OpenAI models, pages, OpenRouter's list."""

    def __init__(self):
        self.openai = {
            "gpt-4o-mini": {"shutdown_date": "2027-01-14"},
            "gpt-4o-mini-2024-07-18": {"shutdown_date": "2027-01-14"},
            "gpt-6-luna": {},
            "text-embedding-3-small": {},
        }
        self.pages = {
            src.ANTHROPIC_DEPRECATIONS_URL: _fixture("anthropic_deprecations.md"),
            src.GOOGLE_DEPRECATIONS_URL: _fixture("google_deprecations.html"),
            src.OPENAI_DEPRECATIONS_URL: _fixture("openai_deprecations.md"),
        }
        self.openrouter = {
            "openai/gpt-4o-mini": None,
            "openai/gpt-6-luna": None,
            "google/gemini-3.8-flash": None,
            "anthropic/claude-haiku-5.5": None,
        }
        self.fail = set()
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url in self.fail:
            return httpx.Response(500, text="oops")
        if url.startswith("https://api.openai.test/v1/models/"):
            mid = url.rsplit("/", 1)[1]
            if mid not in self.openai:
                return httpx.Response(
                    404,
                    json={
                        "error": {
                            "message": f"The model '{mid}' does not exist",
                            "type": "invalid_request_error",
                            "code": "model_not_found",
                        }
                    },
                )
            body = {"id": mid, "object": "model", "created": 1, "owned_by": "system"}
            return httpx.Response(200, json={**body, **self.openai[mid]})
        if url == f"{settings.OPENROUTER_BASE_URL}/models":
            data = [{"id": k, "expiration_date": v} for k, v in self.openrouter.items()]
            return httpx.Response(200, text=json.dumps({"data": data}))
        if url in self.pages:
            return httpx.Response(200, text=self.pages[url])
        return httpx.Response(404, text="unknown url " + url)

    def clients(self):
        transport = httpx.MockTransport(self.handler)
        oai = AsyncOpenAI(
            api_key="k",
            base_url="https://api.openai.test/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        )
        return oai, httpx.AsyncClient(transport=transport)


@pytest.fixture
def world():
    return _World()


def _check(world, refs):
    async def go():
        oai, http = world.clients()
        async with http:
            return await src.check_lifecycles(
                refs, openai_client=oai, http=http, today=TODAY, warn_days=90
            )

    return asyncio.run(go())


def test_gpt_4o_mini_shutdown_is_soon_with_its_replacement(world):
    found = _check(world, ["gpt-4o-mini"])["openai:gpt-4o-mini"]
    assert found.state == "soon"
    assert found.date == datetime.date(2027, 1, 14)
    assert found.replacement == "gpt-6-luna"
    # Both the alias and its snapshot were asked.
    assert any(r.endswith("/models/gpt-4o-mini-2024-07-18") for r in world.requests)


def test_snapshot_shut_down_earlier_than_alias_wins(world):
    world.openai["gpt-4o-mini"] = {"shutdown_date": "2027-01-14"}
    world.openai["gpt-4o-mini-2024-07-18"] = {"shutdown_date": "2026-12-01"}
    found = _check(world, ["gpt-4o-mini"])["openai:gpt-4o-mini"]
    assert found.date == datetime.date(2026, 12, 1)


def test_alias_without_a_date_still_hears_its_snapshot(world):
    world.openai["gpt-4o-mini"] = {"shutdown_date": None}
    world.openai["gpt-4o-mini-2024-07-18"] = {"shutdown_date": "2026-12-01"}
    found = _check(world, ["gpt-4o-mini"])["openai:gpt-4o-mini"]
    assert found.date == datetime.date(2026, 12, 1)


def test_model_gone_at_openai_is_not_found(world):
    del world.openai["gpt-4o-mini"]
    assert _check(world, ["gpt-4o-mini"])["openai:gpt-4o-mini"].state == "not_found"


def test_openrouter_ref_hears_openai_even_when_its_list_says_nothing(world):
    """OpenRouter lists openai/gpt-4o-mini with a null expiration_date."""
    found = _check(world, ["openrouter:openai/gpt-4o-mini"])
    finding = found["openrouter:openai/gpt-4o-mini"]
    assert finding.state == "soon"
    assert finding.date == datetime.date(2027, 1, 14)


def test_healthy_models_give_no_finding(world):
    found = _check(
        world,
        [
            "openai:gpt-6-luna",
            "openai:text-embedding-3-small",
            "openrouter:google/gemini-3.8-flash",
            "openrouter:anthropic/claude-haiku-5.5",
        ],
    )
    assert {ref: f.state for ref, f in found.items()} == dict.fromkeys(found, "ok")
    # Known-positive in the same run: the pages were actually read.
    assert src.GOOGLE_DEPRECATIONS_URL in world.requests
    assert src.ANTHROPIC_DEPRECATIONS_URL in world.requests


def test_one_failing_source_does_not_stop_the_others(world):
    world.fail.add(src.GOOGLE_DEPRECATIONS_URL)
    found = _check(world, ["gpt-4o-mini", "openrouter:google/gemini-3.8-flash"])
    assert found["openrouter:google/gemini-3.8-flash"].state == "call_failed"
    assert found["openai:gpt-4o-mini"].state == "soon"


def test_changed_page_is_source_broken(world):
    world.pages[src.GOOGLE_DEPRECATIONS_URL] = "<html><p>moved</p></html>"
    found = _check(world, ["openrouter:google/gemini-3.8-flash"])
    finding = found["openrouter:google/gemini-3.8-flash"]
    assert finding.state == "source_broken"
    assert src.GOOGLE_DEPRECATIONS_URL in finding.detail


def test_dropped_from_openrouter_list(world):
    del world.openrouter["google/gemini-3.8-flash"]
    found = _check(world, ["openrouter:google/gemini-3.8-flash"])
    assert found["openrouter:google/gemini-3.8-flash"].state == "missing_from_list"


def test_openai_error_is_call_failed_not_ok(world):
    world.fail.add("https://api.openai.test/v1/models/gpt-6-luna")
    assert _check(world, ["gpt-6-luna"])["openai:gpt-6-luna"].state == "call_failed"


# --- run_model_watch: what reaches the admins ---------------------------------------


@pytest.fixture
def state_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(mws, "SessionLocal", sessionmaker(bind=engine))
    return engine


class _Sent(list):
    ok = True


@pytest.fixture
def sent(monkeypatch):
    log = _Sent()

    async def fake(text):
        log.append(text)
        return log.ok

    monkeypatch.setattr(mw, "notify_admins", fake)
    return log


def _svc(world, default="gpt-4o-mini", **tasks):
    oai, _ = world.clients()
    return SimpleNamespace(
        model_for=lambda task: tasks.get(task, default),
        web_search_model=default,
        client=oai,
    )


def _run(world, svc, today=TODAY):
    async def go():
        _, http = world.clients()
        async with http:
            return await mw.run_model_watch(svc, today=today, http=http)

    return asyncio.run(go())


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "k")
    monkeypatch.setattr(settings, "MODEL_WATCH_WARN_DAYS", 90)


def test_models_in_use_groups_tasks_and_includes_embeddings(world):
    uses = mw.models_in_use(_svc(world, vision="openrouter:google/gemini-3.8-flash"))
    assert uses["openai:gpt-4o-mini"] == ["text", "barcode", "label_ocr", "web_search"]
    assert uses["openrouter:google/gemini-3.8-flash"] == ["vision"]
    assert uses[f"openai:{settings.OPENAI_EMBEDDING_MODEL}"] == ["embeddings"]


def test_first_run_tells_about_gpt_4o_mini(state_db, sent, world):
    report = _run(world, _svc(world))

    assert report.news == ["openai:gpt-4o-mini"]
    assert len(sent) == 1
    text = sent[0]
    assert "openai:gpt-4o-mini" in text
    assert "14.01.2027" in text
    assert "осталось 79 дн." in text
    assert "gpt-6-luna" in text
    assert "распознавание фото" in text
    assert "поиск КБЖУ в интернете" in text
    assert "text-embedding" not in text  # ok models stay out of the message
    assert mws.load_state("gpt-4o-mini")["state"] == "soon"


def test_two_runs_one_message(state_db, sent, world):
    _run(world, _svc(world))
    report = _run(world, _svc(world))
    assert report.news == []
    assert len(sent) == 1


def test_far_date_is_told_once_then_again_when_it_comes_close(state_db, sent, world):
    far = TODAY + datetime.timedelta(days=200)
    world.openai["gpt-4o-mini"] = {"shutdown_date": far.isoformat()}
    world.openai["gpt-4o-mini-2024-07-18"] = {}

    _run(world, _svc(world))
    _run(world, _svc(world), today=TODAY + datetime.timedelta(days=7))
    assert len(sent) == 1
    assert "Объявлена дата отключения" in sent[0]

    _run(world, _svc(world), today=far - datetime.timedelta(days=89))
    assert len(sent) == 2
    assert "осталось 89 дн." in sent[1]


def test_date_moved_earlier_tells_again_later_does_not(state_db, sent, world):
    world.openai["gpt-4o-mini-2024-07-18"] = {}
    world.openai["gpt-4o-mini"] = {"shutdown_date": "2027-06-01"}
    _run(world, _svc(world))
    world.openai["gpt-4o-mini"] = {"shutdown_date": "2027-08-01"}
    _run(world, _svc(world))
    assert len(sent) == 1
    world.openai["gpt-4o-mini"] = {"shutdown_date": "2027-04-01"}
    _run(world, _svc(world))
    assert len(sent) == 2
    assert "01.04.2027" in sent[1]


def test_undelivered_message_is_not_recorded_and_retries(state_db, sent, world):
    sent.ok = False
    report = _run(world, _svc(world))
    assert report.delivered is False
    assert mws.load_state("gpt-4o-mini") is None

    sent.ok = True
    _run(world, _svc(world))
    assert len(sent) == 2
    assert mws.load_state("gpt-4o-mini")["state"] == "soon"


def test_back_to_ok_forgets_the_state(state_db, sent, world):
    _run(world, _svc(world))
    world.openai["gpt-4o-mini"] = {}
    world.openai["gpt-4o-mini-2024-07-18"] = {}
    _run(world, _svc(world))
    assert mws.load_state("gpt-4o-mini") is None


def test_a_model_no_longer_used_is_forgotten(state_db, sent, world):
    _run(world, _svc(world))
    _run(world, _svc(world, default="gpt-6-luna"))
    assert mws.load_state("gpt-4o-mini") is None
    assert len(sent) == 1


def test_healthy_setup_sends_nothing(state_db, sent, world):
    report = _run(world, _svc(world, default="gpt-6-luna"))
    assert sent == []
    states = {ref: f.state for ref, f in report.findings.items()}
    assert states == {
        "openai:gpt-6-luna": "ok",
        f"openai:{settings.OPENAI_EMBEDDING_MODEL}": "ok",
    }


def test_no_admins_does_not_crash(state_db, world, monkeypatch):
    """The real notify_admins, with nobody to tell."""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "")
    report = _run(world, _svc(world))
    assert report.news == ["openai:gpt-4o-mini"]
    assert report.delivered is False
    assert mws.load_state("gpt-4o-mini") is None


# --- call sites: the task, its switch, beat -------------------------------------------


def test_task_runs_the_watch_and_respects_the_switch(monkeypatch):
    import celery_app.tasks.model_watch as task_mod

    calls = []

    async def fake_run(svc):
        calls.append(svc)
        return mw.WatchReport(uses={}, findings={})

    monkeypatch.setattr(task_mod, "run_model_watch", fake_run)
    monkeypatch.setattr(task_mod, "OpenAIService", lambda: "svc")

    monkeypatch.setattr(settings, "MODEL_WATCH_ENABLED", False)
    assert task_mod.check_models() == {"skipped": True}
    assert calls == []

    monkeypatch.setattr(settings, "MODEL_WATCH_ENABLED", True)
    assert task_mod.check_models()["news"] == []
    assert calls == ["svc"]


def test_task_is_included_and_scheduled_weekly():
    from celery_app.celery_app import app
    from celery_app.tasks.model_watch import check_models

    assert "celery_app.tasks.model_watch" in app.conf.include
    entry = app.conf.beat_schedule["model-watch"]
    assert entry["task"] == check_models.name
    assert check_models.name in app.tasks
    schedule = entry["schedule"]
    assert schedule.day_of_week == {1}
    assert schedule.hour == {6}
    assert schedule.minute == {0}


# --- /models (admin) ----------------------------------------------------------------


class _Msg:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def _models_update(user_id):
    msg = _Msg()
    update = SimpleNamespace(
        message=msg,
        effective_message=msg,
        effective_user=SimpleNamespace(id=user_id, username="u"),
    )
    return update, msg


def test_models_command_runs_the_check_and_shows_every_model(
    state_db, sent, world, monkeypatch
):
    import app.services.telegram as tg

    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    svc = _svc(world, vision="gpt-6-luna")
    monkeypatch.setattr(tg, "get_openai_service", lambda: svc)
    _, http = world.clients()
    real_run = tg.run_model_watch
    monkeypatch.setattr(
        tg, "run_model_watch", lambda s: real_run(s, today=TODAY, http=http)
    )
    now = SimpleNamespace(date=lambda: TODAY)
    monkeypatch.setattr(tg, "datetime", SimpleNamespace(now=lambda tz: now))

    update, msg = _models_update(42)
    asyncio.run(tg.models_command(update, SimpleNamespace()))

    status = msg.replies[-1]
    assert "⚠️ openai:gpt-4o-mini" in status
    assert "осталось 79 дн." in status
    assert "✅ openai:gpt-6-luna" in status
    assert "Задачи: распознавание фото" in status
    assert "Новости отправлены отдельным сообщением." in status
    # The alert went out through the usual path and was recorded.
    assert len(sent) == 1
    assert mws.load_state("gpt-4o-mini")["state"] == "soon"


def test_models_command_is_admin_only(monkeypatch):
    import app.services.telegram as tg

    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    ran = []
    monkeypatch.setattr(tg, "run_model_watch", lambda s: ran.append(s))
    update, msg = _models_update(7)
    asyncio.run(tg.models_command(update, SimpleNamespace()))
    assert ran == []
    assert not any("Модели бота" in r for r in msg.replies)


def test_models_command_is_registered(monkeypatch):
    import app.services.telegram as tg

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    application = tg.create_bot_application()
    handlers = [h for group in application.handlers.values() for h in group]
    by_command = {
        cmd: h.callback for h in handlers for cmd in getattr(h, "commands", ())
    }
    assert by_command["models"] is tg.models_command
