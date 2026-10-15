"""Repository for the liveness health check.

This is the only layer allowed to touch the DB session directly.
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("tether.repositories.health")


async def check_db_connection(session: AsyncSession, timeout_seconds: float = 3.0) -> bool:
    """Run `SELECT 1` with a bounded timeout.

    Never raises: a down/slow DB is a reportable health state, not a 500.
    """
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=timeout_seconds)
        return True
    except Exception:
        logger.exception("Health check DB connection failed")
        return False
