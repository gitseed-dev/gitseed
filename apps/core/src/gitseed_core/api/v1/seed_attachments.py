from datetime import datetime
from urllib.parse import unquote

from fastapi import APIRouter, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from gitseed_core.api.deps import CurrentUser, DBSession
from gitseed_core.services import seed_attachments as attachments_service
from gitseed_core.services.errors import Invalid

router = APIRouter(prefix="/seeds", tags=["Seed attachments"])


class SeedAttachmentResponse(BaseModel):
    id: str
    seed_id: str
    filename: str
    content_type: str
    size: int
    sha256: str
    created_by: str | None = None
    created_at: datetime | None = None


@router.get("/{seed_id}/attachments", response_model=list[SeedAttachmentResponse])
async def list_seed_attachments(
    seed_id: str, current_user: CurrentUser, session: DBSession
) -> list[SeedAttachmentResponse]:
    """Files attached to a seed (reference material for its stage agents)."""
    rows = await attachments_service.list_attachments(session, current_user, seed_id)
    return [SeedAttachmentResponse(**attachments_service.serialize(a)) for a in rows]


@router.post(
    "/{seed_id}/attachments",
    response_model=SeedAttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_seed_attachment(
    seed_id: str,
    request: Request,
    current_user: CurrentUser,
    session: DBSession,
    filename: str,
) -> SeedAttachmentResponse:
    """Upload one file as the raw request body (`?filename=` names it; Content-Type is kept).

    Raw bytes rather than multipart keeps this dependency-free; the size limit is enforced
    while reading so oversized uploads are rejected early.
    """
    declared = int(request.headers.get("content-length") or 0)
    if declared > attachments_service.MAX_FILE_BYTES:
        raise Invalid(f"Files can be at most {attachments_service.MAX_FILE_BYTES // (1024 * 1024)} MB")
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > attachments_service.MAX_FILE_BYTES:
            raise Invalid(f"Files can be at most {attachments_service.MAX_FILE_BYTES // (1024 * 1024)} MB")
        chunks.append(chunk)
    att = await attachments_service.add_attachment(
        session,
        current_user,
        seed_id,
        filename=unquote(filename),
        content_type=request.headers.get("content-type"),
        data=b"".join(chunks),
    )
    return SeedAttachmentResponse(**attachments_service.serialize(att))


@router.get("/{seed_id}/attachments/{attachment_id}/download")
async def download_seed_attachment(
    seed_id: str, attachment_id: str, current_user: CurrentUser, session: DBSession
) -> FileResponse:
    att, path = await attachments_service.get_attachment(session, current_user, seed_id, attachment_id)
    return FileResponse(path=str(path), filename=att.filename, media_type=att.content_type)


@router.delete("/{seed_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_seed_attachment(
    seed_id: str, attachment_id: str, current_user: CurrentUser, session: DBSession
) -> None:
    await attachments_service.delete_attachment(session, current_user, seed_id, attachment_id)

