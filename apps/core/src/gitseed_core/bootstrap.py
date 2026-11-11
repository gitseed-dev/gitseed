import logging
import os
import secrets
from pathlib import Path

from alembic.script import ScriptDirectory
from gitseed_common.enums import (
    BackendHealth,
    BackendType,
    OrgRole,
    UserStatus,
)
from sqlalchemy import func, inspect, select, text
from sqlmodel import SQLModel

import gitseed_core.models  # noqa: F401
from gitseed_core.config import settings
from gitseed_core.db import AsyncSessionLocal, engine
from gitseed_core.models.agent import Agent
from gitseed_core.models.backend import Backend
from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.user import User
from gitseed_core.security import hash_password
from gitseed_core.services.backends import provision_sdlc_agents_for_backend
from gitseed_core.services.compute_runtime import (
    INCLUSTER_TOKEN_PATH,
    read_incluster_k8s_namespace,
)
from gitseed_core.services.policies import ensure_org_policies

logger = logging.getLogger("uvicorn.error")


def _build_default_compute_backend() -> Backend | None:
    """Build the default compute backend (Local Docker or In-Cluster Kubernetes)."""
    mode = (settings.GITSEED_BOOTSTRAP_COMPUTE_PROVIDER or "auto").strip().lower()
    if mode == "none":
        return None

    in_k8s_cluster = bool(
        os.environ.get("KUBERNETES_SERVICE_HOST") or INCLUSTER_TOKEN_PATH.is_file()
    )
    has_docker_sock = os.path.exists("/var/run/docker.sock")
    use_k8s = mode in ("kubernetes", "k8s") or (
        mode == "auto" and in_k8s_cluster and not has_docker_sock
    )

    if use_k8s:
        runner_ns = settings.RUNNER_K8S_NAMESPACE.strip() or read_incluster_k8s_namespace("default")
        isolation = settings.K8S_NAMESPACE_ISOLATION.strip().lower() or "workload"
        if isolation not in ("shared", "workload", "project"):
            isolation = "workload"
        sandbox_ns = settings.SANDBOX_K8S_NAMESPACE.strip() or (
            f"{runner_ns}-sandboxes" if isolation in ("workload", "project") else runner_ns
        )
        k8s_cfg: dict[str, str] = {
            "namespace": runner_ns,
            "sandbox_namespace": sandbox_ns,
            "namespace_isolation": isolation,
        }
        if settings.RUNNER_K8S_SERVICE_ACCOUNT.strip():
            k8s_cfg["service_account"] = settings.RUNNER_K8S_SERVICE_ACCOUNT.strip()
        return Backend(
            name="In-Cluster Kubernetes",
            type=BackendType.COMPUTE,
            provider="kubernetes",
            endpoint=(settings.K8S_API_URL or "https://kubernetes.default.svc").strip(),
            config=k8s_cfg,
            capacity=4,
            access_restricted=False,
            health=BackendHealth.HEALTHY,
            enabled=True,
        )

    return Backend(
        name="Local Docker",
        type=BackendType.COMPUTE,
        provider="docker",
        endpoint="unix:///var/run/docker.sock",
        capacity=2,
        access_restricted=False,
        health=BackendHealth.HEALTHY,
        enabled=True,
    )


def _resolve_alembic_head_revisions() -> tuple[str, ...]:
    """Dynamically resolve the current Alembic head revision ID(s) from `migrations/`."""
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    script_dir = ScriptDirectory(str(migrations_dir))
    try:
        head = script_dir.get_current_head()
        if head:
            return (head,)
    except Exception:
        pass
    return tuple(h for h in script_dir.get_heads() if h)


async def bootstrap_database() -> None:
    """Idempotently seed OrgSettings, policies, initial Admin, Docker compute, and Agents."""
    if settings.GITSEED_AUTO_MIGRATE:
        head_revisions = _resolve_alembic_head_revisions()
        async with engine.begin() as conn:
            def _create_if_unmigrated(sync_conn: object) -> None:
                if not inspect(sync_conn).has_table("alembic_version"):  # type: ignore[arg-type]
                    SQLModel.metadata.create_all(sync_conn)  # type: ignore[arg-type]
                    try:
                        sync_conn.execute(  # type: ignore[attr-defined]
                            text(
                                "CREATE TABLE IF NOT EXISTS alembic_version ("
                                "version_num VARCHAR(64) NOT NULL, "
                                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
                            )
                        )
                        for head_rev in head_revisions:
                            sync_conn.execute(  # type: ignore[attr-defined]
                                text(
                                    "INSERT INTO alembic_version (version_num) "
                                    "VALUES (:version_num)"
                                ).bindparams(version_num=head_rev)
                            )
                    except Exception:
                        pass

            await conn.run_sync(_create_if_unmigrated)

    async with AsyncSessionLocal() as session:
        # 1. Ensure singleton OrgSettings(id=1) exists
        org_settings = await session.get(OrgSettings, 1)
        if org_settings is None:
            session.add(OrgSettings(id=1))

        # 2. Ensure all 5 org-default stage policies exist
        await ensure_org_policies(session)

        # 3. Ensure initial admin user exists if the user table is empty
        user_count = await session.scalar(select(func.count()).select_from(User))
        generated_password: str | None = None

        if not user_count:
            raw_password = settings.GITSEED_ADMIN_PASSWORD
            if not raw_password:
                raw_password = secrets.token_urlsafe(16)
            generated_password = raw_password

            admin = User(
                email=settings.GITSEED_ADMIN_EMAIL.strip().lower(),
                name=settings.GITSEED_ADMIN_NAME.strip(),
                org_role=OrgRole.ADMIN,
                status=UserStatus.ACTIVE,
                password_hash=hash_password(raw_password),
            )
            session.add(admin)
        else:
            admin_email = settings.GITSEED_ADMIN_EMAIL.strip().lower()
            bootstrap_admin = (
                await session.execute(
                    select(User).where(func.lower(User.email) == admin_email)
                )
            ).scalar_one_or_none()
            if bootstrap_admin is not None and bootstrap_admin.org_role != OrgRole.ADMIN:
                bootstrap_admin.org_role = OrgRole.ADMIN

        # 4. Ensure default compute backend exists if none is registered
        compute_count = await session.scalar(
            select(func.count())
            .select_from(Backend)
            .where(Backend.type == BackendType.COMPUTE)
        )
        if not compute_count:
            default_compute = _build_default_compute_backend()
            if default_compute is not None:
                session.add(default_compute)

        # 5. If an inference backend is registered on startup, ensure default SDLC agents exist.
        # On a fresh deployment with no inference backends, stage agents are seeded during
        # onboarding as soon as the user connects their first inference provider.
        agent_count = await session.scalar(select(func.count()).select_from(Agent))
        if not agent_count:
            first_inference = (
                await session.execute(
                    select(Backend)
                    .where(
                        Backend.type == BackendType.INFERENCE,
                        Backend.enabled.is_(True),  # type: ignore[union-attr]
                    )
                    .order_by(Backend.created_at.asc())  # type: ignore[arg-type]
                    .limit(1)
                )
            ).scalar_one_or_none()
            if first_inference is not None:
                await provision_sdlc_agents_for_backend(
                    session, first_inference, force_switch=False
                )

        await session.commit()

        if generated_password is not None:
            logger.warning(
                "\n"
                "======================================================================\n"
                "  GitSeed Initial Admin Created\n"
                f"  Email:    {settings.GITSEED_ADMIN_EMAIL}\n"
                f"  Password: {generated_password}\n"
                "  (Set GITSEED_ADMIN_PASSWORD in .env to specify initial password)\n"
                "======================================================================"
            )
