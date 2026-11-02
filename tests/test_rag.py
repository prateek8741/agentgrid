"""Knowledge base: extraction, chunking, ingestion, retrieval, grounded answers, citations."""

import io
import os

import pytest
from httpx import AsyncClient

from app.agents.llm.scripted import ScriptedCall, nlu_json, reply, tool_call
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.rbac import Role
from app.rag import service as rag_service
from app.rag.chunking import chunk_pages
from app.rag.embeddings import HashingEmbedder, set_embedder
from app.rag.extract import ExtractionError, Page, extract_pages
from tests.conftest import auth_headers, chat, make_agent, make_tenant, make_user, use_scripted_llm

CLINIC_KB = """# Sunrise Dental Clinic - FAQ

## Timings
We are open Monday to Saturday from 9 am to 7 pm. The clinic is closed on Sundays and public holidays.

## Teeth whitening
Professional teeth whitening costs Rs 8,000 per session and takes about 60 minutes.
Results last 12 to 18 months with good oral hygiene.

## Payment options
We accept UPI, credit cards, debit cards and cash. EMI is available for treatments above Rs 20,000.

## Location
Sunrise Dental Clinic, 12 MG Road, Bengaluru 560001. Free parking is available behind the building.
"""


@pytest.fixture(autouse=True)
def _hashing_embedder(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    # The bag-of-words test embedder scores lower than bge-small: calibrate the threshold for it
    # (relevant queries score >= 0.13, unrelated ones 0.0). Production uses RAG_MIN_SCORE=0.58.
    set_embedder(HashingEmbedder())
    monkeypatch.setattr(settings, "rag_min_score", 0.1)
    monkeypatch.setattr(settings, "chunk_size_chars", 300)
    monkeypatch.setattr(settings, "chunk_overlap_chars", 60)
    yield
    set_embedder(None)


def _docx_bytes() -> bytes:
    import docx

    document = docx.Document()
    document.add_heading("Refund policy", level=1)
    document.add_paragraph("Refunds are processed within 7 working days of the request.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Plan"
    table.rows[0].cells[1].text = "Annual"
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _pdf_bytes(text: str) -> bytes:
    """Minimal single-page PDF with real text (no external tools)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


# ---------------------------------------------------------------- extraction & chunking
def test_extracts_txt_md_docx_pdf() -> None:
    assert "whitening" in extract_pages("faq.md", CLINIC_KB.encode())[0].text
    docx_text = extract_pages("policy.docx", _docx_bytes())[0].text
    assert "## Refund policy" in docx_text and "Plan | Annual" in docx_text
    pdf_pages = extract_pages("brochure.pdf", _pdf_bytes("Root canal treatment costs Rs 6500"))
    assert pdf_pages[0].page == 1 and "Root canal" in pdf_pages[0].text


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("virus.exe", b"MZ..."),
        ("fake.pdf", b"not a pdf"),
        ("fake.docx", b"plain text"),
        ("bin.txt", b"\x00\x01\x02"),
    ],
)
def test_rejects_bad_files(name: str, content: bytes) -> None:
    with pytest.raises(ExtractionError):
        extract_pages(name, content)


def test_chunking_respects_size_overlap_and_sections() -> None:
    text = "\n\n".join(f"## Section {i}\n" + f"Sentence number {i} is here. " * 30 for i in range(5))
    chunks = chunk_pages([Page(text, 3)], size=500, overlap=80)
    assert len(chunks) > 5
    assert all(len(c.text) <= 600 for c in chunks)
    assert all(c.meta["page"] == 3 for c in chunks)
    assert any(c.meta.get("section", "").startswith("Section") for c in chunks)


# ---------------------------------------------------------------- ingestion & search
async def _ingest(tenant, name="clinic-faq.md", content=CLINIC_KB.encode()):  # type: ignore[no-untyped-def]
    async with SessionLocal() as s:
        doc = await rag_service.ingest_document(s, tenant_id=tenant.id, filename=name, content=content)
        await s.commit()
        return doc


async def test_ingest_search_and_tenant_isolation() -> None:
    tenant, other = await make_tenant(), await make_tenant()
    doc = await _ingest(tenant)
    assert doc.chunk_count >= 1 and doc.title == "clinic faq" and doc.mime_type == "text/markdown"

    async with SessionLocal() as s:
        result = await rag_service.search(s, tenant.id, "how much does teeth whitening cost")
        assert result.confident and "8,000" in result.chunks[0].content
        assert result.chunks[0].title == "clinic faq"
        nothing = await rag_service.search(s, other.id, "how much does teeth whitening cost")
        assert nothing.chunks == []  # other tenant's KB is invisible
        unrelated = await rag_service.search(s, tenant.id, "quantum chromodynamics lecture schedule")
        assert not unrelated.confident


async def test_duplicate_upload_and_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.errors import AppError, ConflictError

    tenant = await make_tenant()
    await _ingest(tenant)
    with pytest.raises(ConflictError):
        await _ingest(tenant)
    monkeypatch.setattr(settings, "upload_max_mb", 0)
    with pytest.raises(AppError):
        await _ingest(tenant, "big.txt", b"x" * 100)


async def test_chunk_cap_truncates_large_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_chunks_per_document", 3)
    tenant = await make_tenant()
    big = "\n\n".join(f"Paragraph {i}. " + "Lorem ipsum dolor sit amet. " * 40 for i in range(20))
    doc = await _ingest(tenant, "big.txt", big.encode())
    assert doc.chunk_count == 3 and doc.truncated


# ---------------------------------------------------------------- grounded answers
async def test_llm_answer_is_grounded_and_cites_sources() -> None:
    tenant = await make_tenant()
    await make_agent(tenant, "customer_support_faq")
    await _ingest(tenant)
    seen: list[ScriptedCall] = []

    def responder(call: ScriptedCall):  # type: ignore[no-untyped-def]
        seen.append(call)
        if call.json_mode:
            return nlu_json("question", 0.9)
        return reply("Teeth whitening costs Rs 8,000 per session [1].")

    use_scripted_llm(responder)
    r = await chat(tenant, "How much does teeth whitening cost?", session_id="g1")
    assert r.text == "Teeth whitening costs Rs 8,000 per session [1]."
    assert r.citations and r.citations[0].title == "clinic faq" and "8,000" in r.citations[0].snippet
    assert r.debug["rag"] == "confident"
    system_prompt = seen[-1].messages[0]["content"]
    assert "Knowledge base excerpts" in system_prompt and "Rs 8,000" in system_prompt


async def test_low_retrieval_confidence_says_i_dont_know_without_llm() -> None:
    tenant = await make_tenant()
    await make_agent(tenant, "customer_support_faq")
    await _ingest(tenant)
    provider = use_scripted_llm(
        lambda call: nlu_json("question", 0.9) if call.json_mode else reply("made up")
    )
    r = await chat(tenant, "Do you sell second-hand motorcycles in Chennai?", session_id="g2")
    assert "don't have information" in r.text and r.debug["rag"] == "low_confidence"
    assert not [c for c in provider.calls if not c.json_mode]  # no generation call, nothing invented


async def test_extractive_answer_when_no_llm_configured() -> None:
    tenant = await make_tenant()
    await make_agent(tenant, "customer_support_faq")
    await _ingest(tenant)
    r = await chat(tenant, "What payment options do you accept?", session_id="g3")
    assert "UPI" in r.text and "[1]" in r.text and r.citations


async def test_knowledge_search_tool_returns_numbered_sources() -> None:
    tenant = await make_tenant()
    await make_agent(tenant, "real_estate_lead_qualifier")
    await _ingest(tenant)
    tool_payloads: list[str] = []

    def responder(call: ScriptedCall):  # type: ignore[no-untyped-def]
        if call.json_mode:
            return nlu_json("pricing", 0.9)
        tools = [m for m in call.messages if m["role"] == "tool"]
        if not tools:
            return tool_call("knowledge_search", {"query": "parking near the clinic"})
        tool_payloads.append(tools[-1]["content"])
        return reply("Free parking is available behind the building [1].")

    use_scripted_llm(responder)
    r = await chat(tenant, "Is there any parking available nearby?", session_id="g4")
    assert '"found": true' in tool_payloads[0] and '"source": ' in tool_payloads[0]
    assert r.citations


async def test_faq_cache_is_invalidated_when_kb_changes() -> None:
    tenant = await make_tenant()
    await make_agent(tenant, "customer_support_faq")
    await _ingest(tenant)
    provider = use_scripted_llm(
        lambda call: nlu_json("question", 0.9) if call.json_mode else reply("We open at 9 am [1].")
    )
    await chat(tenant, "What time do you open on Mondays?", session_id="c1")
    cached = await chat(tenant, "What time do you open on Mondays?", session_id="c2")
    assert cached.debug["cache_hit"] and cached.citations
    calls = len(provider.calls)
    await rag_service.bump_kb_version(tenant.id)
    fresh = await chat(tenant, "What time do you open on Mondays?", session_id="c3")
    assert not fresh.debug["cache_hit"] and len(provider.calls) > calls


# ---------------------------------------------------------------- API
async def test_knowledge_api_upload_list_search_delete(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    up = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("faq.md", CLINIC_KB.encode(), "text/markdown")},
        data={"title": "Clinic FAQ"},
    )
    assert up.status_code == 201, up.text
    doc = up.json()
    assert doc["title"] == "Clinic FAQ" and doc["chunk_count"] >= 1
    dup = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("faq.md", CLINIC_KB.encode(), "text/markdown")},
    )
    assert dup.status_code == 409
    bad = await client.post(
        "/api/v1/knowledge/documents",
        headers=headers,
        files={"file": ("x.exe", b"MZ", "application/octet-stream")},
    )
    assert bad.status_code == 415

    overview = (await client.get("/api/v1/knowledge/documents", headers=headers)).json()
    assert overview["total_documents"] == 1 and overview["vector_backend"] in ("pgvector", "in-process")
    detail = (await client.get(f"/api/v1/knowledge/documents/{doc['id']}", headers=headers)).json()
    assert detail["chunks"] and detail["chunks"][0]["chunk_index"] == 0

    hits = (
        await client.post("/api/v1/knowledge/search", json={"query": "whitening price"}, headers=headers)
    ).json()
    assert hits["confident"] and "whitening" in hits["results"][0]["content"].lower()

    viewer = await auth_headers(client, await make_user(tenant, Role.VIEWER))
    assert (
        await client.delete(f"/api/v1/knowledge/documents/{doc['id']}", headers=viewer)
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/knowledge/documents/{doc['id']}", headers=headers)
    ).status_code == 204
    assert (await client.get("/api/v1/knowledge/documents", headers=headers)).json()["total_chunks"] == 0

    other = await auth_headers(client, await make_user(await make_tenant(), Role.TENANT_ADMIN))
    assert (await client.get(f"/api/v1/knowledge/documents/{doc['id']}", headers=other)).status_code == 404


@pytest.mark.skipif(
    os.environ.get("RUN_SLOW") != "1", reason="downloads the real embedding model; RUN_SLOW=1"
)
async def test_real_fastembed_model_quality(monkeypatch: pytest.MonkeyPatch) -> None:
    set_embedder(None)  # use the real bge-small model with production settings
    monkeypatch.setattr(settings, "rag_min_score", 0.58)
    monkeypatch.setattr(settings, "chunk_size_chars", 900)
    tenant = await make_tenant()
    await _ingest(tenant)
    async with SessionLocal() as s:
        hit = await rag_service.search(s, tenant.id, "what does whitening cost")
        miss = await rag_service.search(s, tenant.id, "do you repair laptops")
    assert hit.confident and "8,000" in hit.chunks[0].content
    assert not miss.confident


def test_only_cited_sources_are_returned() -> None:
    from app.rag.answer import cited_only

    sources = [{"n": 1, "title": "A"}, {"n": 2, "title": "B"}, {"n": 3, "title": "C"}]
    assert [s["n"] for s in cited_only("Yes [2] and also 【3】.", sources)] == [2, 3]
    assert [s["n"] for s in cited_only("No markers at all.", sources)] == [1]


async def test_sharing_contact_details_bypasses_dont_know_gate() -> None:
    tenant = await make_tenant()
    await make_agent(tenant, "customer_support_faq")
    await _ingest(tenant)

    def responder(call: ScriptedCall):  # type: ignore[no-untyped-def]
        if call.json_mode:
            return nlu_json("question", 0.9)
        if not any(m["role"] == "tool" for m in call.messages):
            return tool_call("capture_lead", {"name": "Arjun", "phone": "<PHONE_1>", "need": "callback"})
        return reply("Thanks Arjun, our team will call you back.")

    use_scripted_llm(responder)
    r = await chat(
        tenant, "I'm Arjun, 9876501234, can someone call me back about orthodontics?", session_id="cb"
    )
    assert r.text == "Thanks Arjun, our team will call you back."
    assert r.debug["decision"] == "llm"
