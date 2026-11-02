"""Elegir el pg_dump / pg_restore que corresponde al servidor de la BD.

POR QUÉ EXISTE ESTE MÓDULO
--------------------------
Las herramientas de línea de comandos de Postgres NO son compatibles en las
dos direcciones, y las dos incompatibilidades muerden:

  - `pg_dump` de una versión MENOR que el servidor **aborta**:
    "aborting because of server version mismatch". Con cliente 16 contra un
    servidor 17 no hay copia (comprobado: rc=1, ni un byte escrito).
  - `pg_restore` de una versión MAYOR que el servidor **rompe la
    restauración**: el dump que genera `pg_dump` 17 lleva
    `SET transaction_timeout = 0;`, un parámetro que no existe antes de la 17,
    y al restaurarlo contra un servidor 16 salta
    "unrecognized configuration parameter" → con `--single-transaction` se
    deshace todo y la base se queda como estaba (comprobado: el dato de prueba
    seguía roto tras el intento).

O sea: la copia se hace y se sube, el panel la da por buena porque descifra y
empieza por PGDMP, y el día que hace falta NO se puede restaurar. Ese es el
fallo que este módulo evita.

CÓMO LO RESUELVE
----------------
La imagen lleva instalados los clientes de VARIAS versiones (ver
backend/Dockerfile: postgresql-client-16 y postgresql-client-17). Aquí se
pregunta al servidor qué versión es y se ejecuta el binario de esa misma
versión, que vive en /usr/lib/postgresql/<major>/bin/.

Consecuencia práctica, que es lo que se buscaba: la misma imagen sirve para una
instalación con Postgres 16 y para una con 17. Quien ya tiene datos en 16 NO
tiene que migrar nada — le basta con volver a desplegar.

Si no se puede averiguar la versión del servidor (psql ausente, red caída), se
devuelve el nombre pelado (`pg_dump`) y manda el PATH, que es exactamente el
comportamiento anterior a este módulo: se degrada, no se rompe.
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

from app.core.logging import get_logger

logger = get_logger(__name__)

# Donde Debian/PGDG instala los binarios de cada versión. Cada
# postgresql-client-N crea aquí su carpeta N/bin.
PG_BIN_ROOT = Path("/usr/lib/postgresql")

# Herramientas que este módulo sabe resolver.
PG_TOOLS = ("pg_dump", "pg_restore", "psql", "pg_dumpall")

# La versión del servidor se pregunta una vez y se guarda un rato: se consulta
# en cada copia y en cada restauración, y no cambia sin recrear el contenedor.
# El TTL existe para que un servidor actualizado en caliente no quede cacheado
# para siempre en un proceso de vida larga (el worker de Celery lo es).
_CACHE_TTL_SECS = 600
_server_major_cache: dict[tuple, tuple[float, int | None]] = {}

# Techo para las consultas de versión: un psql colgado no puede dejar la tarea
# de copia esperando para siempre.
_PROBE_TIMEOUT_SECS = 20


def installed_client_majors(root: Path | None = None) -> list[int]:
    """Versiones mayores de cliente instaladas, de menor a mayor.

    Se mira que exista el binario, no solo la carpeta: un paquete a medias deja
    el directorio pero no las herramientas.
    """
    base = PG_BIN_ROOT if root is None else root
    majors: list[int] = []
    try:
        entries = sorted(base.iterdir())
    except (OSError, AttributeError):
        return []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        if (entry / "bin" / "pg_dump").exists():
            majors.append(int(entry.name))
    return sorted(majors)


def choose_client_major(server_major: int | None, available: list[int]) -> int | None:
    """Qué cliente usar contra un servidor de versión `server_major`.

    - Igual que el servidor si está instalado: es el único caso en que dump y
      restore funcionan los dos.
    - Si no, el más bajo POR ENCIMA del servidor: al menos la copia se hace
      (pg_dump mayor sí lee servidores menores); la restauración necesitará el
      camino de compatibilidad.
    - Si todos los instalados son menores, el más alto: es el único con alguna
      posibilidad, y el aviso de arranque ya lo habrá cantado.
    - Sin versión de servidor conocida, el más alto: maximiza las copias que
      salen, que es el fallo más caro de los dos.
    """
    if not available:
        return None
    if server_major is None:
        return max(available)
    if server_major in available:
        return server_major
    mayores = [v for v in available if v > server_major]
    if mayores:
        return min(mayores)
    return max(available)


def tool_path(name: str, major: int | None, root: Path | None = None) -> str:
    """Ruta absoluta del binario de esa versión, o el nombre pelado.

    El nombre pelado deja decidir al PATH. En Debian con varios clientes
    instalados eso es `pg_wrapper`, que elige la versión MÁS ALTA: justo lo que
    hay que evitar contra un servidor 16. Solo se cae aquí cuando no se sabe
    nada mejor.
    """
    if major is None:
        return name
    base = PG_BIN_ROOT if root is None else root
    candidate = base / str(major) / "bin" / name
    return str(candidate) if candidate.exists() else name


def _probe_server_major(db: dict) -> int | None:
    """Pregunta al servidor su versión mayor con el psql más alto disponible.

    Se usa `psql` y no una librería de Python a propósito: esto lo llaman tanto
    la tarea Celery (síncrona) como el endpoint de restauración (asíncrono), y
    un subprocess corto vale igual en los dos sitios sin arrastrar el pool de
    SQLAlchemy a un worker.

    La contraseña va SOLO en PGPASSWORD, nunca en argv (misma regla que el
    resto del módulo de copias: argv es visible en /proc).
    """
    majors = installed_client_majors()
    psql = tool_path("psql", max(majors) if majors else None)
    cmd = [
        psql,
        "--no-password",
        "-tAX",
        "-h", str(db.get("host") or "db"),
        "-p", str(db.get("port") or 5432),
        "-U", str(db.get("user") or ""),
        "-d", str(db.get("dbname") or ""),
        "-c", "SHOW server_version_num",
    ]
    env = {**os.environ, "PGPASSWORD": str(db.get("password") or "")}
    # Un psql que se queda esperando a un servidor que no contesta bloquearía
    # la copia entera: la conexión también lleva techo propio.
    env.setdefault("PGCONNECT_TIMEOUT", "10")
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECS
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("pg_tools.probe_failed", error=str(exc)[:200])
        return None
    if proc.returncode != 0:
        logger.warning(
            "pg_tools.probe_failed",
            rc=proc.returncode,
            error=(proc.stderr or "").strip()[-200:],
        )
        return None
    raw = (proc.stdout or "").strip()
    if not raw.isdigit():
        return None
    # server_version_num es 160014 / 170011: los dos primeros dígitos (o el
    # primero, en las de antes de la 10) son la versión mayor.
    return int(raw) // 10000


def server_major(db: dict, *, refresh: bool = False) -> int | None:
    """Versión mayor del servidor, cacheada por conexión."""
    clave = (db.get("host"), db.get("port"), db.get("dbname"))
    ahora = time.monotonic()
    if not refresh:
        cached = _server_major_cache.get(clave)
        if cached is not None and (ahora - cached[0]) < _CACHE_TTL_SECS:
            return cached[1]
    valor = _probe_server_major(db)
    _server_major_cache[clave] = (ahora, valor)
    return valor


def reset_cache() -> None:
    """Olvida la versión cacheada (tests y arranque)."""
    _server_major_cache.clear()


def pg_tool(name: str, db: dict) -> str:
    """La herramienta que toca para ESTE servidor. Es la función a usar."""
    if name not in PG_TOOLS:
        raise ValueError(f"herramienta no soportada: {name}")
    disponibles = installed_client_majors()
    if not disponibles:
        # Nada que elegir (imagen sin PGDG, o un portátil de desarrollo): ni se
        # molesta en preguntar la versión al servidor. Manda el PATH.
        return name
    return tool_path(name, choose_client_major(server_major(db), disponibles))


# ---------------------------------------------------------------------------
# Versión con la que se HIZO un dump (para decidir cómo restaurarlo)
# ---------------------------------------------------------------------------

_DUMPED_BY_RE = re.compile(r"Dumped by pg_dump version:\s*(\d+)\.")


def dump_major(dump_path: Path) -> int | None:
    """Con qué versión de pg_dump se generó un fichero .dump (formato custom).

    Se lee con el cliente MÁS ALTO instalado a propósito: un pg_restore menor
    ni siquiera puede listar un archivo más nuevo ("unsupported version (1.16)
    in file header"), así que preguntarle a él daría un falso "no lo sé".
    """
    majors = installed_client_majors()
    binario = tool_path("pg_restore", max(majors) if majors else None)
    try:
        proc = subprocess.run(
            [binario, "--list", str(dump_path)],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("pg_tools.dump_major_failed", error=str(exc)[:200])
        return None
    if proc.returncode != 0:
        return None
    match = _DUMPED_BY_RE.search(proc.stdout or "")
    return int(match.group(1)) if match else None


# ---------------------------------------------------------------------------
# Aviso de arranque: cliente y servidor descuadrados
# ---------------------------------------------------------------------------


def avisos_de_cliente_postgres(db: dict) -> list[str]:
    """Comprobación de arranque: ¿sirven las copias que va a hacer esta imagen?

    Existe porque la verificación diaria de copias (verify_last_backup) solo
    mira que el fichero descifre y empiece por PGDMP. Un dump hecho por un
    cliente descuadrado pasa esa prueba y NO se puede restaurar: el panel se
    queda en verde con copias inservibles. Esto lo dice el día del despliegue,
    no el día del desastre.
    """
    avisos: list[str] = []
    disponibles = installed_client_majors()
    servidor = server_major(db)

    if servidor is None:
        # Sin versión de servidor no se puede afirmar nada; decirlo es más útil
        # que callar, porque significa que las copias van a ciegas.
        avisos.append(
            "No se ha podido leer la versión de PostgreSQL del servidor, así que "
            "no se puede comprobar que las copias de seguridad vayan a poder "
            "restaurarse. Revisa DATABASE_URL y que la base de datos acepte "
            "conexiones desde este contenedor."
        )
        return avisos

    if not disponibles:
        avisos.append(
            f"El servidor es PostgreSQL {servidor} pero en esta imagen no hay "
            f"ningún cliente de PostgreSQL instalado en {PG_BIN_ROOT}: las "
            "copias de seguridad no se van a poder hacer. Reconstruye la imagen "
            "del backend (backend/Dockerfile)."
        )
        return avisos

    if servidor in disponibles:
        return avisos

    mayores = [v for v in disponibles if v > servidor]
    versiones = ", ".join(str(v) for v in disponibles)
    if mayores:
        avisos.append(
            f"El servidor es PostgreSQL {servidor} y esta imagen solo trae los "
            f"clientes {versiones}. Las copias se harán con el {min(mayores)}, "
            f"pero un dump del {min(mayores)} NO se restaura directamente sobre "
            f"un servidor {servidor}: se usará el camino de compatibilidad, más "
            "lento. Añade postgresql-client-"
            f"{servidor} a backend/Dockerfile para dejarlo limpio."
        )
    else:
        avisos.append(
            f"El servidor es PostgreSQL {servidor} y esta imagen solo trae los "
            f"clientes {versiones}, todos MENORES: pg_dump aborta contra un "
            "servidor más nuevo y NO habrá copias de seguridad. Añade "
            f"postgresql-client-{servidor} a backend/Dockerfile y vuelve a "
            "desplegar."
        )
    return avisos
