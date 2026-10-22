"""KnowledgeBase against an in-memory FaqSearchStore (no DB)."""

import uuid
from types import SimpleNamespace

from core.tenant import Tenant
from models.faq import Faq
from services.knowledge import MAX_LIMIT, KnowledgeBase


def _faq(question: str, *, active: bool = True):
    return Faq(
        id=uuid.uuid4(), question=question, answer=f"A: {question}", category=None, active=active
    )


class FakeStore:
    def __init__(self, faqs, matches=()):
        self.faqs = list(faqs)
        self.matches = list(matches)  # what search_active_faqs returns
        self.searches: list[tuple[str, int]] = []

    async def count_active_faqs(self, business_id):
        return sum(f.active for f in self.faqs)

    async def list_active_faqs(self, business_id, limit):
        return [f for f in self.faqs if f.active][:limit]

    async def search_active_faqs(self, business_id, query, limit):
        self.searches.append((query, limit))
        return [f for f in self.matches if f.active][:limit]


def _tenant() -> Tenant:
    return Tenant(user=SimpleNamespace(), business=SimpleNamespace(id=uuid.uuid4()))  # type: ignore[arg-type]


async def test_few_faqs_are_all_returned_and_inactive_never_is():
    store = FakeStore([_faq("one"), _faq("two"), _faq("gone", active=False)])

    found = await KnowledgeBase(store).relevant(_tenant(), "anything at all")

    assert [f.question for f in found] == ["one", "two"]
    assert store.searches == []


async def test_many_faqs_use_search_and_no_match_is_empty_never_wholesale():
    faqs = [_faq(f"q{i}") for i in range(8)]
    kb_hit = KnowledgeBase(FakeStore(faqs, matches=faqs[3:5]))
    kb_miss = KnowledgeBase(FakeStore(faqs))

    hit = await kb_hit.relevant(_tenant(), "  walk   in? ", limit=5)
    miss = await kb_miss.relevant(_tenant(), "unrelated")

    assert [f.question for f in hit] == ["q3", "q4"]
    assert kb_hit._store.searches == [("walk in?", 5)]  # type: ignore[attr-defined]
    assert miss == []


async def test_inactive_search_hits_are_not_returned():
    faqs = [_faq(f"q{i}") for i in range(7)]
    off = _faq("off", active=False)
    found = await KnowledgeBase(FakeStore(faqs, matches=[off, faqs[0]])).relevant(_tenant(), "x")
    assert [f.question for f in found] == ["q0"]


async def test_limit_is_clamped_and_blank_query_is_empty_when_searching():
    faqs = [_faq(f"q{i}") for i in range(30)]
    store = FakeStore(faqs, matches=faqs)
    kb = KnowledgeBase(store)

    many = await kb.relevant(_tenant(), "q", limit=999)
    one = await kb.relevant(_tenant(), "q", limit=0)
    blank = await kb.relevant(_tenant(), "   ")

    assert len(many) == MAX_LIMIT and len(one) == 1
    assert [s[1] for s in store.searches] == [MAX_LIMIT, 1]
    assert blank == []
