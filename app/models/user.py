from sqlalchemy import Column, String, JSON
from app.db.base_class import BaseClass
from app.db.base import Base


class User(Base, BaseClass):
    __tablename__ = "users"

    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, index=True)
    diet_preferences = Column(JSON)
    target_metrics = Column(JSON)  # For storing KBJU norms
