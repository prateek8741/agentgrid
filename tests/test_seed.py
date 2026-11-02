"""The demo seed runs end-to-end (small) and produces a realistic, analytics-ready dataset."""

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import AnalyticsDaily, Appointment, Conversation, Lead, Tenant, TenantKind, User
from app.rag.embeddings import HashingEmbedder, set_embedder
from app.seed import seed_demo


async def test_seed_demo_is_complete_and_idempotent() -> None:
    set_embedder(HashingEmbedder())
    try:
        rows = await seed_demo(reset=True, conversations=12, seed=7)
        again = await seed_demo(reset=False, conversations=12, seed=7)  # no duplicates
    finally:
        set_embedder(None)
    assert rows == again and len(rows) == 6
    async with SessionLocal() as s:
        tenants = {
            t.slug: t
            for t in (
                await s.execute(
                    select(Tenant).where(
                        Tenant.slug.in_(["sunrise-dental", "urbannest-realty", "brightreach-digital"])
                    )
                )
            ).scalars()
        }
        assert tenants["brightreach-digital"].kind is TenantKind.AGENCY
        assert tenants["sunrise-dental"].parent_id == tenants["brightreach-digital"].id
        for slug in ("sunrise-dental", "urbannest-realty"):
            tid = tenants[slug].id
            convs = (
                await s.execute(select(func.count(Conversation.id)).where(Conversation.tenant_id == tid))
            ).scalar_one()
            aggregates = (
                await s.execute(select(func.count(AnalyticsDaily.id)).where(AnalyticsDaily.tenant_id == tid))
            ).scalar_one()
            users = (await s.execute(select(func.count(User.id)).where(User.tenant_id == tid))).scalar_one()
            assert convs == 12 and aggregates > 0 and users >= 2
        realty = tenants["urbannest-realty"].id
        assert (await s.execute(select(func.count(Lead.id)).where(Lead.tenant_id == realty))).scalar_one() > 0
        dental = tenants["sunrise-dental"].id
        assert (
            await s.execute(select(func.count(Appointment.id)).where(Appointment.tenant_id == dental))
        ).scalar_one() > 0
