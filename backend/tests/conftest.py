"""Configuración global de tests."""
import os
import tempfile

import pytest

os.environ.setdefault("APP_ENV", "test")
# Los tests DB-gated abren un event loop por test (asyncio.run / asyncio_mode
# auto). Con el pool persistente del proceso API, las conexiones quedarían
# atadas al loop del test anterior → NullPool explícito (igual que Celery).
os.environ.setdefault("DB_POOL", "null")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:test@localhost:5432/test")
os.environ.setdefault("JWT_SECRET", "test-secret-change-in-prod")
os.environ.setdefault("ENCRYPTION_KEY", "PqArc6c-wI8DK-bGwCPBlo4e_gg-Ym5mSnjh05FXQd4=")
# Rutas de almacenamiento. En local las define backend/.env, pero .env está en
# .gitignore y en la CI no existe: sin esto se usan los defaults de config.py
# (/data/audios), que en el runner no son escribibles. api/knowledge_base.py
# hace os.makedirs A NIVEL DE MÓDULO, así que el import revienta y se caen 39
# tests con PermissionError: '/data'. Un temporal vale para los tres entornos
# (CI, local, Docker) y además aísla: los tests no escriben en el storage real.
_TEST_STORAGE = os.path.join(tempfile.gettempdir(), "chatbot-tests-storage")
os.environ.setdefault("AUDIO_STORAGE_PATH", os.path.join(_TEST_STORAGE, "audios"))
os.environ.setdefault("UPLOADS_PATH", os.path.join(_TEST_STORAGE, "audios", "uploads"))
os.environ.setdefault("BACKUP_DIR", os.path.join(_TEST_STORAGE, "audios", "backups"))


@pytest.fixture(autouse=True)
def _sin_sondeo_de_postgres(monkeypatch):
    """Ningún test unitario abre un `psql` para preguntar la versión del server.

    services/pg_tools elige el pg_dump/pg_restore de la misma versión mayor que
    el servidor, y para eso lo sondea con psql. En la suite eso sobra y además
    estorba: los tests de copias sustituyen `subprocess.Popen` por un pg_dump de
    mentira, y `subprocess.run` lo usa por dentro, así que el sondeo se comía el
    doble de la broma y reventaba.

    Aquí se deja el sondeo en "no lo sé", que es una rama real del código (sin
    respuesta del servidor se usa el cliente más alto). La elección de versión
    tiene sus propios tests en tests/test_pg_tools.py, y el comportamiento
    contra un Postgres de verdad se comprueba levantando la app, no aquí.
    """
    from app.services import pg_tools

    pg_tools.reset_cache()
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: None)
    yield
    pg_tools.reset_cache()
