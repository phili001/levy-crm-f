"""`GET /api/v1/media/p/{token}`: un archivo guardado, con enlace firmado y sin sesión.

Ver `services/media_publica.py`. Sin token bueno, 404 (no se dice si el archivo
existe). Se sirve con `nosniff` y el tipo que dice su extensión, para que un
navegador no lo interprete como otra cosa.
"""
import mimetypes

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse

from app.core.ratelimit import limit_spec, make_limiter
from app.services.media import resolve_local_path
from app.services.media_publica import verificar

router = APIRouter(prefix="/media", tags=["media"])
limiter = make_limiter()


@router.get("/p/{token}", include_in_schema=False)
@limiter.limit(limit_spec("120/minute"))
async def archivo_publico(request: Request, token: str) -> FileResponse:
    archivo = verificar(token)
    ruta = resolve_local_path(archivo) if archivo else None
    if ruta is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Enlace caducado o inexistente")
    tipo = mimetypes.guess_type(archivo)[0] or "application/octet-stream"
    return FileResponse(
        ruta,
        media_type=tipo,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )
