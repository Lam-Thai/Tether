"""`GET`/`HEAD /api/v1/media/{token}`: serves call audio to Twilio.

No auth: Twilio fetches the URL, and the unguessable short-lived token is the capability.
Every miss (unknown, malformed, expired) is the same 404, so nothing tells "never existed"
from "expired". No listing route; not in the OpenAPI schema.
"""

from fastapi import APIRouter, Depends, Request, Response, status

from core.errors import AppError
from core.media import get_media_store
from services.media import MediaStore

router = APIRouter(tags=["media"])


class MediaNotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "media_not_found"


@router.api_route("/media/{media_token}", methods=["GET", "HEAD"], include_in_schema=False)
async def serve_media(
    media_token: str, request: Request, store: MediaStore = Depends(get_media_store)
) -> Response:
    media = await store.get(media_token)
    if media is None:
        raise MediaNotFoundError("Media not found.")
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if request.method == "HEAD":
        headers["Content-Length"] = str(len(media.data))
        return Response(content=b"", media_type=media.content_type, headers=headers)
    return Response(content=media.data, media_type=media.content_type, headers=headers)
