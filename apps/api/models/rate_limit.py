"""`rate_limit_windows`: one row per rate-limit key and fixed window (slice 17).

`key` is `<rule>:<subject>` (a business id, or a client IP for webhooks), `window_start`
the aligned start of the window. `repositories/rate_limit.py` bumps `count` with a
conditional upsert, so concurrent requests at the limit can't overshoot it. Rows are
disposable: expired windows are purged opportunistically, and there is no tenant FK on
purpose (an IP subject is not a tenant, and a deleted business's rows expire on their own).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base


class RateLimitWindow(Base):
    __tablename__ = "rate_limit_windows"
    __table_args__ = (
        CheckConstraint("count >= 0", name="ck_rate_limit_windows_count"),
        Index("ix_rate_limit_windows_window_start", "window_start"),
    )

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
