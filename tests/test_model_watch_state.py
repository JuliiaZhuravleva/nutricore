"""Admin alerts for broken models: delivery and once-per-breakage (ADR-0004)."""

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.admin_notify as admin_notify
import app.services.model_watch_state as mws
from app.core.config import settings
from app.db.base import Base



class _ListLog(list):
    """A list that can carry the fake as an attribute."""


@pytest.fixture
def state_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(mws, "SessionLocal", sessionmaker(bind=engine))
    return engine


@pytest.fixture
def sent(monkeypatch):
    """Fake notify_admins at its call site; flip .ok to model a failed delivery."""
    log = _ListLog()

    async def fake(text):
        # A real send awaits the network: yield, so concurrent callers interleave.
        await asyncio.sleep(0)
        log.append(text)
        return fake.ok

    fake.ok = True
    monkeypatch.setattr(mws, "notify_admins", fake)
    log.fake = fake
    return log


# --- report_model_failure ------------------------------------------------------


def test_first_failure_alerts_and_records(state_db, sent):
    err = RuntimeError("The model `gpt-x` does not exist")

    assert asyncio.run(mws.report_model_failure("vision", "gpt-x", err)) is True

    assert len(sent) == 1
    assert "openai:gpt-x" in sent[0]
    assert "распознавание фото" in sent[0]
    state = mws.load_state("openai:gpt-x")
    assert state["state"] == "not_found"
    assert state["notified_at"]


def test_same_breakage_alerts_once(state_db, sent):
    err = RuntimeError("gone")
    asyncio.run(mws.report_model_failure("vision", "gpt-x", err))
    # Bare id and full ref are the same model.
    assert asyncio.run(mws.report_model_failure("text", "openai:gpt-x", err)) is False
    assert len(sent) == 1


def test_undelivered_alert_is_not_recorded_and_retries(state_db, sent):
    sent.fake.ok = False
    assert asyncio.run(mws.report_model_failure("vision", "gpt-x", RuntimeError())) is False
    assert mws.load_state("gpt-x") is None

    sent.fake.ok = True
    assert asyncio.run(mws.report_model_failure("vision", "gpt-x", RuntimeError())) is True
    assert len(sent) == 2


def test_concurrent_failures_on_one_photo_alert_once(state_db, sent):
    """Vision and barcode fail on the same dead model at the same moment."""

    async def both():
        return await asyncio.gather(
            mws.report_model_failure("vision", "gpt-x", RuntimeError()),
            mws.report_model_failure("barcode", "gpt-x", RuntimeError()),
        )

    assert sorted(asyncio.run(both())) == [False, True]
    assert len(sent) == 1


def test_a_different_model_still_alerts(state_db, sent):
    asyncio.run(mws.report_model_failure("vision", "gpt-x", RuntimeError()))
    asyncio.run(mws.report_model_failure("vision", "openrouter:x/y", RuntimeError()))
    assert len(sent) == 2


def test_cleared_state_alerts_again(state_db, sent):
    asyncio.run(mws.report_model_failure("vision", "gpt-x", RuntimeError()))
    mws.clear_state("gpt-x")
    asyncio.run(mws.report_model_failure("vision", "gpt-x", RuntimeError()))
    assert len(sent) == 2


def test_db_down_never_raises_into_the_meal_flow(monkeypatch, sent):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mws, "SessionLocal", boom)
    # Alert still goes out (state unknown → treat as new); nothing raises.
    assert asyncio.run(mws.report_model_failure("vision", "gpt-x", RuntimeError())) is True


def test_unreadable_state_row_is_skipped(state_db):
    from app.crud.crud_app_setting import crud_app_setting

    Session = mws.SessionLocal
    with Session() as db:
        crud_app_setting.set(db, "model_watch:openai:a", "not json")
        crud_app_setting.set(db, "model_watch:openai:b", json.dumps({"state": "soon"}))
    assert mws.load_states() == {"openai:b": {"state": "soon"}}


# --- notify_admins -------------------------------------------------------------


class _FakeBot:
    instances = []

    def __init__(self, token, refuse=()):
        self.token = token
        self.refuse = set(refuse)
        self.sent = []
        _FakeBot.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_message(self, chat_id, text):
        if chat_id in self.refuse:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text))


def _fake_bot(monkeypatch, refuse=()):
    _FakeBot.instances = []
    monkeypatch.setattr(admin_notify, "Bot", lambda token: _FakeBot(token, refuse))


def test_notify_sends_to_every_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "11,22")
    _fake_bot(monkeypatch)
    assert asyncio.run(admin_notify.notify_admins("hi")) is True
    assert _FakeBot.instances[0].sent == [(11, "hi"), (22, "hi")]


def test_notify_one_refusal_still_delivers_to_the_rest(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "11,22")
    _fake_bot(monkeypatch, refuse={11})
    assert asyncio.run(admin_notify.notify_admins("hi")) is True
    assert _FakeBot.instances[0].sent == [(22, "hi")]


def test_notify_all_refused_is_false(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "11")
    _fake_bot(monkeypatch, refuse={11})
    assert asyncio.run(admin_notify.notify_admins("hi")) is False


def test_notify_without_admins_is_false_and_builds_no_bot(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "")
    _fake_bot(monkeypatch)
    assert asyncio.run(admin_notify.notify_admins("hi")) is False
    assert _FakeBot.instances == []


def test_notify_truncates_to_telegram_limit(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "11")
    _fake_bot(monkeypatch)
    asyncio.run(admin_notify.notify_admins("x" * 5000))
    assert len(_FakeBot.instances[0].sent[0][1]) == 4096


def test_importing_notify_clamps_the_token_leaking_loggers():
    import logging

    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING
