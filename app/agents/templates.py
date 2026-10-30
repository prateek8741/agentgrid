"""Industry agent templates.

A template defines persona, conversation flows, intents (with fast keyword
rules), qualification questions, enabled tools, handover rules and
recommended integrations. Tenants create an agent from a template in one
click (POST /api/v1/agents/from-template) and then customise every field.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

SlotKind = Literal[
    "text", "name", "phone", "email", "contact", "budget", "timeline", "date", "slot_choice", "service"
]


class TemplateIntent(BaseModel):
    name: str
    description: str
    examples: list[str] = Field(default_factory=list)
    # Regex fragments matched case-insensitively by the rules-based NLU (no LLM cost).
    keywords: list[str] = Field(default_factory=list)
    # If any of these match, the intent is ruled out (e.g. "cancel" excludes booking).
    negative_keywords: list[str] = Field(default_factory=list)
    flow: str | None = None  # structured flow this intent starts, if any


class QualificationQuestion(BaseModel):
    slot: str
    question: str
    kind: SlotKind = "text"
    required: bool = True


class AgentTemplate(BaseModel):
    key: str
    name: str
    industry: str
    description: str
    persona: str
    greeting: str
    intents: list[TemplateIntent]
    qualification_questions: list[QualificationQuestion]
    flow: dict[str, Any]
    enabled_tools: list[str]
    recommended_integrations: list[str]
    handover_rules: dict[str, Any]

    def agent_fields(self) -> dict[str, Any]:
        """Fields copied onto a new `Agent` row."""
        return {
            "template_key": self.key,
            "persona": self.persona,
            "greeting": self.greeting,
            "intents": [i.model_dump() for i in self.intents],
            "qualification_questions": [q.model_dump() for q in self.qualification_questions],
            "flow": self.flow,
            "enabled_tools": list(self.enabled_tools),
            "recommended_integrations": list(self.recommended_integrations),
            "handover_rules": dict(self.handover_rules),
        }


DEFAULT_HANDOVER_RULES: dict[str, Any] = {
    "negative_sentiment": -0.6,  # hand over when sentiment drops below this
    "low_confidence": 0.45,  # clarify once, then hand over below this NLU confidence
    "max_clarifications": 1,
    "urgent_keywords": [],
}

_BOOKING_INTENTS = [
    TemplateIntent(
        name="book_appointment",
        description="Wants to book, schedule or reserve an appointment or visit",
        examples=["I want to book an appointment", "Can I schedule a visit tomorrow?"],
        keywords=[r"\b(book|schedule|reserve)\b", r"\bappointment\b", r"\bslot\b", r"\bsite visit\b"],
        negative_keywords=[r"\bcancel", r"\breschedul"],
        flow="booking",
    ),
    TemplateIntent(
        name="cancel_appointment",
        description="Wants to cancel an existing appointment",
        examples=["Please cancel my appointment"],
        keywords=[
            r"\bcancel\b.*\b(appointment|booking|visit|slot)\b",
            r"\b(appointment|booking)\b.*\bcancel",
        ],
        flow="cancel_appointment",
    ),
]

TEMPLATES: dict[str, AgentTemplate] = {}


def _register(t: AgentTemplate) -> None:
    TEMPLATES[t.key] = t


_register(
    AgentTemplate(
        key="real_estate_lead_qualifier",
        name="Real Estate Lead Qualifier",
        industry="Real estate",
        description="Qualifies home buyers and renters (need, budget, timeline), scores leads and books site visits.",
        persona=(
            "You are a warm, knowledgeable property advisor for {business}. Help buyers and renters find the "
            "right home, answer questions about listings, locations and the buying process using the knowledge "
            "base, qualify serious buyers and book site visits. Be concise and never pressure the customer."
        ),
        greeting="Hi! I'm the {business} property assistant. Are you looking to buy, rent or invest?",
        intents=[
            TemplateIntent(
                name="property_inquiry",
                description="Looking to buy, rent or invest in a property",
                examples=["I'm looking for a 3BHK in Whitefield", "Do you have flats for rent?"],
                keywords=[
                    r"\b(buy|rent|purchase|invest(ing|ment)?)\b",
                    r"\b\d\s?bhk\b",
                    r"\b(flat|apartment|villa|plot|property|house|home|penthouse)s?\b",
                ],
                flow="lead_qualification",
            ),
            TemplateIntent(
                name="pricing",
                description="Asks about prices, payment plans or loans",
                examples=["What is the price per sq ft?"],
                keywords=[r"\b(price|cost|emi|loan)\b"],
            ),
            *_BOOKING_INTENTS,
        ],
        qualification_questions=[
            QualificationQuestion(
                slot="need", question="What are you looking for? (e.g. a 2BHK to buy in Whitefield)"
            ),
            QualificationQuestion(slot="budget", question="What budget do you have in mind?", kind="budget"),
            QualificationQuestion(
                slot="timeline", question="When are you planning to move or buy?", kind="timeline"
            ),
            QualificationQuestion(slot="name", question="May I have your name?", kind="name"),
            QualificationQuestion(
                slot="contact", question="What's the best phone number or email to reach you?", kind="contact"
            ),
        ],
        flow={
            "lead_qualification": {
                "completion": "Thanks {name}! I've shared your requirements with our advisors. A property expert "
                "will contact you shortly. Would you like to book a site visit?",
            },
            "booking": {"services": ["Site visit"], "label": "site visit"},
            "scope": "real estate: buying, renting, listings, locations, prices, home loans and site visits",
        },
        enabled_tools=[
            "knowledge_search",
            "capture_lead",
            "update_lead",
            "check_availability",
            "book_appointment",
            "cancel_appointment",
            "send_notification",
            "request_handover",
        ],
        recommended_integrations=["hubspot", "whatsapp", "webhook"],
        handover_rules={**DEFAULT_HANDOVER_RULES, "urgent_keywords": ["legal", "dispute"]},
    )
)

_register(
    AgentTemplate(
        key="clinic_appointment_booker",
        name="Clinic Appointment Booker",
        industry="Healthcare / clinics",
        description="Books, reschedules and cancels appointments; answers questions on services, timings and fees.",
        persona=(
            "You are the friendly front-desk assistant for {business}. Help patients book or cancel appointments, "
            "and answer questions about services, timings and fees using the knowledge base. Never give medical "
            "diagnoses or treatment advice: for anything clinical or urgent, recommend speaking to the doctor and "
            "offer to connect them with the clinic staff."
        ),
        greeting="Hello! Welcome to {business}. I can book an appointment or answer questions about our services.",
        intents=[
            *_BOOKING_INTENTS,
            TemplateIntent(
                name="services_info",
                description="Asks about treatments or services offered",
                examples=["Do you do teeth whitening?"],
                keywords=[r"\b(treatment|service|whitening|cleaning|root canal|braces|implant)s?\b"],
            ),
            TemplateIntent(
                name="timings",
                description="Asks about opening hours or location",
                examples=["What are your timings?"],
                keywords=[r"\b(timing|hours|open|close|address|location)s?\b"],
            ),
            TemplateIntent(
                name="emergency",
                description="Describes a medical emergency or severe pain",
                examples=["I have severe pain and swelling"],
                keywords=[r"\b(emergency|severe pain|bleeding|swelling|accident)\b"],
            ),
        ],
        qualification_questions=[
            QualificationQuestion(slot="name", question="May I have the patient's name?", kind="name"),
            QualificationQuestion(
                slot="contact", question="What's the best phone number to reach you?", kind="contact"
            ),
            QualificationQuestion(slot="need", question="What would you like to come in for?"),
        ],
        flow={
            "lead_qualification": {"completion": "Thanks {name}! Our team will get back to you shortly."},
            "booking": {
                "services": [
                    "Consultation",
                    "Cleaning",
                    "Teeth whitening",
                    "Root canal",
                    "Braces consultation",
                ],
                "label": "appointment",
            },
            "scope": "the clinic's services, appointments, timings, fees and location",
        },
        enabled_tools=[
            "knowledge_search",
            "check_availability",
            "book_appointment",
            "cancel_appointment",
            "capture_lead",
            "send_notification",
            "request_handover",
        ],
        recommended_integrations=["google_calendar", "whatsapp", "webhook"],
        handover_rules={**DEFAULT_HANDOVER_RULES, "urgent_intents": ["emergency"]},
    )
)

_register(
    AgentTemplate(
        key="ecommerce_product_recommender",
        name="E-commerce Product Recommender",
        industry="E-commerce / retail",
        description="Recommends products from the catalog, shares payment links and raises support tickets.",
        persona=(
            "You are a helpful shopping assistant for {business}. Recommend products from the catalog that match "
            "the customer's needs and budget (use the recommend_products tool, never invent products or prices), "
            "answer policy questions from the knowledge base, create payment links on request and raise a ticket "
            "for order problems."
        ),
        greeting="Hi! Looking for something special at {business}? Tell me what you need and your budget.",
        intents=[
            TemplateIntent(
                name="product_search",
                description="Looking for a product or recommendation",
                examples=["Suggest running shoes under 5000"],
                keywords=[
                    r"\b(recommend|suggest|looking for|show me|need a|want a)\b",
                    r"\bunder\s*(rs\.?|₹|inr)?\s*\d",
                ],
            ),
            TemplateIntent(
                name="order_issue",
                description="Problem with an existing order, delivery or refund",
                examples=["My order hasn't arrived"],
                keywords=[r"\b(order|delivery|refund|return|damaged|wrong item)\b"],
            ),
            TemplateIntent(
                name="payment",
                description="Wants to pay or asks for a payment link",
                examples=["Send me a payment link"],
                keywords=[r"\b(pay|payment link|checkout)\b"],
            ),
        ],
        qualification_questions=[
            QualificationQuestion(slot="name", question="May I have your name?", kind="name"),
            QualificationQuestion(
                slot="contact", question="Your email or phone, so we can follow up?", kind="contact"
            ),
        ],
        flow={
            "lead_qualification": {"completion": "Thanks {name}! We'll be in touch with offers you'll like."},
            "scope": "the store's products, orders, delivery, returns and payments",
        },
        enabled_tools=[
            "knowledge_search",
            "recommend_products",
            "create_payment_link",
            "create_ticket",
            "capture_lead",
            "request_handover",
        ],
        recommended_integrations=["razorpay", "hubspot", "webhook"],
        handover_rules=dict(DEFAULT_HANDOVER_RULES),
    )
)

_register(
    AgentTemplate(
        key="customer_support_faq",
        name="Customer Support FAQ",
        industry="Any",
        description="Answers FAQs from the knowledge base with citations, raises tickets and escalates to humans.",
        persona=(
            "You are a patient customer-support assistant for {business}. Answer questions strictly from the "
            "knowledge base and cite your sources. If the answer is not in the knowledge base, say you don't know "
            "and offer to raise a ticket or connect a human agent."
        ),
        greeting="Hi! I'm the {business} support assistant. How can I help you today?",
        intents=[
            TemplateIntent(
                name="complaint",
                description="Complains about a problem with the product or service",
                examples=["This is not working and I'm frustrated"],
                keywords=[r"\b(complain(t)?|not working|broken|issue|problem)\b"],
            ),
            TemplateIntent(
                name="billing",
                description="Asks about invoices, charges or refunds",
                examples=["I was charged twice"],
                keywords=[r"\b(invoice|bill|charged|refund)\b"],
            ),
        ],
        qualification_questions=[
            QualificationQuestion(slot="name", question="May I have your name?", kind="name"),
            QualificationQuestion(
                slot="contact", question="Your email or phone for updates?", kind="contact"
            ),
        ],
        flow={"scope": "the company's products, services, accounts and policies"},
        enabled_tools=[
            "knowledge_search",
            "create_ticket",
            "send_notification",
            "request_handover",
            "capture_lead",
        ],
        recommended_integrations=["webhook", "hubspot"],
        handover_rules=dict(DEFAULT_HANDOVER_RULES),
    )
)


def get_template(key: str) -> AgentTemplate | None:
    return TEMPLATES.get(key)


def render(text: str, business: str) -> str:
    return text.replace("{business}", business)
