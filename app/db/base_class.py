from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import declared_attr


class BaseClass:
    # Generate __tablename__ automatically
    @declared_attr
    def __tablename__(cls) -> str:
        return cls.__name__.lower()

    # Add common columns here if needed


Base = declarative_base(cls=BaseClass)