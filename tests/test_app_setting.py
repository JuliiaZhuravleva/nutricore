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


def test_get_prefix_returns_only_matching_keys(db_session):
    crud_app_setting.set(db_session, "model_watch:openai:gpt-4o-mini", "{}")
    crud_app_setting.set(db_session, "model_watch:openrouter:qwen/x", "{}")
    crud_app_setting.set(db_session, "model:vision", "gpt-6-luna")
    # "_" is a LIKE wildcard: an unescaped prefix would match "modelXwatch".
    crud_app_setting.set(db_session, "modelXwatch:other", "{}")

    got = crud_app_setting.get_prefix(db_session, "model_watch:")

    assert set(got) == {"model_watch:openai:gpt-4o-mini", "model_watch:openrouter:qwen/x"}


def test_get_prefix_with_no_match_is_empty(db_session):
    crud_app_setting.set(db_session, "openai_model", "gpt-4o")
    assert crud_app_setting.get_prefix(db_session, "model_watch:") == {}


def test_delete_removes_the_row(db_session):
    crud_app_setting.set(db_session, "model:text", "gpt-6-luna")
    assert crud_app_setting.delete(db_session, "model:text") is True
    assert crud_app_setting.get(db_session, "model:text") is None
    assert crud_app_setting.delete(db_session, "model:text") is False
