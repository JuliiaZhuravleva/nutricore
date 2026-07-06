from datetime import UTC, datetime

from app.crud.crud_body_metric import crud_body_metric
from app.crud.crud_user import crud_user
from app.schemas.body_metric import BodyMetricCreate, BodyMetricUpdate
from app.schemas.user import UserCreate


def test_create_body_metric(db_session):
    """Test body metric creation."""
    user_in = UserCreate(telegram_id=444444444)
    user = crud_user.create(db_session, obj_in=user_in)

    metric_in = BodyMetricCreate(
        weight=75.5,
        timestamp=datetime.now(UTC),
        metrics={"body_fat": 15.0, "muscle_mass": 35.0},
    )

    metric = crud_body_metric.create(db_session, obj_in=metric_in, user_id=user.id)
    assert metric.weight == 75.5
    assert metric.metrics == {"body_fat": 15.0, "muscle_mass": 35.0}
    assert metric.user_id == user.id


def test_get_user_metrics(db_session):
    """Test getting user metrics."""
    user_in = UserCreate(telegram_id=555555555)
    user = crud_user.create(db_session, obj_in=user_in)

    metric1 = BodyMetricCreate(weight=76.0, timestamp=datetime.now(UTC))
    metric2 = BodyMetricCreate(weight=75.8, timestamp=datetime.now(UTC))

    crud_body_metric.create(db_session, obj_in=metric1, user_id=user.id)
    crud_body_metric.create(db_session, obj_in=metric2, user_id=user.id)

    metrics = crud_body_metric.get_user_metrics(db_session, user_id=user.id)
    assert len(metrics) == 2


def test_update_body_metric(db_session):
    user_in = UserCreate(telegram_id=777777777)
    user = crud_user.create(db_session, obj_in=user_in)

    metric_in = BodyMetricCreate(weight=70.0, timestamp=datetime.now(UTC))
    metric = crud_body_metric.create(db_session, obj_in=metric_in, user_id=user.id)

    update_in = BodyMetricUpdate(weight=71.2)
    updated_metric = crud_body_metric.update(db_session, metric, update_in)
    assert updated_metric.weight == 71.2


def test_remove_body_metric(db_session):
    user_in = UserCreate(telegram_id=888888888)
    user = crud_user.create(db_session, obj_in=user_in)

    metric_in = BodyMetricCreate(weight=69.9, timestamp=datetime.now(UTC))
    metric = crud_body_metric.create(db_session, obj_in=metric_in, user_id=user.id)
    removed = crud_body_metric.remove(db_session, metric.id)
    assert removed.id == metric.id

    assert crud_body_metric.get(db_session, metric.id) is None
