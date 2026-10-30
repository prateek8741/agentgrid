"""Per-tenant product catalog: CSV import and recommendation search."""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product

MAX_IMPORT_ROWS = 5000
REQUIRED_COLUMNS = {"sku", "name", "price"}
_STOPWORDS = {
    "a",
    "an",
    "the",
    "for",
    "with",
    "and",
    "or",
    "of",
    "to",
    "in",
    "me",
    "i",
    "need",
    "want",
    "looking",
    "show",
    "some",
    "any",
    "under",
    "below",
    "around",
    "suggest",
    "recommend",
    "please",
}


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOPWORDS and len(t) > 1}


async def search(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    query: str,
    max_price: Decimal | None = None,
    category: str | None = None,
    limit: int = 5,
) -> list[Product]:
    """Keyword relevance over name/category/tags/description, filtered by price and stock.

    Catalogs in this PoC are small (<5k rows), so scoring in Python after a
    filtered SELECT is cheap and works identically on Postgres and SQLite.
    """
    stmt = select(Product).where(Product.tenant_id == tenant_id, Product.in_stock.is_(True))
    if max_price is not None:
        stmt = stmt.where(Product.price <= max_price)
    if category:
        stmt = stmt.where(Product.category.ilike(f"%{category}%"))
    products = list((await db.execute(stmt.limit(MAX_IMPORT_ROWS))).scalars().all())
    wanted = _tokens(query)
    scored: list[tuple[float, Product]] = []
    for p in products:
        name, cat, tags = _tokens(p.name), _tokens(p.category or ""), _tokens(" ".join(p.tags or []))
        desc = _tokens(p.description or "")
        score = 3 * len(wanted & name) + 2 * len(wanted & (cat | tags)) + len(wanted & desc)
        if score or not wanted:
            scored.append((score, p))
    scored.sort(key=lambda sp: (-sp[0], sp[1].price))
    return [p for _, p in scored[:limit]]


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)


async def import_csv(db: AsyncSession, tenant_id: uuid.UUID, content: bytes) -> ImportResult:
    """Upsert products by SKU.

    Columns: sku, name, price [, description, category, currency, tags, url, image_url, in_stock].
    """
    result = ImportResult()
    try:
        text = content.decode("utf-8-sig")  # tolerate Excel's BOM
    except UnicodeDecodeError:
        result.errors.append("File must be UTF-8 encoded CSV")
        return result
    reader = csv.DictReader(io.StringIO(text))
    columns = {c.strip().lower() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        result.errors.append(f"Missing required columns: {', '.join(sorted(missing))}")
        return result

    existing = {
        p.sku: p
        for p in (await db.execute(select(Product).where(Product.tenant_id == tenant_id))).scalars().all()
    }
    for line_no, raw in enumerate(reader, start=2):
        if line_no - 1 > MAX_IMPORT_ROWS:
            result.errors.append(f"Stopped after {MAX_IMPORT_ROWS} rows")
            break
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        sku, name = row.get("sku", "")[:80], row.get("name", "")[:300]
        if not sku or not name:
            result.errors.append(f"Line {line_no}: sku and name are required")
            continue
        try:
            price = Decimal(re.sub(r"[^\d.]", "", row.get("price", "")) or "0")
        except InvalidOperation:
            result.errors.append(f"Line {line_no}: invalid price")
            continue
        values = {
            "name": name,
            "price": price,
            "description": row.get("description", "")[:5000],
            "category": row.get("category") or None,
            "currency": (row.get("currency") or "INR")[:3].upper(),
            "tags": [t.strip() for t in re.split(r"[|;,]", row.get("tags", "")) if t.strip()][:30],
            "url": row.get("url") or None,
            "image_url": row.get("image_url") or None,
            "in_stock": row.get("in_stock", "true").lower() not in {"false", "0", "no", "n"},
        }
        product = existing.get(sku)
        if product is None:
            product = Product(tenant_id=tenant_id, sku=sku, **values)
            db.add(product)
            existing[sku] = product
            result.created += 1
        else:
            for key, value in values.items():
                setattr(product, key, value)
            result.updated += 1
    return result
