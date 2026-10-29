"""Management commands: `python -m app.cli <command>`.

migrate           alembic upgrade head
seed              create the platform tenant + super admin (and demo data in later phases)
create-superadmin create/update a super admin from SUPERADMIN_EMAIL / SUPERADMIN_PASSWORD
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select

from app.core.config import BASE_DIR, settings
from app.core.logging import configure_logging, get_logger

log = get_logger("agentgrid.cli")


def run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    command.upgrade(cfg, "head")


async def ensure_platform_and_superadmin() -> tuple[str, str]:
    from app.core.database import SessionLocal
    from app.core.rbac import Role
    from app.core.security import hash_password
    from app.models import Tenant, TenantKind, User
    from app.services.tenant_service import provision_tenant

    email = settings.superadmin_email.lower()
    password = settings.superadmin_password.get_secret_value()
    async with SessionLocal() as db:
        platform = (
            await db.execute(select(Tenant).where(Tenant.kind == TenantKind.PLATFORM))
        ).scalar_one_or_none()
        if platform is None:
            platform = await provision_tenant(
                db, name="AgentGrid Platform", kind=TenantKind.PLATFORM, slug="platform"
            )
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            db.add(
                User(
                    tenant_id=platform.id,
                    email=email,
                    full_name="Platform Admin",
                    role=Role.SUPER_ADMIN,
                    password_hash=await hash_password(password),
                )
            )
        await db.commit()
    return email, password


async def seed() -> None:
    email, password = await ensure_platform_and_superadmin()
    print("\nSeed complete.")
    print("=" * 60)
    print(f"  Super admin:  {email}  /  {password}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    configure_logging(settings.log_level, json_logs=False)
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    sub.add_parser("seed", help="seed the platform tenant and super admin")
    sub.add_parser("create-superadmin", help="create the super admin from env settings")
    args = parser.parse_args(argv)

    if args.command == "migrate":
        run_migrations()
        print("Migrations applied.")
    elif args.command == "seed":
        asyncio.run(seed())
    elif args.command == "create-superadmin":
        email, _ = asyncio.run(ensure_platform_and_superadmin())
        print(f"Super admin ready: {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
