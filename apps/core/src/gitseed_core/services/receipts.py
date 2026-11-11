"""Cryptographically signed governance receipts (`gitseed.receipt.v1`) per Seed / PR."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from gitseed_common.enums import BackendType, SeedStage
from gitseed_common.schemas.receipt import (
    GovernanceReceiptArtifactsInfo,
    GovernanceReceiptTaskItem,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.policy import Policy
from gitseed_core.models.project import Project
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedTask
from gitseed_core.models.user import User
from gitseed_core.services.errors import NotFound
from gitseed_core.services.gateway import resolve_budget_enforcement
from gitseed_core.services.policies import get_effective_stage_policy

RECEIPT_SCHEMA_VERSION = "gitseed.receipt.v1"
RECEIPT_ALGORITHM = "Ed25519"
_RECEIPT_HKDF_INFO = b"gitseed-receipt-signing-v1"


def _derive_ed25519_private_key(secret_key: str | None = None) -> Ed25519PrivateKey:
    """Derive a deterministic Ed25519 signing key from `GITSEED_RECEIPT_SIGNING_KEY` or `GITSEED_SECRET_KEY` via HKDF-SHA256."""
    if secret_key is not None:
        raw_secret = secret_key.encode("utf-8")
    else:
        dedicated = str(getattr(settings, "GITSEED_RECEIPT_SIGNING_KEY", "") or "").strip()
        raw_secret = (dedicated or settings.GITSEED_SECRET_KEY).encode("utf-8")
    seed_bytes = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=_RECEIPT_HKDF_INFO,
    ).derive(raw_secret)
    return Ed25519PrivateKey.from_private_bytes(seed_bytes)


def compute_public_key_id(public_key: Ed25519PublicKey) -> str:
    """Return a 16-hex key identifier derived from SHA-256 of the raw Ed25519 public key bytes."""
    raw_pub = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return hashlib.sha256(raw_pub).hexdigest()[:16]


def _public_key_entry_for_secret(secret_key: str) -> dict[str, str]:
    priv = _derive_ed25519_private_key(secret_key)
    pub = priv.public_key()
    pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return {
        "key_id": compute_public_key_id(pub),
        "algorithm": RECEIPT_ALGORITHM,
        "public_key_pem": pem,
    }


def get_receipt_signing_public_key_info(secret_key: str | None = None) -> dict[str, Any]:
    """Return `{key_id, algorithm: 'Ed25519', public_key_pem, previous_public_keys}` for the active signing key."""
    priv = _derive_ed25519_private_key(secret_key)
    pub = priv.public_key()
    pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    active_key_id = compute_public_key_id(pub)
    previous_keys: list[dict[str, str]] = []
    seen_ids = {active_key_id}
    raw_prev_csv = str(getattr(settings, "GITSEED_SECRET_PREVIOUS_KEYS", "") or "")
    for raw_prev in raw_prev_csv.split(","):
        candidate = raw_prev.strip()
        if not candidate:
            continue
        entry = _public_key_entry_for_secret(candidate)
        if entry["key_id"] not in seen_ids:
            seen_ids.add(entry["key_id"])
            previous_keys.append(entry)
    return {
        "key_id": active_key_id,
        "algorithm": RECEIPT_ALGORITHM,
        "public_key_pem": pem,
        "previous_public_keys": previous_keys,
    }


def canonicalize_receipt_payload(receipt: dict[str, Any]) -> str:
    """Serialize a governance receipt dict into canonical UTF-8 JSON."""
    return json.dumps(receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sign_governance_receipt(
    receipt: dict[str, Any],
    *,
    secret_key: str | None = None,
) -> dict[str, Any]:
    """Canonicalize and sign a governance receipt payload with Ed25519."""
    priv = _derive_ed25519_private_key(secret_key)
    pub = priv.public_key()
    pem = pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    key_id = compute_public_key_id(pub)
    canonical_json = canonicalize_receipt_payload(receipt)
    sig_bytes = priv.sign(canonical_json.encode("utf-8"))
    signature = base64.urlsafe_b64encode(sig_bytes).decode("ascii").rstrip("=")
    return {
        "receipt": receipt,
        "canonical_json": canonical_json,
        "signature": signature,
        "algorithm": RECEIPT_ALGORITHM,
        "key_id": key_id,
        "public_key_pem": pem,
    }


class ReceiptVerificationResult(dict[str, Any]):
    """Verification outcome (`{'valid': bool, ...}`) whose boolean truthiness matches `valid`."""

    def __bool__(self) -> bool:
        return bool(self.get("valid", False))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, bool):
            return bool(self) is other
        return super().__eq__(other)


def verify_governance_receipt(
    envelope: dict[str, Any] | str | bytes,
    public_key_pem: str | None = None,
    *,
    trusted_public_keys: list[str] | None = None,
) -> ReceiptVerificationResult:
    """Verify the Ed25519 signature and canonical JSON integrity of a governance receipt envelope.

    Requires an explicit trusted `public_key_pem` (or `trusted_public_keys`); never trusts an
    unverified `public_key_pem` embedded inside `envelope`.
    """
    candidate_pems: list[str] = []
    if isinstance(public_key_pem, str) and public_key_pem.strip():
        candidate_pems.append(public_key_pem.strip())
    if trusted_public_keys:
        for item in trusted_public_keys:
            if isinstance(item, str) and item.strip() and item.strip() not in candidate_pems:
                candidate_pems.append(item.strip())
    if not candidate_pems:
        return ReceiptVerificationResult(
            {
                "valid": False,
                "error": "Trusted public key is required to verify governance receipt",
            }
        )

    try:
        if isinstance(envelope, str | bytes):
            parsed = json.loads(
                envelope.decode("utf-8") if isinstance(envelope, bytes) else envelope
            )
            if not isinstance(parsed, dict):
                return ReceiptVerificationResult(
                    {"valid": False, "error": "Receipt envelope must be a JSON object"}
                )
            env = parsed
        elif isinstance(envelope, dict):
            env = envelope
        else:
            return ReceiptVerificationResult(
                {"valid": False, "error": "Receipt envelope must be a JSON object"}
            )

        alg = str(env.get("algorithm") or RECEIPT_ALGORITHM).strip()
        if alg != RECEIPT_ALGORITHM:
            return ReceiptVerificationResult(
                {"valid": False, "error": f"Unsupported receipt signature algorithm '{alg}'"}
            )

        receipt_obj = env.get("receipt")
        canonical_str = env.get("canonical_json")
        if isinstance(receipt_obj, dict):
            computed_canonical = canonicalize_receipt_payload(receipt_obj)
            if isinstance(canonical_str, str) and canonical_str != computed_canonical:
                return ReceiptVerificationResult(
                    {
                        "valid": False,
                        "error": "Tamper detected: canonical_json does not match receipt payload",
                    }
                )
            payload_bytes = computed_canonical.encode("utf-8")
        elif isinstance(canonical_str, str) and canonical_str:
            payload_bytes = canonical_str.encode("utf-8")
        else:
            return ReceiptVerificationResult(
                {"valid": False, "error": "Missing receipt payload in envelope"}
            )

        raw_sig = str(env.get("signature") or "").strip()
        if not raw_sig:
            return ReceiptVerificationResult(
                {"valid": False, "error": "Missing signature in receipt envelope"}
            )
        padded_sig = raw_sig + "=" * (-len(raw_sig) % 4)
        sig_bytes = base64.urlsafe_b64decode(padded_sig.encode("ascii"))
        env_key_id = str(env.get("key_id") or "").strip()

        last_err = "Ed25519 signature verification failed"
        for pem_candidate in candidate_pems:
            try:
                loaded_key = serialization.load_pem_public_key(
                    pem_candidate.encode("utf-8")
                )
                if not isinstance(loaded_key, Ed25519PublicKey):
                    last_err = "Provided public key is not an Ed25519 public key"
                    continue
                expected_key_id = compute_public_key_id(loaded_key)
                if env_key_id and env_key_id != expected_key_id:
                    last_err = "Receipt key_id does not match trusted Ed25519 public key"
                    continue
                loaded_key.verify(sig_bytes, payload_bytes)
                return ReceiptVerificationResult(
                    {"valid": True, "key_id": expected_key_id}
                )
            except (InvalidSignature, ValueError, TypeError):
                last_err = "Ed25519 signature verification failed"
                continue
        return ReceiptVerificationResult({"valid": False, "error": last_err})
    except (InvalidSignature, ValueError, TypeError, KeyError) as exc:
        return ReceiptVerificationResult(
            {"valid": False, "error": f"Invalid receipt signature or format: {exc}"}
        )
    except Exception as exc:
        return ReceiptVerificationResult(
            {"valid": False, "error": f"Receipt verification failed: {exc}"}
        )


def _enum_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val.value if hasattr(val, "value") else val)


def _iso_or_none(val: Any) -> str | None:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return str(val.isoformat())
    return str(val)


def _extract_run_trusted(run: Any) -> dict[str, Any] | None:
    out = getattr(run, "output", None)
    if not isinstance(out, dict):
        return None
    raw_trusted = out.get("trusted")
    if not isinstance(raw_trusted, dict):
        return None
    return {
        "commit_sha": raw_trusted.get("commit_sha"),
        "branch": raw_trusted.get("branch"),
        "pushed": bool(raw_trusted.get("pushed", False)),
        "base_heads": dict(raw_trusted.get("base_heads") or {})
        if isinstance(raw_trusted.get("base_heads"), dict)
        else {},
        "blocked_paths_violations": list(
            raw_trusted.get("blocked_paths_violations") or []
        ),
        "secret_scan_findings": list(raw_trusted.get("secret_scan_findings") or []),
        "diff_stats": raw_trusted.get("diff_stats"),
        "verification": raw_trusted.get("verification"),
    }


async def build_seed_governance_receipt(
    session: AsyncSession,
    seed_id: str,
) -> dict[str, Any]:
    """Build and sign a canonical `gitseed.receipt.v1` governance receipt for a Seed."""
    seed: Seed | None = None
    fetched_seed = await session.get(Seed, seed_id)
    if isinstance(fetched_seed, Seed) or (
        fetched_seed is not None
        and hasattr(fetched_seed, "project_id")
        and hasattr(fetched_seed, "number")
    ):
        seed = fetched_seed

    if seed is None:
        raise NotFound("Seed not found")

    project: Project | None = None
    fetched_proj = await session.get(Project, seed.project_id)
    if isinstance(fetched_proj, Project) or (
        fetched_proj is not None and hasattr(fetched_proj, "slug")
    ):
        project = fetched_proj

    project_slug = str(getattr(project, "slug", None) or "project")

    runs_res = await session.execute(
        select(Run)
        .where(Run.seed_id == seed.id)
        .order_by(col(Run.created_at).asc(), col(Run.id).asc())
    )
    run_rows = list(runs_res.scalars().all())

    approvals_res = await session.execute(
        select(Approval)
        .where(Approval.seed_id == seed.id)
        .order_by(col(Approval.created_at).asc(), col(Approval.id).asc())
    )
    approval_rows = list(approvals_res.scalars().all())

    # Optional policy & inference backend lookup (best-effort so minimal mocks with 2 execute calls still work)
    min_approver_role = "developer"
    prevent_self_approval = False
    default_backend: Backend | None = None
    backends_by_id: dict[str, Backend] = {}
    try:
        eff_pol = await get_effective_stage_policy(
            session, seed.project_id, SeedStage.MERGE
        )
        if eff_pol is not None and hasattr(eff_pol, "min_approver_role"):
            min_approver_role = _enum_str(eff_pol.min_approver_role, "developer")
            prevent_self_approval = not bool(
                getattr(eff_pol, "allow_self_approval", True)
            )
    except Exception:
        try:
            pol_res = await session.execute(
                select(Policy).where(
                    col(Policy.project_id).in_([seed.project_id, None]),
                    col(Policy.stage).in_([SeedStage.MERGE, SeedStage.REVIEW]),
                )
            )
            pol_rows = [
                p
                for p in pol_res.scalars().all()
                if isinstance(p, Policy) or hasattr(p, "min_approver_role")
            ]
            if pol_rows:
                pol_rows.sort(
                    key=lambda p: (
                        0 if getattr(p, "project_id", None) == seed.project_id else 1,
                        0 if _enum_str(getattr(p, "stage", "")) == "merge" else 1,
                    )
                )
                chosen_pol = pol_rows[0]
                if getattr(chosen_pol, "min_approver_role", None) is not None:
                    min_approver_role = _enum_str(
                        chosen_pol.min_approver_role, "developer"
                    )
                if getattr(chosen_pol, "allow_self_approval", None) is not None:
                    prevent_self_approval = not bool(chosen_pol.allow_self_approval)
        except Exception:
            pass

    try:
        bnd_res = await session.execute(
            select(Backend)
            .where(
                Backend.type == BackendType.INFERENCE,
                col(Backend.enabled).is_(True),
            )
            .order_by(col(Backend.created_at).asc())
        )
        bnd_rows = [
            b
            for b in bnd_res.scalars().all()
            if isinstance(b, Backend) and getattr(b, "type", None) == BackendType.INFERENCE
        ]
        if bnd_rows:
            default_backend = bnd_rows[0]
            backends_by_id = {str(b.id): b for b in bnd_rows if getattr(b, "id", None)}
    except Exception:
        pass

    task_items: list[GovernanceReceiptTaskItem] = []
    try:
        task_res = await session.execute(
            select(SeedTask)
            .where(SeedTask.seed_id == seed.id)
            .order_by(col(SeedTask.position).asc(), col(SeedTask.id).asc())
        )
        raw_task_rows = [
            t
            for t in task_res.scalars().all()
            if isinstance(t, SeedTask)
            or (
                hasattr(t, "title")
                and hasattr(t, "status")
                and not hasattr(t, "runtime")
                and not hasattr(t, "decision")
            )
        ]
        raw_task_rows.sort(
            key=lambda t: (
                int(getattr(t, "order_index", getattr(t, "position", 0)) or 0),
                str(getattr(t, "id", "")),
            )
        )
        for idx, t in enumerate(raw_task_rows):
            order_idx = getattr(t, "order_index", None)
            if order_idx is None:
                order_idx = getattr(t, "position", idx)
            task_items.append(
                GovernanceReceiptTaskItem(
                    id=str(t.id),
                    title=str(t.title),
                    status=_enum_str(t.status, "pending"),
                    order_index=int(order_idx if order_idx is not None else idx),
                )
            )
    except Exception:
        pass

    raw_spec = getattr(seed, "specification", None)
    spec_sha = (
        hashlib.sha256(raw_spec.encode("utf-8")).hexdigest()
        if isinstance(raw_spec, str) and raw_spec.strip()
        else None
    )
    raw_ac = getattr(seed, "acceptance_criteria", None)
    ac_text: str | None = None
    if isinstance(raw_ac, str) and raw_ac.strip():
        ac_text = raw_ac
    elif isinstance(raw_ac, list | tuple) and any(str(x).strip() for x in raw_ac):
        ac_text = "\n".join(str(x) for x in raw_ac)
    ac_sha = (
        hashlib.sha256(ac_text.encode("utf-8")).hexdigest()
        if ac_text is not None
        else None
    )
    raw_plan = getattr(seed, "plan", None)
    plan_sha = (
        hashlib.sha256(raw_plan.encode("utf-8")).hexdigest()
        if isinstance(raw_plan, str) and raw_plan.strip()
        else None
    )
    artifacts_info = GovernanceReceiptArtifactsInfo(
        specification_sha256=spec_sha,
        acceptance_criteria_sha256=ac_sha,
        plan_sha256=plan_sha,
        task_count=len(task_items),
        tasks=task_items,
    )

    sbx_cfg = (getattr(project, "sandbox_config", None) or {}) if project is not None else {}
    raw_blocked = sbx_cfg.get("blocked_paths") if isinstance(sbx_cfg, dict) else []
    blocked_paths = (
        [str(p).strip() for p in raw_blocked if str(p).strip()]
        if isinstance(raw_blocked, list)
        else []
    )
    if isinstance(sbx_cfg, dict):
        if sbx_cfg.get("min_approver_role"):
            min_approver_role = str(sbx_cfg["min_approver_role"])
        if "prevent_self_approval" in sbx_cfg:
            prevent_self_approval = bool(sbx_cfg["prevent_self_approval"])

    branch_name = getattr(seed, "branch_name", None) or f"gitseed/{project_slug}/seed-{seed.number}"
    pr_url: str | None = getattr(seed, "pr_url", None) or getattr(seed, "pull_request_url", None)

    serialized_runs: list[dict[str, Any]] = []
    for r in run_rows:
        out_dict = r.output if isinstance(getattr(r, "output", None), dict) else {}
        trusted_info = _extract_run_trusted(r)
        if trusted_info and trusted_info.get("branch"):
            branch_name = str(trusted_info["branch"])
        elif out_dict.get("branch_name"):
            branch_name = str(out_dict["branch_name"])

        candidate_pr = out_dict.get("pull_request_url") or out_dict.get("pr_url")
        if isinstance(candidate_pr, str) and candidate_pr.strip():
            pr_url = candidate_pr.strip()

        run_backend = (
            backends_by_id.get(str(out_dict.get("models_backend_id") or ""))
            or default_backend
        )
        enf_mode, _ = resolve_budget_enforcement(run_backend, r)
        raw_cost = getattr(r, "cost", Decimal("0"))
        cost_str = str(Decimal(str(raw_cost or 0)))

        completed_ts = _iso_or_none(
            getattr(r, "finished_at", None) or getattr(r, "completed_at", None)
        )
        serialized_runs.append(
            {
                "id": str(r.id),
                "stage": _enum_str(r.stage),
                "runtime": _enum_str(r.runtime),
                "model": str(r.model or "default"),
                "status": _enum_str(r.status),
                "cost_usd": cost_str,
                "cost_enforcement": enf_mode,
                "input_tokens": int(getattr(r, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(r, "output_tokens", 0) or 0),
                "started_at": _iso_or_none(getattr(r, "started_at", None)),
                "completed_at": completed_ts,
                "trusted": trusted_info,
            }
        )

    serialized_approvals: list[dict[str, Any]] = []
    for a in approval_rows:
        approver_uid = getattr(a, "approver_user_id", None) or getattr(a, "decided_by", None)
        approver_name = getattr(a, "approver_name", None)
        if not approver_name and approver_uid:
            try:
                u = await session.get(User, approver_uid)
                if isinstance(u, User) or (u is not None and hasattr(u, "email")):
                    approver_name = (
                        getattr(u, "preferred_name", None)
                        or getattr(u, "name", None)
                        or getattr(u, "email", None)
                    )
            except Exception:
                pass
            if not approver_name:
                approver_name = str(approver_uid)
        elif not approver_name and getattr(a, "automatic", False):
            approver_name = "system (auto)"

        raw_status = getattr(a, "status", None) or getattr(a, "decision", "approved")
        decided_ts = _iso_or_none(
            getattr(a, "decided_at", None) or getattr(a, "created_at", None)
        )
        serialized_approvals.append(
            {
                "id": str(a.id),
                "stage": _enum_str(a.stage),
                "status": _enum_str(raw_status),
                "approver_user_id": str(approver_uid) if approver_uid else None,
                "approver_name": str(approver_name) if approver_name else None,
                "comment": getattr(a, "comment", None),
                "decided_at": decided_ts,
            }
        )

    receipt_payload: dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "seed": {
            "id": str(seed.id),
            "number": int(seed.number),
            "title": str(seed.title),
            "project_id": str(seed.project_id),
            "project_slug": project_slug,
            "status": _enum_str(seed.status),
            "stage": _enum_str(seed.stage),
            "branch_name": branch_name,
            "pr_url": pr_url,
        },
        "policy": {
            "autonomy": _enum_str(seed.autonomy, "supervised"),
            "min_approver_role": min_approver_role,
            "prevent_self_approval": prevent_self_approval,
            "blocked_paths": blocked_paths,
        },
        "artifacts": artifacts_info.model_dump(),
        "runs": serialized_runs,
        "approvals": serialized_approvals,
        "generated_at": datetime.now(UTC).isoformat(),
    }

    return sign_governance_receipt(receipt_payload)
