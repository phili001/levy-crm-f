"""El worker y el beat tapan los datos personales de sus registros, como la API.

Salió en el dry-run 2 (S-1, crm2): `configure_logging` solo se llamaba desde
`app.main`, así que el worker —que procesa mensajes, correos y llamadas— y el
beat se quedaban con la configuración de fábrica de structlog, sin `_scrub_pii`.
Además Celery apuntaba cada evento `info` como WARNING.

Se prueba en un proceso aparte que solo importa `app.tasks`, como hace
`celery -A app.tasks worker`: en el de pytest la API ya lo configuró antes.
"""
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]

CODIGO = """
import structlog
import app.tasks
structlog.get_logger("prueba").info(
    "mensaje.recibido", telefono="+529980000003", correo="paciente@ejemplo.mx"
)
print("NIVEL", app.tasks.celery_app.conf.worker_redirect_stdouts_level)
print("REINTENTO", app.tasks.celery_app.conf.broker_connection_retry_on_startup)
"""


def test_un_evento_del_worker_sale_sin_telefono_ni_correo():
    salida = subprocess.run(
        [sys.executable, "-c", CODIGO],
        capture_output=True, text=True, cwd=BACKEND, env=dict(os.environ), timeout=60,
    )
    assert salida.returncode == 0, salida.stderr[-2000:]
    assert "mensaje.recibido" in salida.stdout
    assert "+529980000003" not in salida.stdout
    assert "paciente@ejemplo.mx" not in salida.stdout
    assert "<PHONE>" in salida.stdout
    assert "NIVEL INFO" in salida.stdout
    # Sin el aviso de Celery en cada arranque del worker.
    assert "REINTENTO True" in salida.stdout
