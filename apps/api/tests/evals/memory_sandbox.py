"""An in-memory `SandboxFactory`: the demo salon over the in-memory stores, for runner tests.

Same contract as `evals.sandbox.postgres_sandbox`: one tenant per run, a fresh
engine per case, the run's budget applied through `eval_usage_limits`, and a
pinned business clock. `MemorySandbox.specs` records what the runner asked for.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from core.retry import RetryPolicy
from core.tenant import Tenant
from evals.cases import CalendarMode, StoredInteraction
from evals.playback import CaseEnv, Operation
from evals.runner import SandboxSpec
from evals.sandbox import EVAL_NOW, eval_usage_limits
from evals.summary_seed import facts_of
from integrations.llm.port import LLM_RETRY, LLMClient
from models.user import User
from seeds.demo_salon import (
    DEMO_SALON_FAQS,
    DEMO_SALON_HOURS,
    DEMO_SALON_SERVICES,
    new_demo_business,
)
from services.business_tools import get_business_info_tool, get_services_tool
from services.conversation import ConversationEngine
from services.directory import BusinessDirectory
from services.escalation import EscalationService
from services.faq_search import search_faq_tool
from services.knowledge import KnowledgeBase
from services.notifier import LogNotifier
from services.redaction import redact
from services.summarizer import Summarizer, Summary
from services.tools import ToolRegistry
from tests.services.memory_conversation_store import MemoryStore
from tests.services.memory_directory_store import MemoryDirectoryStore
from tests.services.memory_escalation_store import MemoryEscalationStore
from tests.services.memory_faq_store import MemoryFaqStore
from tests.services.memory_summary_store import MemorySummaryStore
from tests.services.memory_usage_store import MemoryUsageStore, guard, limits


def demo_salon() -> tuple[Tenant, MemoryFaqStore, MemoryDirectoryStore, dict[str, uuid.UUID]]:
    """The demo salon in memory. Returns the FAQ ids by question."""
    user = User(id=uuid.uuid4(), external_auth_id="eval|test", email="eval@example.test")
    business = new_demo_business(user.id)
    business.id = uuid.uuid4()
    faqs = MemoryFaqStore()
    faq_ids = {
        item.question: faqs.add(
            business.id, item.question, item.answer, item.category, active=item.active
        ).id
        for item in DEMO_SALON_FAQS
    }
    directory = MemoryDirectoryStore()
    for item in DEMO_SALON_SERVICES:
        directory.add_service(
            business.id,
            item.name,
            duration_minutes=item.duration_minutes,
            price=str(item.price) if item.price is not None else None,
            description=item.description,
            active=item.active,
        )
    directory.set_week(
        business.id,
        {
            day.day_of_week: None
            if day.opens_at is None or day.closes_at is None
            else (day.opens_at.strftime("%H:%M"), day.closes_at.strftime("%H:%M"))
            for day in DEMO_SALON_HOURS
        },
    )
    return Tenant(user=user, business=business), faqs, directory, faq_ids


@dataclass
class MemorySandbox:
    usage_store: MemoryUsageStore = field(default_factory=MemoryUsageStore)
    specs: list[SandboxSpec] = field(default_factory=list)
    # Raised by `case()` while > 0 (decremented each time), to test error handling.
    fail_cases: int = 0
    closed: bool = False

    def __post_init__(self) -> None:
        self.tenant, self.faqs, self.directory_store, self.faq_ids = demo_salon()
        self.store = MemoryStore()
        self.escalation_store = MemoryEscalationStore()

    @asynccontextmanager
    async def __call__(self, spec: SandboxSpec) -> AsyncIterator["MemorySandbox"]:
        self.specs.append(spec)
        self.usage = guard(self.usage_store, eval_usage_limits(limits(), spec))
        try:
            yield self
        finally:
            self.closed = True

    async def summarize(self, llm: LLMClient, stored: StoredInteraction) -> Summary | None:
        if self.fail_cases > 0:
            self.fail_cases -= 1
            raise RuntimeError("sandbox broke")
        business_id = self.tenant.business.id  # type: ignore[union-attr]
        store = MemorySummaryStore()
        interaction_id = store.add(business_id, facts_of(stored))
        summarizer = Summarizer(
            store=store,
            llm=llm,
            usage=self.usage,
            redact=redact,
            timeout_seconds=30 + self.specs[-1].pacing_allowance_seconds,
            retry=RetryPolicy(attempts=LLM_RETRY.attempts),
        )
        return await summarizer.summarize(business_id, interaction_id)

    @asynccontextmanager
    async def case(self, llm: LLMClient, calendar: CalendarMode = "busy") -> AsyncIterator[CaseEnv]:
        if self.fail_cases > 0:
            self.fail_cases -= 1
            raise RuntimeError("sandbox broke")
        directory = BusinessDirectory(self.directory_store, lambda: EVAL_NOW)
        engine = ConversationEngine(
            self.store,
            llm,
            ToolRegistry.of(
                [
                    search_faq_tool(KnowledgeBase(self.faqs)),
                    get_services_tool(directory),
                    get_business_info_tool(directory),
                ]
            ),
            history_limit=20,
            turn_timeout_seconds=30 + self.specs[-1].pacing_allowance_seconds,
            usage=self.usage,
            # LLM_RETRY's attempts without its real backoff sleep.
            llm_retry=RetryPolicy(attempts=LLM_RETRY.attempts),
            directory=directory,
            escalations=EscalationService(
                self.escalation_store,
                LogNotifier(),
                inbox_url=lambda interaction_id: f"https://eval.example.test/{interaction_id}",
            ),
        )

        async def operations(interaction_id: uuid.UUID) -> list[Operation]:
            return [
                Operation(row["tool_name"], row["status"], row["result"])
                for row in self.store.operations
                if row["interaction_id"] == interaction_id
            ]

        async def commit() -> None:
            self.store.calls.append("commit")

        async def escalations(interaction_id: uuid.UUID) -> list[tuple[str, str]]:
            rows = await self.escalation_store.list(
                self.tenant.business.id,  # type: ignore[union-attr]
                status=None,
                interaction_id=interaction_id,
                limit=100,
            )
            return [(r.reason, r.urgency) for r in reversed(rows)]

        yield CaseEnv(
            tenant=self.tenant,
            engine=engine,
            operations=operations,
            commit=commit,
            escalations=escalations,
        )
