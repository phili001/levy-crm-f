# cache-bust 2026-05-17 18:15:39 UTC: force rebuild after switching build type from Nixpacks to Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    curl \
    gosu \
    && rm -rf /var/lib/apt/lists/*

# pg_dump/pg_restore desde el repo oficial PGDG, para los backups de Postgres
# (tarea Celery backup_db). Se instalan DOS versiones, 16 y 17, porque los dos
# descuadres posibles rompen algo:
#   - cliente MENOR que el servidor: pg_dump aborta ("aborting because of
#     server version mismatch") y no hay copia.
#   - cliente MAYOR que el servidor: la copia se hace, pero el dump lleva
#     `SET transaction_timeout` (nuevo en la 17) y no se restaura sobre un
#     servidor 16 — el panel enseña copias verdes que no sirven.
# Cuál se usa NO lo decide el PATH (con dos instalados, pg_wrapper elige el más
# alto): lo decide app/services/pg_tools.py preguntando la versión al servidor.
# Así la misma imagen vale para una instalación con Postgres 16 y para una con
# 17, y actualizarse es volver a desplegar, sin tocar los datos.
# Si algún despliegue sube a pg18, añadir aquí postgresql-client-18.
# La clave del repo va por signed-by (sin apt-key).
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
        -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] http://apt.postgresql.org/pub/repos/apt $(. /etc/os-release; echo "$VERSION_CODENAME")-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-16 postgresql-client-17 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh

# Usuario sin privilegios + dirs de datos preparados. El entrypoint corre
# inicialmente como root para poder ajustar el ownership de volúmenes
# montados (EasyPanel los puede inicializar con uid 0) y después baja a
# 'app' vía gosu antes de ejecutar uvicorn / celery.
#
# Solo /data/audios: es el ÚNICO volumen persistente que se monta. Todo lo
# demás cuelga de ahí (uploads/, backups/ y kb_docs/, que crea al importarse
# api/knowledge_base.py). Aquí había además un `/data/kb_docs` suelto, fuera
# de todo volumen y que no usaba nadie: directorio muerto en la imagen.
RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data/audios \
    && chown -R app:app /app /data

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
