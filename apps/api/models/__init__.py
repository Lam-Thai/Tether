"""Import every model so `Base.metadata` is fully populated for Alembic
autogenerate and `Base.metadata.create_all` (tests) as soon as anything
imports `models` or `models.base`.
"""

from models.business import Business
from models.user import User

__all__ = ["Business", "User"]
