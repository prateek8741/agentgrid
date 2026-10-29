"""Roles and the permission model.

Roles are hierarchical: a higher role can do everything a lower one can.

* super_admin   - platform operator; manages every tenant
* tenant_admin  - owns a tenant (or an agency and its client tenants)
* agent         - human support agent: inbox, leads, appointments
* viewer        - read-only access to the tenant's data and analytics
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    TENANT_ADMIN = "tenant_admin"
    AGENT = "agent"
    VIEWER = "viewer"


ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 10,
    Role.AGENT: 20,
    Role.TENANT_ADMIN: 30,
    Role.SUPER_ADMIN: 100,
}


def role_at_least(role: Role | str, minimum: Role) -> bool:
    return ROLE_RANK[Role(role)] >= ROLE_RANK[minimum]


def assignable_roles(actor: Role | str) -> set[Role]:
    """Roles an actor may grant to other users (never above their own)."""
    actor = Role(actor)
    if actor is Role.SUPER_ADMIN:
        return set(Role)
    if actor is Role.TENANT_ADMIN:
        return {Role.TENANT_ADMIN, Role.AGENT, Role.VIEWER}
    return set()
