from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, Request
from gitseed_common.schemas.project import WebhookDeliveryResponse

from gitseed_core.api.deps import DBSession
from gitseed_core.services import webhooks as webhooks_service
from gitseed_core.services.errors import Invalid

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

MAX_WEBHOOK_BODY_BYTES = 10 * 1024 * 1024


async def _read_bounded_webhook_body(request: Request) -> tuple[bytes, dict[str, Any]]:
    """Read inbound webhook body with a strict byte ceiling before UTF-8 / JSON decoding."""
    raw_cl = request.headers.get("content-length")
    if raw_cl:
        try:
            if int(raw_cl.strip()) > MAX_WEBHOOK_BODY_BYTES:
                raise Invalid(
                    f"Webhook body exceeds maximum allowed size ({MAX_WEBHOOK_BODY_BYTES} bytes)"
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_WEBHOOK_BODY_BYTES:
            raise Invalid(
                f"Webhook body exceeds maximum allowed size ({MAX_WEBHOOK_BODY_BYTES} bytes)"
            )
        chunks.append(chunk)
    raw_body = b"".join(chunks)

    try:
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Invalid("Webhook body must be valid UTF-8 JSON") from exc

    try:
        parsed = json.loads(decoded or "{}")
    except json.JSONDecodeError as exc:
        raise Invalid("Webhook body must be valid JSON") from exc

    return raw_body, parsed if isinstance(parsed, dict) else {}


@router.post(
    "/github",
    response_model=WebhookDeliveryResponse,
)
async def receive_github_webhook_root(
    request: Request,
    session: DBSession,
    project_id: str | None = None,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> WebhookDeliveryResponse:
    """Receive and verify an inbound GitHub webhook event (resolved by project_id or repository.full_name)."""
    raw_body, payload = await _read_bounded_webhook_body(request)

    return await webhooks_service.handle_github_webhook(
        session,
        project_id,
        event_name=x_github_event,
        raw_body=raw_body,
        signature_header=x_hub_signature_256,
        payload=payload,
        delivery_id=x_github_delivery,
    )


@router.post(
    "/github/{project_id}",
    response_model=WebhookDeliveryResponse,
)
async def receive_github_webhook(
    project_id: str,
    request: Request,
    session: DBSession,
    x_github_event: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
) -> WebhookDeliveryResponse:
    """Receive and verify an inbound GitHub webhook event for a project."""
    raw_body, payload = await _read_bounded_webhook_body(request)

    return await webhooks_service.handle_github_webhook(
        session,
        project_id,
        event_name=x_github_event,
        raw_body=raw_body,
        signature_header=x_hub_signature_256,
        payload=payload,
        delivery_id=x_github_delivery,
    )


@router.post(
    "/gitlab",
    response_model=WebhookDeliveryResponse,
)
async def receive_gitlab_webhook_root(
    request: Request,
    session: DBSession,
    project_id: str | None = None,
    x_gitlab_event: str = Header(default=""),
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event_uuid: str | None = Header(default=None),
) -> WebhookDeliveryResponse:
    """Receive and verify an inbound GitLab webhook event (resolved by project_id or project.path_with_namespace)."""
    _, payload = await _read_bounded_webhook_body(request)

    return await webhooks_service.handle_gitlab_webhook(
        session,
        project_id,
        event_name=x_gitlab_event,
        token_header=x_gitlab_token,
        payload=payload,
        delivery_id=x_gitlab_event_uuid,
    )


@router.post(
    "/gitlab/{project_id}",
    response_model=WebhookDeliveryResponse,
)
async def receive_gitlab_webhook(
    project_id: str,
    request: Request,
    session: DBSession,
    x_gitlab_event: str = Header(default=""),
    x_gitlab_token: str | None = Header(default=None),
    x_gitlab_event_uuid: str | None = Header(default=None),
) -> WebhookDeliveryResponse:
    """Receive and verify an inbound GitLab webhook event for a project."""
    _, payload = await _read_bounded_webhook_body(request)

    return await webhooks_service.handle_gitlab_webhook(
        session,
        project_id,
        event_name=x_gitlab_event,
        token_header=x_gitlab_token,
        payload=payload,
        delivery_id=x_gitlab_event_uuid,
    )


@router.post(
    "/bitbucket",
    response_model=WebhookDeliveryResponse,
)
async def receive_bitbucket_webhook_root(
    request: Request,
    session: DBSession,
    project_id: str | None = None,
    x_event_key: str = Header(default=""),
    x_hub_signature: str | None = Header(default=None),
    x_request_uuid: str | None = Header(default=None),
) -> WebhookDeliveryResponse:
    """Receive and verify an inbound Bitbucket Cloud webhook event (resolved by project_id or repository.full_name)."""
    raw_body, payload = await _read_bounded_webhook_body(request)

    return await webhooks_service.handle_bitbucket_webhook(
        session,
        project_id,
        event_name=x_event_key,
        raw_body=raw_body,
        signature_header=x_hub_signature,
        payload=payload,
        delivery_id=x_request_uuid,
    )


@router.post(
    "/bitbucket/{project_id}",
    response_model=WebhookDeliveryResponse,
)
async def receive_bitbucket_webhook(
    project_id: str,
    request: Request,
    session: DBSession,
    x_event_key: str = Header(default=""),
    x_hub_signature: str | None = Header(default=None),
    x_request_uuid: str | None = Header(default=None),
) -> WebhookDeliveryResponse:
    """Receive and verify an inbound Bitbucket Cloud webhook event for a project."""
    raw_body, payload = await _read_bounded_webhook_body(request)

    return await webhooks_service.handle_bitbucket_webhook(
        session,
        project_id,
        event_name=x_event_key,
        raw_body=raw_body,
        signature_header=x_hub_signature,
        payload=payload,
        delivery_id=x_request_uuid,
    )

