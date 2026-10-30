from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Query, Request, Response, UploadFile, status
from sqlalchemy import func, select

from app.api.deps import DB, AdminAuth, AgentAuth, ViewerAuth
from app.core.errors import AppError, NotFoundError
from app.models import Product, Ticket, TicketStatus
from app.schemas.common import Page
from app.schemas.crm import ProductImportResult, ProductOut, TicketOut, TicketUpdate
from app.services import audit_service, product_service

router = APIRouter(tags=["commerce"])
MAX_CSV_BYTES = 2 * 1024 * 1024


@router.get("/products", response_model=Page[ProductOut])
async def list_products(
    auth: ViewerAuth,
    db: DB,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[ProductOut]:
    stmt = select(Product).where(Product.tenant_id == auth.tenant_id)
    if q:
        stmt = stmt.where(Product.name.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Product.name).limit(limit).offset(offset))).scalars().all()
    return Page(
        items=[ProductOut.model_validate(r) for r in rows], total=int(total), limit=limit, offset=offset
    )


@router.post(
    "/products/import", response_model=ProductImportResult, summary="Upsert the product catalog from CSV"
)
async def import_products(
    request: Request, auth: AdminAuth, db: DB, file: UploadFile = File(description="CSV: sku,name,price,...")
) -> ProductImportResult:
    content = await file.read(MAX_CSV_BYTES + 1)
    if len(content) > MAX_CSV_BYTES:
        raise AppError("CSV is larger than 2 MB", status_code=413)
    result = await product_service.import_csv(db, auth.tenant_id, content)
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="products.import",
        resource_type="product",
        actor=auth.user,
        request=request,
        details={"created": result.created, "updated": result.updated, "errors": len(result.errors)},
    )
    await db.commit()
    return ProductImportResult(created=result.created, updated=result.updated, errors=result.errors[:50])


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(product_id: uuid.UUID, request: Request, auth: AdminAuth, db: DB) -> Response:
    product = (
        await db.execute(select(Product).where(Product.id == product_id, Product.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if product is None:
        raise NotFoundError("Product not found")
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="product.delete",
        resource_type="product",
        resource_id=product.id,
        actor=auth.user,
        request=request,
        details={"sku": product.sku},
    )
    await db.delete(product)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/tickets", response_model=Page[TicketOut])
async def list_tickets(
    auth: ViewerAuth,
    db: DB,
    status_filter: TicketStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[TicketOut]:
    stmt = select(Ticket).where(Ticket.tenant_id == auth.tenant_id)
    if status_filter:
        stmt = stmt.where(Ticket.status == status_filter)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(Ticket.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return Page(
        items=[TicketOut.model_validate(r) for r in rows], total=int(total), limit=limit, offset=offset
    )


@router.patch("/tickets/{ticket_id}", response_model=TicketOut)
async def update_ticket(
    ticket_id: uuid.UUID, body: TicketUpdate, request: Request, auth: AgentAuth, db: DB
) -> TicketOut:
    ticket = (
        await db.execute(select(Ticket).where(Ticket.id == ticket_id, Ticket.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if ticket is None:
        raise NotFoundError("Ticket not found")
    changes = body.model_dump(exclude_none=True)
    for key, value in changes.items():
        setattr(ticket, key, value)
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="ticket.update",
        resource_type="ticket",
        resource_id=ticket.id,
        actor=auth.user,
        request=request,
        details={"fields": sorted(changes)},
    )
    await db.commit()
    await db.refresh(ticket)
    return TicketOut.model_validate(ticket)
