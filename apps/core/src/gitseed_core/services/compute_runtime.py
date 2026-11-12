"""Docker & Kubernetes Compute Container/Job Runtime Helpers."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import ssl
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx

from gitseed_core.config import settings
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.security import (
    create_run_token,
    decrypt_secrets,
    verify_scoped_run_token,
)

logger = logging.getLogger("uvicorn.error")

INCLUSTER_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
INCLUSTER_NAMESPACE_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
INCLUSTER_CA_CERT_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


def _resolve_k8s_ssl_context(
    backend: Backend, secrets_map: dict[str, str]
) -> ssl.SSLContext | bool:
    """Resolve an SSLContext or boolean TLS verification flag for Kubernetes API requests.

    Defaults to `True` (system CA bundle verification) unless `insecure_skip_tls_verify` is
    explicitly enabled (`True`).
    """
    cfg = backend.config or {}
    raw_skip = cfg.get("insecure_skip_tls_verify")
    if raw_skip is True or (
        isinstance(raw_skip, str) and raw_skip.strip().lower() in ("1", "true", "yes")
    ):
        return False
    ca_pem = (secrets_map.get("ca_cert") or "").strip()
    if ca_pem:
        try:
            return ssl.create_default_context(cadata=ca_pem)
        except Exception:
            pass
    try:
        if INCLUSTER_CA_CERT_PATH.is_file():
            return ssl.create_default_context(cafile=str(INCLUSTER_CA_CERT_PATH))
    except Exception:
        pass
    return True


def is_k8s_backend(backend: Backend | None) -> bool:
    """Return True when `backend` is a Kubernetes compute backend."""
    return bool(backend and backend.provider.lower() in ("kubernetes", "k8s"))


def read_incluster_k8s_token() -> str:
    """Read the mounted Kubernetes ServiceAccount bearer token when running in-cluster."""
    try:
        if INCLUSTER_TOKEN_PATH.is_file():
            return INCLUSTER_TOKEN_PATH.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def read_incluster_k8s_namespace(default: str = "default") -> str:
    """Read the mounted Kubernetes Pod namespace when running in-cluster."""
    try:
        if INCLUSTER_NAMESPACE_PATH.is_file():
            ns = INCLUSTER_NAMESPACE_PATH.read_text(encoding="utf-8").strip()
            if ns:
                return ns
    except Exception:
        pass
    return default


def _sanitize_k8s_label(value: str, *, max_len: int = 32) -> str:
    cleaned = re.sub(r"[^a-z0-9-]", "-", (value or "").lower())
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    return cleaned[:max_len].strip("-") or "default"


def resolve_k8s_target_namespace(
    backend: Backend,
    default_ns: str,
    *,
    workload: str = "runner",
    project_slug: str | None = None,
) -> str:
    """Resolve the target Kubernetes namespace for an SDLC stage runner or sandbox session.

    Supports three isolation modes in `backend.config['namespace_isolation']`:
    - `'shared'`: both SDLC stage Jobs and sandbox Pods run in `namespace` (unless `sandbox_namespace` is set).
    - `'workload'`: SDLC stage Jobs run in `namespace`; sandbox/session Pods run in `sandbox_namespace`
      (defaults to `<namespace>-sandboxes`).
    - `'project'`: isolates per project (`<namespace>-<project_slug>` for SDLC stage Jobs,
      and `<sandbox_namespace>-<project_slug>` for sandbox/session Pods).
    """
    cfg = backend.config or {}
    isolation = str(
        cfg.get("namespace_isolation") or cfg.get("namespaceIsolation") or ""
    ).strip().lower()
    base_runner_ns = _sanitize_k8s_label(default_ns or "default", max_len=48)

    if isolation == "shared":
        return base_runner_ns

    if workload == "sandbox":
        explicit_sbx_ns = str(
            cfg.get("sandbox_namespace") or settings.SANDBOX_K8S_NAMESPACE or ""
        ).strip()
        if explicit_sbx_ns:
            base_ns = _sanitize_k8s_label(explicit_sbx_ns, max_len=48)
        elif isolation in ("workload", "project"):
            base_ns = _sanitize_k8s_label(f"{base_runner_ns}-sandboxes", max_len=48)
        else:
            base_ns = base_runner_ns
    else:
        base_ns = base_runner_ns

    if isolation == "project" and project_slug:
        slug_part = _sanitize_k8s_label(project_slug, max_len=24)
        return _sanitize_k8s_label(f"{base_ns}-{slug_part}", max_len=63)

    return base_ns


def _build_k8s_default_egress_network_policy(
    target_ns: str, control_plane_ns: str
) -> dict[str, Any]:
    """Build a default egress NetworkPolicy blocking RFC1918 private CIDRs except DNS and control-plane."""
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "gitseed-default-egress",
            "namespace": target_ns,
            "labels": {
                "app.gitseed.managed": "true",
            },
        },
        "spec": {
            "podSelector": {},
            "policyTypes": ["Egress"],
            "egress": [
                {
                    "ports": [
                        {"protocol": "UDP", "port": 53},
                        {"protocol": "TCP", "port": 53},
                    ],
                },
                {
                    "to": [
                        {
                            "namespaceSelector": {
                                "matchLabels": {
                                    "kubernetes.io/metadata.name": control_plane_ns,
                                }
                            }
                        }
                    ],
                },
                {
                    "to": [
                        {
                            "ipBlock": {
                                "cidr": "0.0.0.0/0",
                                "except": [
                                    "10.0.0.0/8",
                                    "172.16.0.0/12",
                                    "192.168.0.0/16",
                                    "169.254.0.0/16",
                                    "100.64.0.0/10",
                                ],
                            }
                        }
                    ],
                },
            ],
        },
    }


def _resolve_k8s_control_plane_namespace(fallback_ns: str) -> str:
    """Resolve the Kubernetes namespace hosting the GitSeed control plane (`api` / `egress-proxy`)."""
    explicit = (getattr(settings, "CONTROL_PLANE_K8S_NAMESPACE", "") or "").strip()
    if explicit:
        return explicit
    try:
        if INCLUSTER_NAMESPACE_PATH.is_file():
            in_cluster_ns = INCLUSTER_NAMESPACE_PATH.read_text(encoding="utf-8").strip()
            if in_cluster_ns:
                return in_cluster_ns
    except Exception:
        pass
    return fallback_ns


async def _ensure_k8s_namespace_network_policy(
    client: httpx.AsyncClient,
    base_url: str,
    target_ns: str,
    control_plane_ns: str,
) -> None:
    """Idempotently apply default egress NetworkPolicy (`POST` ignoring `409 Conflict`), failing closed on RBAC/API errors."""
    np_resp = await client.post(
        f"{base_url}/apis/networking.k8s.io/v1/namespaces/{quote(target_ns, safe='')}/networkpolicies",
        json=_build_k8s_default_egress_network_policy(target_ns, control_plane_ns),
    )
    if np_resp.status_code not in (200, 201, 202, 409):
        raise RuntimeError(
            f"Kubernetes NetworkPolicy apply in {target_ns} failed with HTTP {np_resp.status_code}"
        )


async def _ensure_k8s_namespace(
    client: httpx.AsyncClient,
    base_url: str,
    target_ns: str,
    fallback_ns: str,
    *,
    project_slug: str | None = None,
    workload: str = "runner",
) -> str:
    """Ensure `target_ns` exists with an active egress NetworkPolicy, falling back to `fallback_ns` on RBAC errors."""
    if not target_ns or target_ns == fallback_ns:
        return fallback_ns
    control_plane_ns = _resolve_k8s_control_plane_namespace(fallback_ns)
    try:
        get_resp = await client.get(f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}")
        if get_resp.status_code == 200:
            await _ensure_k8s_namespace_network_policy(
                client, base_url, target_ns, control_plane_ns
            )
            return target_ns
        if get_resp.status_code == 404:
            labels: dict[str, str] = {
                "app.gitseed.managed": "true",
                "app.gitseed.workload": workload,
            }
            if project_slug:
                labels["app.gitseed.project_slug"] = _sanitize_k8s_label(project_slug, max_len=63)
            create_resp = await client.post(
                f"{base_url}/api/v1/namespaces",
                json={
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {
                        "name": target_ns,
                        "labels": labels,
                    },
                },
            )
            if create_resp.status_code in (200, 201, 202, 409):
                await _ensure_k8s_namespace_network_policy(
                    client, base_url, target_ns, control_plane_ns
                )
                return target_ns
    except Exception as exc:
        logger.warning(
            "Kubernetes isolated namespace/NetworkPolicy setup for %s fell back to %s: %s",
            target_ns,
            fallback_ns,
            exc,
        )
    return fallback_ns


def _split_k8s_ref(default_ns: str, container_id: str) -> tuple[str, str]:
    """Split a `namespace/name` or `name` Kubernetes workload identifier."""
    cleaned = (container_id or "").strip()
    if "/" in cleaned:
        ns, name = cleaned.split("/", 1)
        return (ns.strip() or default_ns), name.strip()
    return default_ns, cleaned


def _format_k8s_ref(default_ns: str, target_ns: str, name: str) -> str:
    """Return `name` when in `default_ns`, or `target_ns/name` when isolated in another namespace."""
    if target_ns and target_ns != default_ns:
        return f"{target_ns}/{name}"
    return name


def _is_k8s_job(name: str) -> bool:
    """Return True if the Kubernetes workload name is a Batch Job (`gitseed-run-*`) rather than a Pod."""
    return name.startswith("gitseed-run-")


def build_docker_ssl_context(secrets_map: dict[str, Any]) -> ssl.SSLContext | None:
    """Build an mTLS/CA SSLContext for remote `tcp://` / `https://` Docker daemons when TLS secrets exist."""
    tls_ca = (secrets_map.get("tls_ca") or "").strip()
    tls_cert = (secrets_map.get("tls_cert") or "").strip()
    tls_key = (secrets_map.get("tls_key") or "").strip()
    if not (tls_ca or tls_cert):
        return None
    ctx = ssl.create_default_context(cadata=tls_ca) if tls_ca else ssl.create_default_context()
    if tls_cert:
        cert_path: str | None = None
        key_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as cf:
                cf.write(tls_cert)
                cert_path = cf.name
            os.chmod(cert_path, 0o600)
            if tls_key:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as kf:
                    kf.write(tls_key)
                    key_path = kf.name
                os.chmod(key_path, 0o600)
            ctx.load_cert_chain(certfile=cert_path, keyfile=key_path)
        finally:
            for p in (cert_path, key_path):
                if p:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
    return ctx


def _docker_client_for_backend(backend: Backend) -> tuple[httpx.AsyncClient, str]:
    """Return an httpx.AsyncClient and base URL configured for a Docker compute backend."""
    default_ep = getattr(settings, "DOCKER_HOST", "unix:///var/run/docker.sock") or "unix:///var/run/docker.sock"
    target = (backend.endpoint or default_ep).strip().rstrip("/")
    if target == "unix:///var/run/docker.sock" and default_ep != target and not os.path.exists("/var/run/docker.sock"):
        target = default_ep.strip().rstrip("/")
    if target.startswith("unix://"):
        sock_path = target.removeprefix("unix://")
        transport = httpx.AsyncHTTPTransport(uds=sock_path)
        return httpx.AsyncClient(transport=transport, timeout=20.0), "http://docker"

    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    has_tls_secrets = bool(secrets_map.get("tls_cert") or secrets_map.get("tls_ca"))
    if target.startswith("tcp://"):
        scheme = "https://" if has_tls_secrets else "http://"
        base_url = scheme + target.removeprefix("tcp://")
    elif target.startswith(("http://", "https://")):
        base_url = target
    else:
        base_url = f"http://{target}"
    ssl_ctx = build_docker_ssl_context(secrets_map) if base_url.startswith("https://") else None
    if ssl_ctx is not None:
        return httpx.AsyncClient(verify=ssl_ctx, timeout=20.0), base_url
    return httpx.AsyncClient(timeout=20.0), base_url


async def _ensure_docker_network(
    client: httpx.AsyncClient, base_url: str, network_name: str
) -> None:
    """Ensure the isolated runner/sandbox bridge network exists in Docker."""
    resp = await client.get(f"{base_url}/networks/{quote(network_name, safe='')}")
    if resp.status_code == 200:
        return
    await client.post(
        f"{base_url}/networks/create",
        json={
            "Name": network_name,
            "Driver": "bridge",
            "Internal": True,
            "CheckDuplicate": True,
            "Labels": {"app.gitseed.managed": "true"},
        },
    )


def _k8s_client_for_backend(backend: Backend) -> tuple[httpx.AsyncClient, str, str]:
    """Return (httpx.AsyncClient, base_url, namespace) for a Kubernetes compute backend."""
    target = (backend.endpoint or settings.K8S_API_URL or "https://kubernetes.default.svc").strip().rstrip("/")
    if not target.startswith(("http://", "https://")):
        target = f"https://{target}"
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    token = str(
        secrets_map.get("token")
        or secrets_map.get("bearer_token")
        or read_incluster_k8s_token()
        or ""
    ).strip()
    cfg = backend.config or {}
    default_ns = settings.RUNNER_K8S_NAMESPACE.strip() or read_incluster_k8s_namespace("default")
    namespace = str(cfg.get("namespace") or default_ns).strip() or "default"
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    verify_ssl = _resolve_k8s_ssl_context(backend, secrets_map)
    return httpx.AsyncClient(headers=headers, verify=verify_ssl, timeout=20.0), target, namespace


async def _spawn_k8s_runner_job(
    *,
    backend: Backend,
    run: Run,
    seed: Seed,
    project: Project,
    run_token: str,
    image: str,
    cpus: float,
    memory_mb: int,
    env_map: dict[str, str],
    prepare_env: dict[str, str] | None = None,
    agent_env: dict[str, str] | None = None,
    finalize_env: dict[str, str] | None = None,
) -> str:
    """Create a Kubernetes Batch v1/Job with prepare initContainer and agent + finalize containers."""
    job_name = f"gitseed-run-{run.id}".lower().replace("_", "-")
    cfg = backend.config or {}
    labels = {
        "app.gitseed.managed": "true",
        "app.kubernetes.io/managed-by": "gitseed",
        "app.gitseed.workload": "runner",
        "app.gitseed.run_id": run.id,
        "app.gitseed.seed_id": seed.id,
        "app.gitseed.project_id": project.id,
        "app.gitseed.stage": str(run.stage),
    }

    p_env = dict(prepare_env or {**env_map, "GITSEED_RUNNER_MODE": "prepare"})
    a_env = dict(agent_env or {**env_map, "GITSEED_RUNNER_MODE": "agent"})
    f_env = dict(finalize_env or {**env_map, "GITSEED_RUNNER_MODE": "finalize"})

    sec_ctx = {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }
    res_spec = {
        "limits": {
            "cpu": f"{cpus}",
            "memory": f"{memory_mb}Mi",
        },
        "requests": {
            "cpu": f"{max(0.25, cpus / 2)}",
            "memory": f"{max(256, memory_mb // 2)}Mi",
        },
    }
    helper_res_spec = {
        "limits": {
            "cpu": "1.0",
            "memory": "1024Mi",
        },
        "requests": {
            "cpu": "0.25",
            "memory": "256Mi",
        },
    }

    sbx_cfg = project.sandbox_config if isinstance(project.sandbox_config, dict) else {}
    is_second_opinion = isinstance(run.output, dict) and bool(
        run.output.get("second_opinion")
    )
    enable_warm_ws = bool(sbx_cfg.get("enable_warm_workspace", True)) and not is_second_opinion
    ws_subpath: str | None = None
    ws_volume: dict[str, Any] = {"name": "workspace", "emptyDir": {}}
    if cfg.get("workspace_pvc"):
        ws_volume = {
            "name": "workspace",
            "persistentVolumeClaim": {"claimName": str(cfg["workspace_pvc"]).strip()},
        }
        ws_subpath = (
            f"ws-{seed.id}" if (seed.id and enable_warm_ws) else f"ws-run-{run.id}"
        )

    ws_mount_rw: dict[str, Any] = {
        "name": "workspace",
        "mountPath": "/workspace",
        "readOnly": False,
        **({"subPath": ws_subpath} if ws_subpath else {}),
    }
    ws_mount_ro: dict[str, Any] = {
        "name": "workspace",
        "mountPath": "/workspace",
        "readOnly": True,
        **({"subPath": ws_subpath} if ws_subpath else {}),
    }

    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "shareProcessNamespace": False,
        "initContainers": [
            {
                "name": "prepare",
                "image": image,
                "env": [{"name": k, "value": v} for k, v in p_env.items()],
                "resources": helper_res_spec,
                "securityContext": sec_ctx,
                "volumeMounts": [
                    ws_mount_rw,
                    {"name": "ctl", "mountPath": "/gitseed-ctl", "readOnly": False},
                    {
                        "name": "gitseed-secrets",
                        "mountPath": "/gitseed-secrets",
                        "readOnly": False,
                    },
                    {
                        "name": "git-mirror",
                        "mountPath": "/home/runner/.cache/git-mirrors",
                        "readOnly": False,
                    },
                ],
            }
        ],
        "containers": [
            {
                "name": "agent",
                "image": image,
                "env": [{"name": k, "value": v} for k, v in a_env.items()],
                "resources": res_spec,
                "securityContext": sec_ctx,
                "volumeMounts": [
                    dict(ws_mount_rw),
                    {"name": "ctl", "mountPath": "/gitseed-ctl", "readOnly": False},
                    {
                        "name": "pkg-cache",
                        "mountPath": "/home/runner/.cache/packages",
                        "readOnly": False,
                    },
                ],
            },
            {
                "name": "finalize",
                "image": image,
                "env": [{"name": k, "value": v} for k, v in f_env.items()],
                "resources": helper_res_spec,
                "securityContext": sec_ctx,
                "volumeMounts": [
                    ws_mount_ro,
                    {"name": "ctl", "mountPath": "/gitseed-ctl", "readOnly": True},
                    {
                        "name": "gitseed-secrets",
                        "mountPath": "/gitseed-secrets",
                        "readOnly": True,
                    },
                    {
                        "name": "git-mirror",
                        "mountPath": "/home/runner/.cache/git-mirrors",
                        "readOnly": True,
                    },
                ],
            },
        ],
        "volumes": [
            ws_volume,
            {"name": "ctl", "emptyDir": {}},
            {"name": "gitseed-secrets", "emptyDir": {}},
            {"name": "git-mirror", "emptyDir": {}},
            {"name": "pkg-cache", "emptyDir": {}},
        ],
    }
    if cfg.get("service_account"):
        pod_spec["serviceAccountName"] = str(cfg["service_account"]).strip()

    job_manifest: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "labels": labels,
        },
        "spec": {
            "backoffLimit": 0,
            "ttlSecondsAfterFinished": 300,
            "template": {
                "metadata": {"labels": labels},
                "spec": pod_spec,
            },
        },
    }

    client, base_url, default_ns = _k8s_client_for_backend(backend)
    desired_ns = resolve_k8s_target_namespace(
        backend, default_ns, workload="runner", project_slug=project.slug
    )
    async with client:
        target_ns = await _ensure_k8s_namespace(
            client,
            base_url,
            desired_ns,
            default_ns,
            project_slug=project.slug,
            workload="runner",
        )
        await client.delete(
            f"{base_url}/apis/batch/v1/namespaces/{quote(target_ns, safe='')}/jobs/{quote(job_name, safe='')}?propagationPolicy=Background"
        )
        resp = await client.post(
            f"{base_url}/apis/batch/v1/namespaces/{quote(target_ns, safe='')}/jobs",
            json=job_manifest,
        )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"Kubernetes Job create failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )
        return _format_k8s_ref(default_ns, target_ns, job_name)


async def spawn_k8s_pod(
    *,
    backend: Backend,
    pod_name: str,
    image: str,
    command: list[str],
    args: list[str] | None = None,
    env_map: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    cpus: float = 2.0,
    memory_mb: int = 2048,
    as_root: bool = False,
    tty: bool = True,
    stdin: bool = True,
    working_dir: str = "/workspace",
    project_slug: str | None = None,
    workload: str = "sandbox",
    wait_running_seconds: float = 45.0,
) -> str:
    """Create a Kubernetes v1/Pod in the target sandbox/session namespace and optionally wait until Running."""
    clean_name = _sanitize_k8s_label(pod_name, max_len=63)
    cfg = backend.config or {}
    merged_labels = {
        "app.gitseed.managed": "true",
        "app.kubernetes.io/managed-by": "gitseed",
        "app.gitseed.workload": workload,
        **(labels or {}),
    }

    sec_ctx: dict[str, Any] = {
        "allowPrivilegeEscalation": bool(as_root),
        "capabilities": {
            "drop": ["ALL"],
            **(
                {"add": ["SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER"]}
                if as_root
                else {}
            ),
        },
    }
    if as_root:
        sec_ctx["runAsUser"] = 0

    pod_ws_volume: dict[str, Any] = {"name": "workspace", "emptyDir": {}}
    pod_ws_mount: dict[str, Any] = {"name": "workspace", "mountPath": "/workspace"}
    if cfg.get("workspace_pvc"):
        pod_ws_volume = {
            "name": "workspace",
            "persistentVolumeClaim": {"claimName": str(cfg["workspace_pvc"]).strip()},
        }
        sub_id = (
            str(
                merged_labels.get("app.gitseed.seed_id")
                or merged_labels.get("app.gitseed.session_id")
                or clean_name
            ).strip()
        )
        pod_ws_mount["subPath"] = f"ws-{sub_id}"

    container_spec: dict[str, Any] = {
        "name": "sandbox",
        "image": image,
        "command": command,
        **({"args": args} if args else {}),
        "workingDir": working_dir,
        "tty": tty,
        "stdin": stdin,
        "env": [{"name": k, "value": v} for k, v in (env_map or {}).items()],
        "resources": {
            "limits": {
                "cpu": f"{cpus}",
                "memory": f"{memory_mb}Mi",
            },
            "requests": {
                "cpu": f"{max(0.25, cpus / 2)}",
                "memory": f"{max(256, memory_mb // 2)}Mi",
            },
        },
        "securityContext": sec_ctx,
        "volumeMounts": [
            pod_ws_mount,
        ],
    }

    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "containers": [container_spec],
        "volumes": [
            pod_ws_volume,
        ],
    }
    if cfg.get("sandbox_service_account"):
        pod_spec["serviceAccountName"] = str(cfg["sandbox_service_account"]).strip()

    pod_manifest: dict[str, Any] = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": clean_name,
            "labels": merged_labels,
        },
        "spec": pod_spec,
    }

    client, base_url, default_ns = _k8s_client_for_backend(backend)
    desired_ns = resolve_k8s_target_namespace(
        backend, default_ns, workload=workload, project_slug=project_slug
    )
    async with client:
        target_ns = await _ensure_k8s_namespace(
            client,
            base_url,
            desired_ns,
            default_ns,
            project_slug=project_slug,
            workload=workload,
        )
        await client.delete(
            f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods/{quote(clean_name, safe='')}?gracePeriodSeconds=0"
        )
        resp = await client.post(
            f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods",
            json=pod_manifest,
        )
        if resp.status_code == 409:
            await asyncio.sleep(1.0)
            resp = await client.post(
                f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods",
                json=pod_manifest,
            )
        if resp.status_code not in (200, 201, 202):
            raise RuntimeError(
                f"Kubernetes Pod create failed (HTTP {resp.status_code}): {resp.text[:300]}"
            )

        ref = _format_k8s_ref(default_ns, target_ns, clean_name)
        if wait_running_seconds > 0:
            attempts = max(1, int(wait_running_seconds / 0.5))
            for _ in range(attempts):
                insp = await client.get(
                    f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods/{quote(clean_name, safe='')}"
                )
                if insp.status_code == 200:
                    phase = str(((insp.json() or {}).get("status") or {}).get("phase") or "")
                    if phase in ("Running", "Succeeded", "Failed"):
                        break
                await asyncio.sleep(0.5)
        return ref


def _ws_encode_frame(payload: bytes, *, opcode: int = 0x2) -> bytes:
    """Encode a masked RFC 6455 client-to-server WebSocket frame."""
    header = bytearray([0x80 | (opcode & 0x0F)])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(n.to_bytes(2, "big"))
    else:
        header.append(0x80 | 127)
        header.extend(n.to_bytes(8, "big"))
    mask = os.urandom(4)
    header.extend(mask)
    if not payload:
        return bytes(header)
    mask_rep = (mask * ((n // 4) + 1))[:n]
    masked = (int.from_bytes(payload, "big") ^ int.from_bytes(mask_rep, "big")).to_bytes(n, "big")
    return bytes(header) + masked


async def _ws_read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
    """Read a single RFC 6455 WebSocket frame from `reader`, returning `(opcode, payload)`."""
    try:
        b0, b1 = await reader.readexactly(2)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    opcode = b0 & 0x0F
    masked = bool(b1 & 0x80)
    length = b1 & 0x7F
    if length == 126:
        length = int.from_bytes(await reader.readexactly(2), "big")
    elif length == 127:
        length = int.from_bytes(await reader.readexactly(8), "big")
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(length) if length > 0 else b""
    if masked and payload:
        mask_rep = (mask * ((length // 4) + 1))[:length]
        payload = (int.from_bytes(payload, "big") ^ int.from_bytes(mask_rep, "big")).to_bytes(
            length, "big"
        )
    return opcode, payload


async def k8s_open_exec_ws(
    backend: Backend,
    container_id: str,
    cmd: list[str],
    *,
    stdin: bool = False,
    stdout: bool = True,
    stderr: bool = True,
    tty: bool = False,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open an upgraded `v4.channel.k8s.io` WebSocket stream to a Kubernetes Pod's `/exec` endpoint."""
    _, base_url, default_ns = _k8s_client_for_backend(backend)
    target_ns, pod_name = _split_k8s_ref(default_ns, container_id)
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    token = str(
        secrets_map.get("token")
        or secrets_map.get("bearer_token")
        or read_incluster_k8s_token()
        or ""
    ).strip()

    parts = urlsplit(base_url)
    is_tls = parts.scheme == "https"
    port = parts.port or (443 if is_tls else 80)
    ssl_ctx: ssl.SSLContext | None = None
    if is_tls:
        resolved_ssl = _resolve_k8s_ssl_context(backend, secrets_map)
        if isinstance(resolved_ssl, ssl.SSLContext):
            ssl_ctx = resolved_ssl
        elif resolved_ssl is False:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
        else:
            ssl_ctx = ssl.create_default_context()

    reader, writer = await asyncio.open_connection(parts.hostname, port, ssl=ssl_ctx)
    query_pairs: list[tuple[str, str]] = [
        ("stdin", "true" if stdin else "false"),
        ("stdout", "true" if stdout else "false"),
        ("stderr", "true" if stderr else "false"),
        ("tty", "true" if tty else "false"),
    ]
    for c in cmd:
        query_pairs.append(("command", str(c)))
    base_path = parts.path.rstrip("/")
    req_path = (
        f"{base_path}/api/v1/namespaces/{quote(target_ns, safe='')}"
        f"/pods/{quote(pod_name, safe='')}/exec?{urlencode(query_pairs)}"
    )
    ws_key = base64.b64encode(os.urandom(16)).decode("ascii")
    headers = [
        f"GET {req_path} HTTP/1.1",
        f"Host: {parts.netloc}",
        "Connection: Upgrade",
        "Upgrade: websocket",
        "Sec-WebSocket-Version: 13",
        f"Sec-WebSocket-Key: {ws_key}",
        "Sec-WebSocket-Protocol: v4.channel.k8s.io",
    ]
    if token:
        headers.append(f"Authorization: Bearer {token}")
    request_bytes = ("\r\n".join(headers) + "\r\n\r\n").encode("latin-1")
    writer.write(request_bytes)
    await writer.drain()

    resp_header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15.0)
    status_line = resp_header.split(b"\r\n", 1)[0].decode("latin-1", errors="replace")
    if " 101 " not in status_line:
        writer.close()
        raise RuntimeError(f"Kubernetes exec upgrade failed: {status_line}")
    return reader, writer


async def k8s_exec_command(
    backend: Backend,
    container_id: str,
    cmd: list[str],
    *,
    stdin_data: bytes | None = None,
    timeout: float = 60.0,
) -> tuple[int, bytes, bytes]:
    """Run a non-interactive command in a Kubernetes Pod via `/exec` and return `(exit_code, stdout, stderr)`."""
    reader, writer = await k8s_open_exec_ws(
        backend,
        container_id,
        cmd,
        stdin=stdin_data is not None,
        stdout=True,
        stderr=True,
        tty=False,
    )
    stdout_buf = bytearray()
    stderr_buf = bytearray()
    exit_code = 0
    try:
        if stdin_data:
            chunk_size = 32768
            for idx in range(0, len(stdin_data), chunk_size):
                chunk = stdin_data[idx : idx + chunk_size]
                writer.write(_ws_encode_frame(b"\x00" + chunk))
            await writer.drain()

        async def _read_loop() -> None:
            nonlocal exit_code
            while True:
                frame = await _ws_read_frame(reader)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:  # Close
                    break
                if opcode == 0x9:  # Ping -> Pong
                    writer.write(_ws_encode_frame(payload, opcode=0xA))
                    await writer.drain()
                    continue
                if not payload:
                    continue
                channel = payload[0]
                data = payload[1:]
                if channel == 1:
                    stdout_buf.extend(data)
                elif channel == 2:
                    stderr_buf.extend(data)
                elif channel == 3 and data:
                    try:
                        status_obj = json.loads(data.decode("utf-8", errors="replace"))
                        if isinstance(status_obj, dict) and status_obj.get("status") == "Failure":
                            exit_code = 1
                            causes = ((status_obj.get("details") or {}).get("causes")) or []
                            for c in causes:
                                if isinstance(c, dict) and c.get("reason") == "ExitCode":
                                    exit_code = int(c.get("message") or 1)
                    except Exception:
                        pass

        await asyncio.wait_for(_read_loop(), timeout=timeout)
    finally:
        try:
            writer.write(_ws_encode_frame(b"", opcode=0x8))
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
    return exit_code, bytes(stdout_buf), bytes(stderr_buf)


async def k8s_put_archive(
    backend: Backend,
    container_id: str,
    path: str,
    tar_bytes: bytes,
    *,
    timeout: float = 60.0,
) -> None:
    """Upload a tar archive into `path` inside a running Kubernetes Pod."""
    if not tar_bytes:
        return
    clean_dest = (path or "/").strip() or "/"
    n_bytes = len(tar_bytes)
    extract_cmd = [
        "sh",
        "-c",
        'mkdir -p "$1" && head -c "$2" | tar -xf - -C "$1"',
        "sh",
        clean_dest,
        str(n_bytes),
    ]
    rc, _, err = await k8s_exec_command(
        backend,
        container_id,
        extract_cmd,
        stdin_data=tar_bytes,
        timeout=timeout,
    )
    if rc != 0:
        raise RuntimeError(
            f"Kubernetes archive upload to {clean_dest} failed (exit {rc}): {err.decode('utf-8', errors='replace')[:200]}"
        )


async def k8s_fetch_archive(
    backend: Backend,
    container_id: str,
    path: str,
    *,
    timeout: float = 120.0,
) -> bytes | None:
    """Stream a tar archive of `path` out of a running Kubernetes Pod."""
    clean = PurePosixPath((path or "/workspace").strip() or "/workspace")
    parent = str(clean.parent) if str(clean.parent) else "/"
    base = clean.name or "."
    tar_cmd = [
        "sh",
        "-c",
        'test -e "$1" && tar -cf - -C "$2" "$3"',
        "sh",
        str(clean),
        parent,
        base,
    ]
    try:
        rc, out, _ = await k8s_exec_command(
            backend,
            container_id,
            tar_cmd,
            timeout=timeout,
        )
        if rc != 0 or not out:
            return None
        return out
    except Exception as exc:
        logger.debug("Kubernetes archive fetch of %s from %s failed: %s", path, container_id, exc)
        return None


def _build_role_environments(
    *,
    run: Run,
    seed: Seed,
    project: Project,
    api_url: str,
    gateway_url: str,
    prepare_token: str,
    agent_token: str,
    finalize_token: str,
    sbx_cfg: dict[str, Any],
    timeout_seconds: int = 3600,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Construct separate environment dicts for prepare, agent, and finalize containers."""
    effective_timeout = max(60, int(timeout_seconds))
    finalize_wait = max(3600, effective_timeout + 600)
    common_env = {
        "GITSEED_RUN_ID": run.id,
        "GITSEED_SEED_ID": seed.id,
        "GITSEED_PROJECT_SLUG": project.slug,
        "GITSEED_STAGE": str(run.stage),
        "GITSEED_RUNTIME": str(run.runtime),
        "GITSEED_MODEL": run.model,
        "GITSEED_API_URL": api_url,
        "GITSEED_GATEWAY_URL": gateway_url,
        "GITSEED_WORKSPACE": "/workspace",
        "GITSEED_CTL_DIR": "/gitseed-ctl",
        "RUNNER_TIMEOUT_SECONDS": str(effective_timeout),
        "GITSEED_FINALIZE_WAIT_SECONDS": str(finalize_wait),
    }
    prepare_env = {
        **common_env,
        "GITSEED_RUNNER_MODE": "prepare",
        "GITSEED_RUN_TOKEN": prepare_token,
        "GITSEED_GIT_CACHE_DIR": "/home/runner/.cache/git-mirrors",
    }
    agent_env = {
        **common_env,
        "GITSEED_RUNNER_MODE": "agent",
        "GITSEED_RUN_TOKEN": agent_token,
        "UV_CACHE_DIR": "/home/runner/.cache/packages/uv",
        "PIP_CACHE_DIR": "/home/runner/.cache/packages/pip",
        "NPM_CONFIG_CACHE": "/home/runner/.cache/packages/npm",
        "PNPM_STORE_DIR": "/home/runner/.cache/packages/pnpm",
        "GOCACHE": "/home/runner/.cache/packages/go-build",
    }
    max_turns_val = int(
        sbx_cfg.get("max_turns") or getattr(settings, "RUNNER_MAX_TURNS", 0) or 0
    )
    if max_turns_val > 0:
        agent_env["GITSEED_MAX_TURNS"] = str(max_turns_val)

    finalize_env = {
        **common_env,
        "GITSEED_RUNNER_MODE": "finalize",
        "GITSEED_RUN_TOKEN": finalize_token,
        "GITSEED_GIT_CACHE_DIR": "/home/runner/.cache/git-mirrors",
    }

    proxy_url = str(getattr(settings, "RUNNER_EGRESS_PROXY_URL", "") or "").strip()
    if proxy_url:
        internal_hosts = str(
            getattr(settings, "EGRESS_INTERNAL_HOSTS", "litellm,api") or "litellm,api"
        ).strip()
        no_proxy_val = f"{internal_hosts},localhost,127.0.0.1,host.docker.internal"
        for target_env in (prepare_env, agent_env, finalize_env):
            target_env["HTTPS_PROXY"] = proxy_url
            target_env["HTTP_PROXY"] = proxy_url
            target_env["https_proxy"] = proxy_url
            target_env["http_proxy"] = proxy_url
            target_env["NO_PROXY"] = no_proxy_val
            target_env["no_proxy"] = no_proxy_val

    return prepare_env, agent_env, finalize_env


async def spawn_docker_runner_container(
    *,
    backend: Backend,
    run: Run,
    seed: Seed,
    project: Project,
    run_token: str = "",
    prepare_token: str | None = None,
    agent_token: str | None = None,
    finalize_token: str | None = None,
    ttl_seconds: int = 14400,
    timeout_seconds: int | None = None,
) -> str:
    """Create and start isolated 3-container broker (`prepare`, `agent`, `finalize`) on Docker or Kubernetes."""
    cfg = backend.config or {}
    sbx_cfg = project.sandbox_config if isinstance(project.sandbox_config, dict) else {}
    image = str(cfg.get("runner_image") or settings.RUNNER_IMAGE).strip()
    network_name = str(cfg.get("network") or settings.RUNNER_DOCKER_NETWORK).strip()
    cpus = float(sbx_cfg.get("cpus") or cfg.get("cpus") or 2.0)
    memory_mb = int(sbx_cfg.get("memory_mb") or cfg.get("memory_mb") or 4096)

    resolved_timeout = (
        int(timeout_seconds)
        if timeout_seconds is not None and int(timeout_seconds) > 0
        else int(sbx_cfg.get("timeout_seconds") or 0)
        or (int(sbx_cfg.get("timeout_minutes") or 0) * 60)
        or (max(60, ttl_seconds - 600) if ttl_seconds != 14400 else 3600)
    )
    effective_ttl = max(ttl_seconds, resolved_timeout + 600)

    api_url = str(cfg.get("api_url") or settings.RUNNER_API_URL).strip().rstrip("/")
    gateway_url = str(cfg.get("gateway_url") or settings.RUNNER_GATEWAY_URL).strip().rstrip("/")

    p_tok = prepare_token or create_run_token(
        run.id, ttl_seconds=effective_ttl, scope="prepare"
    )
    if agent_token:
        a_tok = agent_token
    elif run_token and (scoped := verify_scoped_run_token(run_token)) and scoped[1] == "agent":
        a_tok = run_token
    else:
        a_tok = create_run_token(run.id, ttl_seconds=effective_ttl, scope="agent")
    f_tok = finalize_token or create_run_token(
        run.id, ttl_seconds=effective_ttl, scope="finalize"
    )

    prepare_env, agent_env, finalize_env = _build_role_environments(
        run=run,
        seed=seed,
        project=project,
        api_url=api_url,
        gateway_url=gateway_url,
        prepare_token=p_tok,
        agent_token=a_tok,
        finalize_token=f_tok,
        sbx_cfg=sbx_cfg,
        timeout_seconds=resolved_timeout,
    )

    if backend.provider.lower() in ("kubernetes", "k8s"):
        return await _spawn_k8s_runner_job(
            backend=backend,
            run=run,
            seed=seed,
            project=project,
            run_token=a_tok,
            image=image,
            cpus=cpus,
            memory_mb=memory_mb,
            env_map=agent_env,
            prepare_env=prepare_env,
            agent_env=agent_env,
            finalize_env=finalize_env,
        )

    enable_cache = bool(sbx_cfg.get("enable_dependency_cache", True))
    is_second_opinion = isinstance(run.output, dict) and bool(
        run.output.get("second_opinion")
    )
    enable_warm_ws = bool(sbx_cfg.get("enable_warm_workspace", True)) and not is_second_opinion
    ws_vol_name = (
        f"gitseed-ws-{seed.id.lower()}"
        if enable_warm_ws
        else f"gitseed-ws-run-{run.id.lower()}"
    )
    ctl_vol_name = f"gitseed-ctl-{run.id.lower()}"
    secrets_vol_name = f"gitseed-secrets-{run.id.lower()}"
    mirror_vol_name = f"gitseed-git-mirror-{project.id.lower()}"
    cache_vol_name = f"gitseed-cache-{project.id.lower()}"

    prepare_binds = [
        f"{ws_vol_name}:/workspace",
        f"{ctl_vol_name}:/gitseed-ctl",
        f"{secrets_vol_name}:/gitseed-secrets",
        f"{mirror_vol_name}:/home/runner/.cache/git-mirrors",
    ]
    agent_binds = [
        f"{ws_vol_name}:/workspace",
        f"{ctl_vol_name}:/gitseed-ctl",
        *([f"{cache_vol_name}:/home/runner/.cache/packages"] if enable_cache else []),
    ]
    finalize_binds = [
        f"{ws_vol_name}:/workspace:ro",
        f"{ctl_vol_name}:/gitseed-ctl:ro",
        f"{secrets_vol_name}:/gitseed-secrets:ro",
        f"{mirror_vol_name}:/home/runner/.cache/git-mirrors:ro",
    ]

    base_labels = {
        "app.gitseed.managed": "true",
        "app.gitseed.run_id": run.id,
        "app.gitseed.seed_id": seed.id,
        "app.gitseed.project_id": project.id,
        "app.gitseed.stage": str(run.stage),
    }
    pids_limit = int(sbx_cfg.get("pids_limit") or cfg.get("pids_limit") or 512)

    role_specs: list[tuple[str, str, dict[str, str], list[str], list[str] | None]] = [
        (
            "prepare",
            f"gitseed-run-{run.id}-prepare",
            prepare_env,
            prepare_binds,
            ["host.docker.internal:host-gateway"],
        ),
        (
            "finalize",
            f"gitseed-run-{run.id}-finalize",
            finalize_env,
            finalize_binds,
            ["host.docker.internal:host-gateway"],
        ),
        (
            "agent",
            f"gitseed-run-{run.id}",
            agent_env,
            agent_binds,
            None,  # Never map host.docker.internal into untrusted agent container
        ),
    ]

    client, base_url = _docker_client_for_backend(backend)
    spawned_ids: list[str] = []
    primary_container_id = f"gitseed-run-{run.id}"
    async with client:
        await _ensure_docker_network(client, base_url, network_name)
        await client.post(
            f"{base_url}/volumes/create",
            json={
                "Name": ctl_vol_name,
                "Labels": {**base_labels, "app.gitseed.volume": "ctl"},
            },
        )
        await client.post(
            f"{base_url}/volumes/create",
            json={
                "Name": secrets_vol_name,
                "Labels": {**base_labels, "app.gitseed.volume": "secrets"},
            },
        )
        for role, c_name, role_env, role_binds, extra_hosts in role_specs:
            await client.delete(
                f"{base_url}/containers/{quote(c_name, safe='')}?force=true"
            )
            host_cfg: dict[str, Any] = {
                "NetworkMode": network_name,
                "NanoCPUs": int(cpus * 1_000_000_000),
                "Memory": int(memory_mb * 1024 * 1024),
                "PidsLimit": pids_limit,
                "Privileged": False,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "AutoRemove": False,
                "Binds": role_binds,
            }
            if extra_hosts:
                host_cfg["ExtraHosts"] = extra_hosts

            create_payload: dict[str, Any] = {
                "Image": image,
                "Env": [f"{k}={v}" for k, v in role_env.items()],
                "Labels": {
                    **base_labels,
                    "app.gitseed.role": role,
                },
                "HostConfig": host_cfg,
            }
            create_resp = await client.post(
                f"{base_url}/containers/create?name={quote(c_name, safe='')}",
                json=create_payload,
            )
            if create_resp.status_code not in (200, 201):
                for prev_id in spawned_ids:
                    await client.delete(
                        f"{base_url}/containers/{quote(prev_id, safe='')}?force=true"
                    )
                raise RuntimeError(
                    f"Docker create failed for {role} (HTTP {create_resp.status_code}): {create_resp.text}"
                )
            cid = str(create_resp.json().get("Id") or c_name)
            start_resp = await client.post(
                f"{base_url}/containers/{quote(cid, safe='')}/start"
            )
            if start_resp.status_code not in (200, 204, 304):
                await client.delete(
                    f"{base_url}/containers/{quote(cid, safe='')}?force=true"
                )
                for prev_id in spawned_ids:
                    await client.delete(
                        f"{base_url}/containers/{quote(prev_id, safe='')}?force=true"
                    )
                raise RuntimeError(
                    f"Docker container start failed for {role} (HTTP {start_resp.status_code}): {start_resp.text}"
                )
            spawned_ids.append(cid)
            if role == "prepare":
                # Wait for trusted prepare phase to complete before starting untrusted agent container
                prepare_wait_timeout = float(sbx_cfg.get("clone_timeout") or 300.0)
                try:
                    wait_resp = await client.post(
                        f"{base_url}/containers/{quote(cid, safe='')}/wait",
                        timeout=prepare_wait_timeout,
                    )
                    if wait_resp.status_code >= 400:
                        raise RuntimeError(
                            f"Runner prepare phase wait failed (HTTP {wait_resp.status_code})"
                        )
                    if wait_resp.status_code == 200:
                        wait_data = wait_resp.json()
                        prep_exit = int(
                            (wait_data.get("StatusCode") if isinstance(wait_data, dict) else 0)
                            or 0
                        )
                        if prep_exit != 0:
                            raise RuntimeError(
                                f"Runner prepare phase failed with exit code {prep_exit}"
                            )
                except Exception as exc:
                    for prev_id in spawned_ids:
                        try:
                            await client.delete(
                                f"{base_url}/containers/{quote(prev_id, safe='')}?force=true"
                            )
                        except Exception:
                            pass
                    if isinstance(exc, RuntimeError):
                        raise
                    raise RuntimeError(
                        f"Runner prepare phase did not complete cleanly: {exc}"
                    ) from exc
            elif role == "agent":
                primary_container_id = cid

        return primary_container_id


async def stop_and_remove_docker_container(
    backend: Backend,
    container_id: str | None,
    *,
    run_id: str | None = None,
) -> None:
    """Stop and remove runner container(s) (prepare, agent, finalize) or Kubernetes Job/Pod if they exist."""
    if not container_id and not run_id:
        return
    try:
        if backend.provider.lower() in ("kubernetes", "k8s"):
            if not container_id:
                return
            client, base_url, default_ns = _k8s_client_for_backend(backend)
            target_ns, name = _split_k8s_ref(default_ns, container_id)
            async with client:
                if _is_k8s_job(name):
                    await client.delete(
                        f"{base_url}/apis/batch/v1/namespaces/{quote(target_ns, safe='')}/jobs/{quote(name, safe='')}?propagationPolicy=Background"
                    )
                else:
                    await client.delete(
                        f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods/{quote(name, safe='')}?gracePeriodSeconds=1"
                    )
            return

        effective_run_id = run_id
        if not effective_run_id and container_id and container_id.startswith("gitseed-run-"):
            raw_suffix = container_id.removeprefix("gitseed-run-")
            for role_sfx in ("-prepare", "-agent", "-finalize"):
                if raw_suffix.endswith(role_sfx):
                    raw_suffix = raw_suffix[: -len(role_sfx)]
                    break
            if raw_suffix:
                effective_run_id = raw_suffix

        protected_infra = {
            "gitseed-api",
            "gitseed-postgresql",
            "gitseed-postgres",
            "gitseed-litellm",
            "gitseed-web",
            "gitseed-gitea",
            "gitseed-egress-proxy",
            "gitseed-docker-proxy",
            "gitseed-mcp",
        }
        targets: list[str] = []
        if container_id and container_id.strip().lower() not in protected_infra:
            targets.append(container_id.strip())
        if effective_run_id:
            for c_name in (
                f"gitseed-run-{effective_run_id}-prepare",
                f"gitseed-run-{effective_run_id}",
                f"gitseed-run-{effective_run_id}-finalize",
            ):
                if c_name not in targets:
                    targets.append(c_name)

        client, base_url = _docker_client_for_backend(backend)
        async with client:
            for target in targets:
                await client.post(
                    f"{base_url}/containers/{quote(target, safe='')}/stop?t=3"
                )
                await client.delete(
                    f"{base_url}/containers/{quote(target, safe='')}?force=true"
                )
            if effective_run_id:
                for vol in (
                    f"gitseed-ctl-{effective_run_id.lower()}",
                    f"gitseed-secrets-{effective_run_id.lower()}",
                    f"gitseed-ws-run-{effective_run_id.lower()}",
                ):
                    await client.delete(
                        f"{base_url}/volumes/{quote(vol, safe='')}?force=true"
                    )
    except Exception as exc:
        logger.debug("Container cleanup for %s ignored error: %s", container_id, exc)


async def remove_docker_seed_workspace_volume(
    backend: Backend, seed_id: str | None
) -> None:
    """Remove the warm per-seed workspace volume (`gitseed-ws-<seed_id>`) once a seed is merged or closed."""
    if not seed_id or backend.provider.lower() in ("kubernetes", "k8s"):
        return
    vol_name = f"gitseed-ws-{seed_id.lower()}"
    try:
        client, base_url = _docker_client_for_backend(backend)
        async with client:
            await client.delete(
                f"{base_url}/volumes/{quote(vol_name, safe='')}?force=true"
            )
    except Exception as exc:
        logger.debug("Workspace volume cleanup for %s ignored error: %s", vol_name, exc)


def session_workspace_volume_name(session_id: str) -> str:
    """Canonical Docker volume name for an interactive session's `/workspace`."""
    return f"gitseed-ws-{session_id.lower()}"


async def ensure_docker_session_workspace_volume(
    client: httpx.AsyncClient,
    base_url: str,
    session_id: str,
    project_id: str,
) -> tuple[str, bool]:
    """Ensure the per-session `/workspace` Docker volume exists.

    Returns `(volume_name, already_existed)`. When `already_existed` is True,
    the volume already holds the session's `/workspace` files from an earlier
    container lifecycle.
    """
    vol_name = session_workspace_volume_name(session_id)
    inspect_resp = await client.get(f"{base_url}/volumes/{quote(vol_name, safe='')}")
    if inspect_resp.status_code == 200:
        return vol_name, True

    await client.post(
        f"{base_url}/volumes/create",
        json={
            "Name": vol_name,
            "Labels": {
                "app.gitseed.managed": "true",
                "app.gitseed.session_id": session_id,
                "app.gitseed.project_id": project_id,
            },
        },
    )
    return vol_name, False


async def remove_docker_session_workspace_volume(
    backend: Backend, session_id: str | None
) -> None:
    """Remove the per-session `/workspace` volume (`gitseed-ws-<session_id>`) when a session is deleted."""
    if not session_id or backend.provider.lower() in ("kubernetes", "k8s"):
        return
    vol_name = session_workspace_volume_name(session_id)
    try:
        client, base_url = _docker_client_for_backend(backend)
        async with client:
            await client.delete(
                f"{base_url}/volumes/{quote(vol_name, safe='')}?force=true"
            )
    except Exception as exc:
        logger.debug("Session volume cleanup for %s ignored error: %s", vol_name, exc)


def _demux_docker_logs(raw: bytes) -> str:
    """Decode Docker container log stream (stripping 8-byte multiplexed frame headers if present)."""
    if not raw:
        return ""
    parts: list[str] = []
    idx = 0
    n = len(raw)
    while (
        idx + 8 <= n
        and raw[idx] in (0, 1, 2)
        and raw[idx + 1 : idx + 4] == b"\x00\x00\x00"
    ):
        frame_size = int.from_bytes(raw[idx + 4 : idx + 8], "big")
        frame_end = idx + 8 + frame_size
        if frame_end > n:
            break
        parts.append(raw[idx + 8 : frame_end].decode("utf-8", errors="replace"))
        idx = frame_end
    if parts and idx == n:
        text_out = "".join(parts)
    else:
        text_out = raw.decode("utf-8", errors="replace")
    filtered_lines = [
        ln
        for ln in text_out.splitlines()
        if "/api/v1/runner/steering" not in ln
    ]
    return "\n".join(filtered_lines).strip()[-50000:]


async def fetch_docker_container_logs(
    backend: Backend, container_id: str | None, *, tail_lines: int = 800
) -> str:
    """Fetch stdout+stderr log tail from a Docker container or Kubernetes Job/Pod before removal."""
    if not container_id:
        return ""
    try:
        if backend.provider.lower() in ("kubernetes", "k8s"):
            client, base_url, default_ns = _k8s_client_for_backend(backend)
            target_ns, name = _split_k8s_ref(default_ns, container_id)
            async with client:
                pod_name = name
                is_job_pod = _is_k8s_job(name)
                has_finalize = False
                if is_job_pod:
                    pods_resp = await client.get(
                        f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods"
                        f"?labelSelector=job-name%3D{quote(name, safe='')}"
                    )
                    pod_name = ""
                    if pods_resp.status_code == 200:
                        items = (pods_resp.json() or {}).get("items") or []
                        if items and isinstance(items[0], dict):
                            pod_obj = items[0]
                            pod_name = str(
                                (pod_obj.get("metadata") or {}).get("name") or ""
                            ).strip()
                            spec_containers = (pod_obj.get("spec") or {}).get("containers") or []
                            status_containers = (
                                (pod_obj.get("status") or {}).get("containerStatuses") or []
                            )
                            all_containers = [*spec_containers, *status_containers]
                            has_finalize = not all_containers or any(
                                isinstance(c, dict) and c.get("name") == "finalize"
                                for c in all_containers
                            )
                if pod_name:
                    pod_log_base = (
                        f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}"
                        f"/pods/{quote(pod_name, safe='')}/log"
                    )
                    if is_job_pod:
                        log_resp = await client.get(
                            f"{pod_log_base}?container=agent&tailLines={tail_lines}"
                        )
                        if log_resp.status_code == 200:
                            combined_logs = log_resp.text
                            if has_finalize:
                                fin_resp = await client.get(
                                    f"{pod_log_base}?container=finalize&tailLines={min(tail_lines, 200)}"
                                )
                                fin_txt = fin_resp.text.strip() if fin_resp.status_code == 200 else ""
                                if fin_txt and fin_txt != combined_logs.strip():
                                    combined_logs = (
                                        f"{combined_logs.rstrip()}\n[finalize]\n{fin_txt}"
                                    )
                            return _demux_docker_logs(combined_logs.encode("utf-8"))
                    fallback_resp = await client.get(
                        f"{pod_log_base}?tailLines={tail_lines}"
                    )
                    if fallback_resp.status_code == 200:
                        return _demux_docker_logs(fallback_resp.text.encode("utf-8"))
                    if not is_job_pod:
                        agent_resp = await client.get(
                            f"{pod_log_base}?container=agent&tailLines={tail_lines}"
                        )
                        if agent_resp.status_code == 200:
                            return _demux_docker_logs(agent_resp.text.encode("utf-8"))
            return ""

        client, base_url = _docker_client_for_backend(backend)
        async with client:
            resp = await client.get(
                f"{base_url}/containers/{quote(container_id, safe='')}/logs"
                f"?stdout=true&stderr=true&tail={tail_lines}"
            )
            if resp.status_code == 200:
                return _demux_docker_logs(resp.content)
    except Exception as exc:
        logger.debug("Fetching logs for container %s ignored error: %s", container_id, exc)
    return ""


def _attach_run_log_tail(run: Run, log_tail: str | None) -> None:
    """Persist or enrich `run.output['log_tail']` with container stdout/stderr logs."""
    cleaned = (log_tail or "").strip()
    if not cleaned:
        return
    existing = dict(run.output or {})
    prev_logs = str(existing.get("log_tail") or "").strip()
    if len(cleaned) >= len(prev_logs):
        existing["log_tail"] = cleaned[-50000:]
    elif cleaned not in prev_logs:
        existing["log_tail"] = f"{prev_logs}\n{cleaned}"[-50000:]
    run.output = existing


def _parse_container_timestamp(raw_ts: Any) -> datetime | None:
    """Parse an ISO-8601 Docker/Kubernetes timestamp, ignoring zero-time sentinel values."""
    ts = str(raw_ts or "").strip()
    if not ts or ts.startswith("0001-01-01"):
        return None
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts)
    try:
        parsed = datetime.fromisoformat(ts)
        if parsed.year <= 1:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def _detect_k8s_crashed_agent_hang(
    pod_status: dict[str, Any], now: datetime
) -> tuple[int, bool, str] | None:
    """Return `(exit_code, oom_killed, error_msg)` when `agent` crashed/OOMKilled >20s ago while `finalize` still waits."""
    c_statuses = pod_status.get("containerStatuses") or []
    agent_cs: dict[str, Any] | None = None
    fin_cs: dict[str, Any] | None = None
    for cs in c_statuses:
        if not isinstance(cs, dict):
            continue
        c_name = str(cs.get("name") or "")
        if c_name == "agent":
            agent_cs = cs
        elif c_name == "finalize":
            fin_cs = cs
    if not agent_cs:
        return None
    agent_term = (agent_cs.get("state") or {}).get("terminated")
    if not isinstance(agent_term, dict):
        return None
    agent_exit = int(agent_term.get("exitCode") or 0)
    agent_reason = str(agent_term.get("reason") or "")
    oom_killed = agent_reason == "OOMKilled" or agent_exit == 137
    if not oom_killed and agent_exit == 0:
        return None
    fin_running = isinstance(fin_cs, dict) and isinstance(
        (fin_cs.get("state") or {}).get("running"), dict
    )
    if not fin_running:
        return None
    finished_at = _parse_container_timestamp(agent_term.get("finishedAt"))
    if finished_at is None or (now - finished_at).total_seconds() <= 20.0:
        return None
    effective_exit = agent_exit or 137
    err_msg = (
        "OOMKilled"
        if oom_killed
        else f"Agent container exited with code {effective_exit}"
    )
    return effective_exit, oom_killed, err_msg


async def _inspect_docker_container(
    backend: Backend, container_id: str
) -> dict[str, Any] | None:
    try:
        if backend.provider.lower() in ("kubernetes", "k8s"):
            client, base_url, default_ns = _k8s_client_for_backend(backend)
            target_ns, name = _split_k8s_ref(default_ns, container_id)
            now = datetime.now(UTC)
            async with client:
                if _is_k8s_job(name):
                    resp = await client.get(
                        f"{base_url}/apis/batch/v1/namespaces/{quote(target_ns, safe='')}/jobs/{quote(name, safe='')}"
                    )
                    if resp.status_code == 404:
                        return {"_missing": True}
                    if resp.status_code == 200:
                        payload = resp.json() or {}
                        job_status = payload.get("status") or {}
                        active = int(job_status.get("active") or 0)
                        failed = int(job_status.get("failed") or 0)
                        succeeded = int(job_status.get("succeeded") or 0)
                        running = active > 0 or (failed == 0 and succeeded == 0)
                        exit_code = 0 if succeeded > 0 else (1 if failed > 0 else 0)
                        oom_killed = False
                        err_str = "Kubernetes Job failed" if failed > 0 else ""

                        pods_resp = await client.get(
                            f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods"
                            f"?labelSelector=job-name%3D{quote(name, safe='')}"
                        )
                        if pods_resp.status_code == 200:
                            items = (pods_resp.json() or {}).get("items") or []
                            if items and isinstance(items[0], dict):
                                p_status = items[0].get("status") or {}
                                if running:
                                    crashed = _detect_k8s_crashed_agent_hang(p_status, now)
                                    if crashed is not None:
                                        exit_code, oom_killed, err_str = crashed
                                        running = False
                                        await client.delete(
                                            f"{base_url}/apis/batch/v1/namespaces/{quote(target_ns, safe='')}/jobs/{quote(name, safe='')}?propagationPolicy=Background"
                                        )
                                else:
                                    for cs in p_status.get("containerStatuses") or []:
                                        if not isinstance(cs, dict):
                                            continue
                                        term = (cs.get("state") or {}).get("terminated")
                                        if isinstance(term, dict):
                                            c_exit = int(term.get("exitCode") or 0)
                                            c_reason = str(term.get("reason") or "")
                                            if c_reason == "OOMKilled" or c_exit == 137:
                                                oom_killed = True
                                            if c_exit != 0 and cs.get("name") == "agent":
                                                exit_code = c_exit

                        return {
                            "Config": {"Labels": (payload.get("metadata") or {}).get("labels") or {}},
                            "State": {
                                "Running": running,
                                "ExitCode": exit_code,
                                "OOMKilled": oom_killed,
                                "Error": err_str,
                            },
                        }
                    return None

                resp = await client.get(
                    f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods/{quote(name, safe='')}"
                )
                if resp.status_code == 404:
                    return {"_missing": True}
                if resp.status_code == 200:
                    payload = resp.json() or {}
                    pod_status = payload.get("status") or {}
                    phase = str(pod_status.get("phase") or "Pending")
                    running = phase in ("Pending", "Running")
                    exit_code = 0 if phase == "Succeeded" else (1 if phase == "Failed" else 0)
                    oom_killed = False
                    err_str = "Kubernetes Pod failed" if phase == "Failed" else ""

                    if running:
                        crashed = _detect_k8s_crashed_agent_hang(pod_status, now)
                        if crashed is not None:
                            exit_code, oom_killed, err_str = crashed
                            running = False
                            await client.delete(
                                f"{base_url}/api/v1/namespaces/{quote(target_ns, safe='')}/pods/{quote(name, safe='')}?gracePeriodSeconds=1"
                            )

                    c_statuses = pod_status.get("containerStatuses") or []
                    if c_statuses and all(
                        isinstance(cs, dict)
                        and isinstance((cs.get("state") or {}).get("terminated"), dict)
                        for cs in c_statuses
                    ):
                        running = False
                        for cs in c_statuses:
                            term = ((cs.get("state") or {}).get("terminated")) or {}
                            code = int(term.get("exitCode") or 0)
                            if str(term.get("reason") or "") == "OOMKilled" or code == 137:
                                oom_killed = True
                            if code != 0:
                                exit_code = code
                                break
                    return {
                        "Config": {"Labels": (payload.get("metadata") or {}).get("labels") or {}},
                        "State": {
                            "Running": running,
                            "ExitCode": exit_code,
                            "OOMKilled": oom_killed,
                            "Error": err_str,
                        },
                    }
            return None

        client, base_url = _docker_client_for_backend(backend)
        async with client:
            resp = await client.get(
                f"{base_url}/containers/{quote(container_id, safe='')}/json"
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    state = data.get("State") or {}
                    labels = (data.get("Config") or {}).get("Labels") or {}
                    run_id = str(labels.get("app.gitseed.run_id") or "").strip()
                    role = str(labels.get("app.gitseed.role") or "").strip()
                    if (
                        run_id
                        and role in ("", "agent")
                        and not state.get("Running")
                    ):
                        fin_name = f"gitseed-run-{run_id.lower()}-finalize"
                        if fin_name != container_id:
                            fin_resp = await client.get(
                                f"{base_url}/containers/{quote(fin_name, safe='')}/json"
                            )
                            if fin_resp.status_code == 200:
                                fin_data = fin_resp.json()
                                if isinstance(fin_data, dict):
                                    fin_state = fin_data.get("State")
                                    agent_exit = int(state.get("ExitCode") or 0)
                                    agent_oom = bool(state.get("OOMKilled")) or agent_exit == 137
                                    agent_finished = _parse_container_timestamp(
                                        state.get("FinishedAt")
                                    )
                                    if isinstance(fin_state, dict) and fin_state.get("Running"):
                                        if (
                                            (agent_oom or agent_exit != 0)
                                            and agent_finished is not None
                                            and (datetime.now(UTC) - agent_finished).total_seconds()
                                            > 20.0
                                        ):
                                            await client.post(
                                                f"{base_url}/containers/{quote(fin_name, safe='')}/stop?t=2"
                                            )
                                            await client.delete(
                                                f"{base_url}/containers/{quote(fin_name, safe='')}?force=true"
                                            )
                                            effective_exit = agent_exit or 137
                                            return {
                                                **data,
                                                "State": {
                                                    **state,
                                                    "Running": False,
                                                    "ExitCode": effective_exit,
                                                    "OOMKilled": agent_oom,
                                                    "Error": str(
                                                        state.get("Error")
                                                        or (
                                                            "OOMKilled"
                                                            if agent_oom
                                                            else f"Agent container exited with code {effective_exit}"
                                                        )
                                                    ),
                                                },
                                            }
                                    elif isinstance(fin_state, dict):
                                        if agent_oom:
                                            fin_state["OOMKilled"] = True
                                        if int(fin_state.get("ExitCode") or 0) == 0 and (
                                            agent_oom or agent_exit != 0
                                        ):
                                            fin_state["ExitCode"] = agent_exit or 137
                                    return fin_data
                    return data
            if resp.status_code == 404:
                return {"_missing": True}
    except Exception:
        pass
    return None


async def inspect_docker_container_state(
    backend: Backend, container_id: str
) -> dict[str, Any]:
    """Return normalized container state dict: `{'exists': bool, 'running': bool, 'exit_code': int, 'oom_killed': bool}`."""
    info = await _inspect_docker_container(backend, container_id)
    if not info or info.get("_missing"):
        return {"exists": False, "running": False, "exit_code": -1, "oom_killed": False}
    state = info.get("State") or {}
    return {
        "exists": True,
        "running": bool(state.get("Running")),
        "exit_code": int(state.get("ExitCode") or 0),
        "oom_killed": bool(state.get("OOMKilled")),
    }


inspect_run_execution = inspect_docker_container_state
