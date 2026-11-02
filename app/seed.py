"""Demo data: an agency with two client businesses and ~50 conversations each.

Conversations are produced by the real orchestrator in rules-only mode (no LLM
calls: fast, free, deterministic), so leads, bookings, handovers, intents and
citations are genuine pipeline output. Afterwards timestamps are spread over
the last 30 days and analytics aggregates are rebuilt. LLM token usage for
the seeded period is illustrative (synthetic `llm_usage` rows) so the cost
KPIs have data; everything else comes from the pipeline.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, select, update

from app.agents.llm.router import LLMRouter, get_llm_router, set_llm_router
from app.agents.orchestrator import AgentOrchestrator
from app.agents.templates import get_template, render
from app.channels.schema import ChannelMetadata, ContactInfo, InboundMessage
from app.core.config import BASE_DIR
from app.core.database import SessionLocal
from app.core.rbac import Role
from app.core.security import hash_password
from app.core.tenancy import set_session_tenant, unscoped
from app.models import (
    ActionLog,
    ActionStatus,
    Agent,
    Appointment,
    AppointmentStatus,
    Channel,
    Conversation,
    ConversationStatus,
    Lead,
    LeadStatus,
    LLMUsage,
    Message,
    MessageRole,
    Tenant,
    TenantKind,
    User,
)
from app.rag import service as rag_service
from app.schemas.tenant import Branding, TenantSettings
from app.services import booking_service
from app.services.tenant_service import provision_tenant
from app.workers.analytics import rebuild_daily
from app.workers.events import get_event_bus

DEMO_PASSWORD = "Demo@12345"
SEED_DIR = BASE_DIR / "app" / "seed_data"
FIRST = [
    "Aarav",
    "Diya",
    "Rohan",
    "Ananya",
    "Vikram",
    "Meera",
    "Kabir",
    "Isha",
    "Arjun",
    "Priya",
    "Neha",
    "Rahul",
    "Sneha",
    "Karthik",
    "Pooja",
    "Aditya",
    "Riya",
    "Siddharth",
    "Tanvi",
    "Varun",
    "Nisha",
    "Manish",
    "Zoya",
]
LAST = [
    "Sharma",
    "Iyer",
    "Reddy",
    "Nair",
    "Gupta",
    "Menon",
    "Rao",
    "Kapoor",
    "Patel",
    "Das",
    "Joshi",
    "Shetty",
]


@dataclass
class DemoTenant:
    slug: str
    name: str
    industry: str
    template: str
    kb_file: str
    kb_title: str
    branding: Branding
    users: list[tuple[str, str, Role]]
    slot_hours: tuple[int, int]
    slot_minutes: int


SUNRISE = DemoTenant(
    slug="sunrise-dental",
    name="Sunrise Dental Clinic",
    industry="Healthcare",
    template="clinic_appointment_booker",
    kb_file="sunrise_dental_faq.md",
    kb_title="Patient FAQ & price list",
    branding=Branding(
        brand_name="Sunrise Dental",
        primary_color="#0D9488",
        accent_color="#F59E0B",
        widget_title="Sunrise Dental",
        widget_subtitle="Book a visit or ask us anything",
    ),
    users=[
        ("admin@sunrisedental.in", "Dr. Arjun Mehta", Role.TENANT_ADMIN),
        ("agent@sunrisedental.in", "Lakshmi (Front desk)", Role.AGENT),
        ("viewer@sunrisedental.in", "Clinic Owner", Role.VIEWER),
    ],
    slot_hours=(10, 18),
    slot_minutes=30,
)
URBANNEST = DemoTenant(
    slug="urbannest-realty",
    name="UrbanNest Realty",
    industry="Real estate",
    template="real_estate_lead_qualifier",
    kb_file="urbannest_projects.md",
    kb_title="Projects & buyer FAQ",
    branding=Branding(
        brand_name="UrbanNest",
        primary_color="#2563EB",
        accent_color="#10B981",
        widget_title="UrbanNest Homes",
        widget_subtitle="Find your next home",
    ),
    users=[
        ("admin@urbannest.in", "Rhea Kapoor", Role.TENANT_ADMIN),
        ("agent@urbannest.in", "Sameer (Sales)", Role.AGENT),
    ],
    slot_hours=(10, 17),
    slot_minutes=60,
)
AGENCY = ("brightreach-digital", "BrightReach Digital", "admin@brightreach.agency", "Neel Varma")


def _person(rng: random.Random) -> tuple[str, str, str]:
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    phone = f"9{rng.randint(100000000, 999999999)}"
    email = f"{name.split()[0].lower()}.{rng.randint(10, 99)}@example.com"
    return name, phone, email


def dental_scripts(rng: random.Random) -> list[list[str]]:
    name, phone, _ = _person(rng)
    service = rng.choice(["Consultation", "Cleaning", "Teeth whitening", "Root canal", "Braces consultation"])
    day = rng.choice(["tomorrow", "day after tomorrow", "friday", "monday"])
    faq = rng.choice(
        [
            ["What are your timings?", "thanks"],
            ["How much does teeth whitening cost?", "Is EMI available for braces?", "thank you"],
            ["Where is the clinic located? Is there parking?", "ok thanks"],
            ["Do you accept Star Health insurance?", "great, thanks"],
            ["How much is a root canal?", "bye"],
            ["What is the consultation fee?", "thanks"],
        ]
    )
    return rng.choices(
        [
            ["Hi", "I want to book an appointment", service, day, str(rng.randint(1, 3)), name, phone, "yes"],
            [
                "Book an appointment for " + service.lower() + " " + day,
                service,
                str(rng.randint(1, 4)),
                name,
                phone,
                "yes",
                "thanks",
            ],
            faq,
            ["I have severe pain and swelling in my jaw"],
            ["Can I talk to a human please?"],
            ["This is the worst service, my appointment was missed and nobody called. Useless!"],
            ["hello", "hmm", "asdf"],
        ],
        weights=[24, 14, 34, 7, 8, 5, 8],
    )[0]


def realty_scripts(rng: random.Random) -> list[list[str]]:
    name, phone, email = _person(rng)
    need = rng.choice(
        [
            "I'm looking to buy a 3BHK apartment in Whitefield",
            "Looking for a 2BHK flat to buy near Hebbal",
            "We want to invest in a villa on Sarjapur Road",
            "I want to rent a 2BHK apartment near Marathahalli",
            "Interested in buying a 1BHK in Metro Heights",
        ]
    )
    budget = rng.choice(
        ["around 80 lakh", "1.2 crore", "50-60 lakh", "about 2 crore", "flexible", "35k per month"]
    )
    timeline = rng.choice(
        ["within a month", "in 3 months", "next month", "in 6 months", "just browsing", "asap"]
    )
    contact = rng.choice([phone, email, f"{phone}, {email}"])
    return rng.choices(
        [
            [need, budget, timeline, f"I'm {name}", contact],
            [
                need,
                budget,
                timeline,
                name,
                contact,
                "yes",
                rng.choice(["tomorrow", "saturday", "sunday"]),
                "1",
                phone,
                "yes",
            ],
            [
                "Book a site visit at Lakeview Residences " + rng.choice(["tomorrow", "this saturday"]),
                str(rng.randint(1, 3)),
                name,
                phone,
                "yes",
            ],
            [
                rng.choice(
                    [
                        "What is the price per sq ft at Lakeview Residences?",
                        "Which banks do you work with for home loans?",
                        "What documents do I need to buy a flat?",
                        "When is possession for Metro Heights?",
                        "What is the booking amount for villas?",
                    ]
                ),
                "thanks",
            ],
            ["Can I speak to an agent?"],
            ["hi", "hello"],
        ],
        weights=[34, 12, 14, 24, 8, 8],
    )[0]


CHANNEL_MIX = [(Channel.WEB, 60), (Channel.WHATSAPP, 25), (Channel.API, 9), (Channel.MESSENGER, 6)]
UTM_SOURCES = ["google", "instagram", "facebook", "newsletter", None]


async def _reset(slugs: list[str]) -> None:
    """Delete demo tenants with a set-based DELETE so the database cascades (clients before the agency)."""
    async with SessionLocal() as db:
        for slug in slugs:
            await db.execute(delete(Tenant).where(Tenant.slug == slug))
            await db.commit()


async def _ensure_user(db: Any, tenant: Tenant, email: str, name: str, role: Role) -> None:
    exists = (await db.execute(select(User.id).where(User.email == email))).first()
    if exists is None:
        db.add(
            User(
                tenant_id=tenant.id,
                email=email,
                full_name=name,
                role=role,
                password_hash=await hash_password(DEMO_PASSWORD),
            )
        )


async def _setup_tenant(spec: DemoTenant, agency: Tenant) -> Tenant:
    async with SessionLocal() as db:
        tenant = await provision_tenant(
            db,
            name=spec.name,
            kind=TenantKind.BUSINESS,
            parent_id=agency.id,
            slug=spec.slug,
            industry=spec.industry,
            tenant_settings=TenantSettings(
                notification_email=spec.users[0][0], blocked_terms=["casino", "crypto trading"]
            ),
            branding=spec.branding,
        )
        await db.flush()
        for email, name, role in spec.users:
            await _ensure_user(db, tenant, email, name, role)
        template = get_template(spec.template)
        assert template is not None
        set_session_tenant(db, tenant.id)
        await db.execute(update(Agent).where(Agent.tenant_id == tenant.id).values(is_default=False))
        fields = template.agent_fields()
        fields["persona"] = render(fields["persona"], tenant.name)
        fields["greeting"] = render(fields["greeting"], tenant.name)
        db.add(
            Agent(
                tenant_id=tenant.id, name=f"{spec.branding.brand_name} Assistant", is_default=True, **fields
            )
        )
        today = datetime.now(booking_service.tenant_tz(tenant)).date()
        await booking_service.generate_slots(
            db,
            tenant,
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=21),
            weekdays=list(range(7)) if spec.template.startswith("real_estate") else [0, 1, 2, 3, 4, 5],
            day_start=time(spec.slot_hours[0]),
            day_end=time(spec.slot_hours[1]),
            slot_minutes=spec.slot_minutes,
            resource="Dr. Arjun Mehta" if spec.slug == "sunrise-dental" else "Site visit team",
        )
        await rag_service.ingest_document(
            db,
            tenant_id=tenant.id,
            filename=spec.kb_file,
            title=spec.kb_title,
            content=(SEED_DIR / spec.kb_file).read_bytes(),
        )
        await db.commit()
        await db.refresh(tenant)
        return tenant


async def _run_conversations(tenant: Tenant, spec: DemoTenant, count: int, rng: random.Random) -> list[str]:
    orchestrator = AgentOrchestrator(LLMRouter([]))  # rules-only: deterministic and free
    scripts = dental_scripts if spec.template.startswith("clinic") else realty_scripts
    sessions: list[str] = []
    for i in range(count):
        channel = rng.choices([c for c, _ in CHANNEL_MIX], weights=[w for _, w in CHANNEL_MIX])[0]
        session = (
            f"seed-{spec.slug[:6]}-{i:03d}"
            if channel is not Channel.WHATSAPP
            else f"91{rng.randint(7000000000, 9999999999)}"
        )
        name, _phone, _ = _person(rng)
        source = rng.choice(UTM_SOURCES) if channel is Channel.WEB else None
        meta = ChannelMetadata(
            utm_source=source,
            utm_medium="cpc" if source == "google" else None,
            utm_campaign=rng.choice(["diwali-offer", "brand", None]) if source else None,
        )
        contact = ContactInfo(
            external_id=session,
            name=name if channel is Channel.WHATSAPP else None,
            phone=f"+{session}" if channel is Channel.WHATSAPP else None,
        )
        for text in scripts(rng):
            async with SessionLocal() as db:
                reply = await orchestrator.handle(
                    db,
                    InboundMessage(
                        tenant_id=tenant.id,
                        channel=channel,
                        session_id=session,
                        text=text,
                        contact=contact,
                        metadata=meta,
                    ),
                )
            if reply.handed_over and reply.text is None:
                break
        sessions.append(session)
    await get_event_bus().drain()
    return sessions


async def _age_and_enrich(tenant: Tenant, rng: random.Random) -> None:
    """Spread conversations over 30 days; add human replies, pipeline stages, past appointments, usage."""
    now = datetime.now(UTC)
    async with SessionLocal() as db:
        set_session_tenant(db, tenant.id)
        conversations = list(
            (await db.execute(select(Conversation).where(Conversation.tenant_id == tenant.id))).scalars()
        )
        agent_user = (
            (await db.execute(select(User).where(User.tenant_id == tenant.id, User.role == Role.AGENT)))
            .scalars()
            .first()
        )
        for conv in conversations:
            age = timedelta(days=min(29.5, rng.expovariate(1 / 9)), hours=rng.uniform(0, 10))
            target = (now - age).replace(hour=rng.randint(8, 20), minute=rng.randint(0, 59))
            delta = target - conv.created_at
            messages = list(
                (
                    await db.execute(
                        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
                    )
                ).scalars()
            )
            for idx, msg in enumerate(messages):
                msg.created_at = msg.created_at + delta + timedelta(seconds=20 * idx)
            conv.created_at += delta
            conv.last_message_at = messages[-1].created_at if messages else conv.created_at
            if conv.handed_over_at:
                conv.handed_over_at += delta
                if agent_user and rng.random() < 0.7:  # a teammate picked it up
                    db.add(
                        Message(
                            tenant_id=tenant.id,
                            conversation_id=conv.id,
                            role=MessageRole.HUMAN_AGENT,
                            content=rng.choice(
                                [
                                    "Hi, this is the team. Sorry about that, let me help you right away.",
                                    "Thanks for waiting! I can see your details, calling you in 5 minutes.",
                                    "Hello! I've taken over from our assistant. How can I help?",
                                ]
                            ),
                            sender_user_id=agent_user.id,
                            created_at=conv.last_message_at + timedelta(minutes=3),
                        )
                    )
                    conv.assigned_user_id = agent_user.id
                    conv.message_count += 1
                    if age > timedelta(days=1):
                        conv.status = ConversationStatus.CLOSED
                        conv.closed_at = conv.last_message_at + timedelta(minutes=30)
            elif age > timedelta(days=2) and rng.random() < 0.5:
                conv.status = ConversationStatus.CLOSED
                conv.closed_at = conv.last_message_at + timedelta(hours=1)
                conv.resolved_by_ai = conv.resolved_by_ai or rng.random() < 0.8
            # Illustrative LLM metering for the seeded history (see module docstring).
            for msg in messages:
                if msg.role is MessageRole.ASSISTANT and (msg.meta or {}).get("decision") == "llm":
                    prompt, completion = rng.randint(650, 1400), rng.randint(60, 220)
                    llm_ms = rng.randint(450, 1400)
                    # Keep the illustrative history self-consistent: an LLM answer takes LLM time.
                    msg.latency_ms = llm_ms + rng.randint(60, 250)
                    db.add(
                        LLMUsage(
                            tenant_id=tenant.id,
                            conversation_id=conv.id,
                            purpose="chat",
                            provider="groq",
                            model="openai/gpt-oss-120b",
                            prompt_tokens=prompt,
                            completion_tokens=completion,
                            total_tokens=prompt + completion,
                            latency_ms=llm_ms,
                            created_at=msg.created_at,
                        )
                    )
                if msg.role is MessageRole.USER and rng.random() < 0.6:
                    db.add(
                        LLMUsage(
                            tenant_id=tenant.id,
                            conversation_id=conv.id,
                            purpose="nlu",
                            provider="groq",
                            model="openai/gpt-oss-20b",
                            prompt_tokens=rng.randint(260, 420),
                            completion_tokens=rng.randint(25, 60),
                            total_tokens=0,
                            latency_ms=rng.randint(180, 600),
                            created_at=msg.created_at,
                        )
                    )
            first_reply = next((m for m in messages if m.role is MessageRole.ASSISTANT), None)
            if first_reply is not None and first_reply.latency_ms is not None:
                conv.first_response_ms = first_reply.latency_ms
            for lead in (await db.execute(select(Lead).where(Lead.conversation_id == conv.id))).scalars():
                lead.created_at = conv.created_at + timedelta(minutes=2)
                lead.status = rng.choices(list(LeadStatus), weights=[30, 20, 18, 12, 10, 10])[0]
            for appt in (
                await db.execute(select(Appointment).where(Appointment.conversation_id == conv.id))
            ).scalars():
                appt.created_at = conv.created_at + timedelta(minutes=3)
        for usage in (
            await db.execute(
                select(LLMUsage).where(LLMUsage.tenant_id == tenant.id, LLMUsage.total_tokens == 0)
            )
        ).scalars():
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
        # A few completed / no-show appointments in the past for a realistic calendar.
        for _ in range(8):
            name, phone, _ = _person(rng)
            start = (now - timedelta(days=rng.randint(1, 20))).replace(
                hour=rng.randint(10, 17), minute=0, second=0
            )
            db.add(
                Appointment(
                    tenant_id=tenant.id,
                    reference=booking_service.new_reference("APT"),
                    customer_name=name,
                    customer_phone=phone,
                    service=rng.choice(["Consultation", "Cleaning", "Site visit"]),
                    start_at=start,
                    end_at=start + timedelta(minutes=30),
                    status=rng.choice([AppointmentStatus.COMPLETED] * 4 + [AppointmentStatus.NO_SHOW]),
                    created_at=start - timedelta(days=rng.randint(1, 5)),
                )
            )
        await db.commit()
        await rebuild_daily(db, tenant.id)
        await db.commit()


async def seed_demo(
    reset: bool = False, conversations: int = 50, seed: int = 42
) -> list[tuple[str, str, str]]:
    """Create the demo agency + tenants. Returns (workspace, email, password) rows."""
    slugs = [SUNRISE.slug, URBANNEST.slug, AGENCY[0]]
    if reset:
        await _reset(slugs)
    async with SessionLocal() as db, unscoped(db):
        existing = (await db.execute(select(Tenant.slug).where(Tenant.slug.in_(slugs)))).scalars().all()
    if set(existing) >= set(slugs):
        print("Demo tenants already exist (use --reset to recreate).")
    else:
        if existing:
            await _reset(slugs)
        rng = random.Random(seed)  # noqa: S311 - reproducible demo data, not security
        async with SessionLocal() as db:
            agency = await provision_tenant(
                db,
                name=AGENCY[1],
                kind=TenantKind.AGENCY,
                slug=AGENCY[0],
                industry="Digital marketing agency",
                branding=Branding(brand_name="BrightReach", primary_color="#7C3AED"),
            )
            await _ensure_user(db, agency, AGENCY[2], AGENCY[3], Role.TENANT_ADMIN)
            await db.commit()
            await db.refresh(agency)
        previous_router = get_llm_router()
        set_llm_router(LLMRouter([]))
        try:
            for spec in (SUNRISE, URBANNEST):
                tenant = await _setup_tenant(spec, agency)
                await _run_conversations(tenant, spec, conversations, rng)
                await _age_and_enrich(tenant, rng)
        finally:
            set_llm_router(previous_router)
        async with SessionLocal() as db:  # seeded reminders are demo noise; keep the action log clean
            await db.execute(
                update(ActionLog)
                .where(ActionLog.status == ActionStatus.PENDING, ActionLog.action_type == "notify.team")
                .values(status=ActionStatus.CANCELLED)
            )
            await db.commit()
    rows = [("BrightReach Digital (agency)", AGENCY[2], DEMO_PASSWORD)]
    rows += [(SUNRISE.name, email, DEMO_PASSWORD) for email, _, _ in SUNRISE.users]
    rows += [(URBANNEST.name, email, DEMO_PASSWORD) for email, _, _ in URBANNEST.users]
    return rows
