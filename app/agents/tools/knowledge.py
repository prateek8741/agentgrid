"""Knowledge-base search tool (RAG)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolContext, tool
from app.rag import answer, service


class KnowledgeSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="What to look up, in plain words")


@tool(
    "knowledge_search",
    "Search the business knowledge base (services, prices, policies, FAQs, listings). "
    "Answer only from the results and cite them like [1].",
    KnowledgeSearchArgs,
)
async def knowledge_search(ctx: ToolContext, args: KnowledgeSearchArgs) -> dict[str, Any]:
    result = await service.search(ctx.db, ctx.tenant.id, args.query)
    if not result.confident:
        return {
            "found": False,
            "note": "Nothing relevant in the knowledge base. Say you don't know and offer a human.",
        }
    known = {c["chunk_id"] for c in ctx.citations}
    fresh = [c for c in answer.citations(result, start=len(ctx.citations) + 1) if c["chunk_id"] not in known]
    ctx.citations.extend(fresh)
    by_chunk = {c["chunk_id"]: c["n"] for c in ctx.citations}
    return {
        "found": True,
        "results": [
            {"source": by_chunk[str(chunk.chunk_id)], "title": chunk.title, "text": chunk.content[:1200]}
            for chunk in result.chunks
        ],
    }
