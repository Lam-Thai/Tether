"""FAQ repository against real Postgres: full-text ranking, activity filter, scoping."""

from core.db import async_session_factory
from repositories import faqs as repo
from seeds.demo_salon import DEMO_SALON_FAQS, WALK_IN_QUESTION

KERATIN_QUESTION = "Do you offer keratin treatments?"


async def test_search_ranks_filters_and_scopes(two_tenants):
    tenant_a, tenant_b = two_tenants
    async with async_session_factory() as session:
        for item in DEMO_SALON_FAQS:
            await repo.create_faq(
                session,
                tenant_a.business_id,
                question=item.question,
                answer=item.answer,
                category=item.category,
                active=item.active,
            )
        await repo.create_faq(
            session,
            tenant_b.business_id,
            question="Do you take walk-ins at B?",
            answer="B only.",
            category=None,
            active=True,
        )
        await session.commit()

    active_total = sum(i.active for i in DEMO_SALON_FAQS)
    async with async_session_factory() as session:
        a = tenant_a.business_id
        walk = await repo.search_active_faqs(session, a, "can I just walk in?", 5)
        stopwords = await repo.search_active_faqs(session, a, "the and of a", 5)
        nothing = await repo.search_active_faqs(session, a, "zebra quantum", 5)
        keratin = await repo.search_active_faqs(session, a, "keratin treatments", 5)
        hostile = await repo.search_active_faqs(session, a, "x'); DROP TABLE faqs;--", 5)
        listed = await repo.list_active_faqs(session, a, 50)
        all_a = await repo.list_faqs(session, a)
        b_search = await repo.search_active_faqs(session, tenant_b.business_id, "walk", 5)
        counts = (
            await repo.count_active_faqs(session, a),
            await repo.count_faqs(session, a),
            await repo.count_active_faqs(session, tenant_b.business_id),
        )

    assert walk[0].question == WALK_IN_QUESTION
    assert stopwords == [] and nothing == [] and hostile == []
    assert all(f.question != KERATIN_QUESTION for f in keratin)  # inactive never matches
    assert len(listed) == active_total and all(f.active for f in listed)
    categories = [f.category for f in listed]
    assert categories == sorted(categories, key=lambda c: (c is None, c or ""))
    assert len(all_a) == len(DEMO_SALON_FAQS)
    assert [f.business_id for f in b_search] == [tenant_b.business_id]
    assert counts == (active_total, len(DEMO_SALON_FAQS), 1)
