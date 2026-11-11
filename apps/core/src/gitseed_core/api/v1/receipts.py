from __future__ import annotations

from fastapi import APIRouter
from gitseed_common.schemas.receipt import (
    GovernanceReceiptEnvelopeResponse,
    ReceiptPublicKeyResponse,
)

from gitseed_core.api.deps import CurrentUser, DBSession
from gitseed_core.services import seeds as seeds_service
from gitseed_core.services.git_pr_providers import format_governance_receipt_comment
from gitseed_core.services.receipts import (
    build_seed_governance_receipt,
    get_receipt_signing_public_key_info,
)

router = APIRouter(tags=["Governance Receipts"])


@router.get("/receipts/public-key", response_model=ReceiptPublicKeyResponse)
async def get_receipt_public_key() -> ReceiptPublicKeyResponse:
    """Return the Ed25519 public key and key ID used to sign GitSeed governance receipts."""
    info = get_receipt_signing_public_key_info()
    return ReceiptPublicKeyResponse.model_validate(info)


@router.get(
    "/seeds/{seed_id}/receipt",
    response_model=GovernanceReceiptEnvelopeResponse,
)
async def get_seed_governance_receipt(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> GovernanceReceiptEnvelopeResponse:
    """Build and sign a canonical governance receipt for a Seed."""
    seed = await seeds_service.get_seed(session, current_user, seed_id)
    envelope = await build_seed_governance_receipt(session, seed.id)
    markdown = format_governance_receipt_comment(envelope)
    return GovernanceReceiptEnvelopeResponse.model_validate(
        {
            **envelope,
            "markdown": markdown,
        }
    )
