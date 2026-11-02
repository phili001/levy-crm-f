"""El catálogo «Copiar prompt» (Fase 10.2).

Los prompts para pegar en Claude Code viven como archivos en `backend/prompts/`
(`docs/prompts` es un enlace): se editan como texto y viajan en la imagen del
backend, así que cada versión enseña los suyos, igual que el CHANGELOG. El
panel los enseña en Plataforma → Prompts y en el botón «Copiar prompt» de cada
tarjeta de Canales → Servicios.

Cada archivo `NN-id.md` lleva una cabecera entre `---` con `titulo`, `grupo`
(`principal` | `avanzado`), `resumen` y, si es de una tarjeta, `canal`. El
`00-LEEME.md` explica la carpeta y no sale en el panel. Se lee a mano (clave:
valor) para no añadir una dependencia por cuatro campos.
"""
from __future__ import annotations

from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

GRUPOS = ("principal", "avanzado")


def carpeta() -> Path | None:
    """Dentro de la imagen, /app/prompts; en desarrollo, backend/prompts."""
    raiz = Path(__file__).resolve().parents[2]
    for ruta in (Path("/app/prompts"), raiz / "prompts"):
        if ruta.is_dir():
            return ruta
    return None


def _partir(contenido: str) -> tuple[dict, str]:
    """(cabecera, texto) de un archivo con cabecera entre `---`."""
    if not contenido.startswith("---\n"):
        return {}, contenido
    cabecera_txt, _, texto = contenido[4:].partition("\n---\n")
    cabecera = {}
    for linea in cabecera_txt.splitlines():
        clave, sep, valor = linea.partition(":")
        if sep:
            cabecera[clave.strip()] = valor.strip()
    return cabecera, texto


def leer_catalogo() -> list[dict]:
    """Los prompts en su orden (el del nombre del archivo).

    Un archivo mal escrito no tumba el catálogo: se salta y queda en el log.
    """
    base = carpeta()
    if base is None:
        return []
    prompts = []
    for archivo in sorted(base.glob("[0-9][0-9]-*.md")):
        if archivo.name.startswith("00-"):
            continue
        cabecera, texto = _partir(archivo.read_text(encoding="utf-8"))
        titulo = cabecera.get("titulo")
        grupo = cabecera.get("grupo")
        if not titulo or grupo not in GRUPOS or not texto.strip():
            logger.warning("prompts_catalogo.archivo_invalido", archivo=archivo.name)
            continue
        prompts.append(
            {
                "id": archivo.stem.split("-", 1)[1],
                "titulo": titulo,
                "grupo": grupo,
                "canal": cabecera.get("canal") or None,
                "resumen": cabecera.get("resumen", ""),
                "texto": texto.strip(),
            }
        )
    return prompts
