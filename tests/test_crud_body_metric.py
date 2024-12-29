import pytest
from datetime import datetime

from app.crud.crud_body_metric import crud_body_metric
from app.crud.crud_user import crud_user
from app.schemas.body_metric import BodyMetricCreate, BodyMetricUpdate
from app.schemas.user import UserCreate


def test_create_body_metric(db_session):
    user_in = UserCreate(telegram_id="bodymetric_user_1")
    user = crud_user.create(db_session, user_in)

    metric_in = BodyMetricCreate(
        timestamp=datetime(2024, 1, 10, 9, 0, 0),
        weight=70.5
    )
    metric = crud_body_metric.create(db_session, metric_in, user_id=user.id)
    assert metric.id is not None
    assert metric.weight == 70.5
    assert metric.user_id == user.id


def test_update_body_metric(db_session):
    user_in = UserCreate(telegram_id="bodymetric_user_2")
    user = crud_user.create(db_session, user_in)

    metric_in = BodyMetricCreate(
        timestamp=datetime(2024, 1, 10, 9, 0, 0),
        weight=70.0
    )
    metric = crud_body_metric.create(db_session, metric_in, user_id=user.id)

    update_in = BodyMetricUpdate(weight=71.2)
    updated_metric = crud_body_metric.update(db_session, metric, update_in)
    assert updated_metric.weight == 71.2


def test_remove_body_metric(db_session):
    user_in = UserCreate(telegram_id="bodymetric_user_3")
    user = crud_user.create(db_session, user_in)

    metric_in = BodyMetricCreate(
        timestamp=datetime(2024, 1, 11, 10, 0, 0),
        weight=69.9
    )
    metric = crud_body_metric.create(db_session, metric_in, user_id=user.id)
    removed = crud_body_metric.remove(db_session, metric.id)
    assert removed.id == metric.id

    # Проверяем, что в БД записи нет
    assert crud_body_metric.get(db_session, metric.id) is None
