from app.db.base import Base
from app.models.activity import Activity
from app.models.ai_call_log import AiCallLog
from app.models.analysis_report import AnalysisReport
from app.models.app_setting import AppSetting
from app.models.body_metric import BodyMetric
from app.models.meal import Meal
from app.models.subscription import Subscription
from app.models.user import User

# All models are imported here for Alembic autogenerate support

__all__ = [
    "Base",
    "User",
    "Meal",
    "BodyMetric",
    "Activity",
    "AnalysisReport",
    "Subscription",
    "AiCallLog",
    "AppSetting",
]
