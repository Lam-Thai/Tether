"""Builds the `Summarizer` over one session: the inbox detail route, the interaction
lifecycle (End, idle sweep) and the eval harness all use this one builder."""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.db import get_session
from core.llm import get_llm_client
from core.usage import get_usage_guard
from integrations.llm.port import LLMClient
from repositories.summaries import SqlSummaryStore
from services.redaction import redact
from services.summarizer import Summarizer
from services.usage import UsageGuard


def build_summarizer(
    session: AsyncSession,
    llm: LLMClient,
    usage: UsageGuard,
    *,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    timeout_seconds: float | None = None,
) -> Summarizer:
    return Summarizer(
        store=SqlSummaryStore(session),
        llm=llm,
        usage=usage,
        redact=redact,
        timeout_seconds=timeout_seconds or settings.summary_timeout_seconds,
        clock=clock,
    )


def get_summarizer(
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm_client),
    usage: UsageGuard = Depends(get_usage_guard),
) -> Summarizer:
    return build_summarizer(session, llm, usage)
