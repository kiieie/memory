"""ORM 공통 베이스. 실제 모델: T2, docs/reference/db-schema.md"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
