"""Unit tests for the app_settings key/value store (TD-005 persistence)."""

from app.crud.crud_app_setting import crud_app_setting
from app.models.app_setting import AppSetting


def test_get_missing_returns_none(db_session):
    assert crud_app_setting.get(db_session, "openai_model") is None


def test_set_then_get(db_session):
    crud_app_setting.set(db_session, "openai_model", "gpt-4o")
    assert crud_app_setting.get(db_session, "openai_model") == "gpt-4o"


def test_set_upserts_single_row(db_session):
    crud_app_setting.set(db_session, "openai_model", "gpt-4o")
    crud_app_setting.set(db_session, "openai_model", "gpt-4o-mini")

    assert crud_app_setting.get(db_session, "openai_model") == "gpt-4o-mini"
    assert db_session.query(AppSetting).count() == 1
