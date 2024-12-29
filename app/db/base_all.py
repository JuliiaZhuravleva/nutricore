from app.db.base import Base
from app.models.user import User
from app.models.meal import Meal
from app.models.body_metric import BodyMetric
from app.models.activity import Activity
from app.models.analysis_report import AnalysisReport

# All models are imported here for Alembic autogenerate support

__all__ = ["Base", "User", "Meal", "BodyMetric", "Activity", "AnalysisReport"]