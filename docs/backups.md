# Backups de la base de datos

> ## ⚠️ Lo primero: guarda `ENCRYPTION_KEY` fuera del servidor
>
> Las copias se cifran con `ENCRYPTION_KEY`, que es una variable de entorno
> del contenedor, configurada en el panel de EasyPanel **del mismo servidor**.
> Si pierdes el servidor —el desastre exacto contra el que protege la copia
> remota— pierdes la clave con él, y el fichero cifrado del bucket es ruido
> irrecuperable. **Cópiala a un gestor de contraseñas el día del despliegue.**
> Si la cambias, las copias anteriores dejan de poder restaurarse.

Backups automáticos de Postgres mediante la tarea Celery
`app.tasks.backup_db.backup_db`. La frecuencia es
**configurable desde el panel** (Admin → Copias de seguridad): cada hora / 6 h
/ 12 h / diaria / semanal / mensual (defecto diaria a las 04:00 en la zona horaria del negocio;
las de "días" corren a la hora que elijas). El beat dispara `backup_tick` cada
10 min y este decide contra la config de BD si toca copia.

Si en el panel hay un bucket S3-compatible configurado (Cloudflare R2 / Amazon
S3 / MinIO, claves `backup_s3_*` en credenciales), la copia se **cifra con
AES-256-GCM** (clave derivada de `ENCRYPTION_KEY`) y se sube a
`<bucket>/chatbot/<fecha>.dump.enc` con retención automática (7 diarias + 4
semanales; con más frecuencia, además las últimas 24-48). Cada intento queda
registrado en `backup_runs` y se ve en el panel, que también ofrece "Hacer
copia ahora", "Descargar última copia" (descifrada) y "Restaurar…" con doble
confirmación (`pg_restore --clean --single-transaction`). Ojo: esos dos últimos
botones **solo trabajan contra el bucket** — ver el aviso de más abajo.

> **Después de restaurar, reinicia la aplicación** (`app`, `worker` y `beat`).
> La restauración recrea las tablas y la aplicación se queda con el plano viejo
> en memoria: la primera petición contra cada conexión abierta devuelve un 500
> con `cached statement plan is invalid`. Se recupera sola al siguiente intento,
> pero durante unos segundos el panel da errores sin estar roto. El reinicio lo
> evita. (El runbook manual de más abajo ya rearranca los servicios; el botón
> del panel no.)

### Dónde acaba cada copia (selector "Dónde se guarda cada copia")

| Destino | Con espacio en disco | Sin espacio en disco |
|---|---|---|
| **Servidor** | Dump a fichero en `BACKUP_DIR`. No se sube nada. | No hay copia: se avisa y se registra el fallo (sin bucket no hay dónde salvarla). |
| **Bucket** (recomendado) | Copia **directa al bucket**: pg_dump escribe por su salida estándar, se cifra por bloques y se sube en varias partes. No deja fichero en el servidor. | Igual. Además se avisa del disco. |
| **Ambos** | Dump a fichero + subida desde el fichero. | Copia directa al bucket, marcada como **degradada** (sin fichero local por falta de espacio). |

> ### ⚠️ Con destino "Servidor" el panel no restaura
>
> Los botones **Descargar última copia** y **Restaurar…** trabajan solo contra
> el **bucket**: la lista de copias sale de `list_remote_backups()` y tanto la
> descarga como la restauración exigen que la clave venga de él
> (`backend/app/api/backups.py`, `backend/app/services/backups.py`). Una copia
> que solo existe como fichero en `BACKUP_DIR` no aparece en esa lista y no se
> puede restaurar desde el panel: hay que hacerlo a mano por SSH con el
> [runbook de más abajo](#runbook-de-restauración).
>
> Con destino **"Ambos"** no hay problema: la copia también está en el bucket y
> el panel la ve. **Si eliges "Servidor", asume que el día que tengas que
> restaurar vas a hacerlo por línea de comandos.** Es otra razón para preferir
> "Bucket" o "Ambos".

> ### ⚠️ Un bucket (o al menos una cuenta) por instalación
>
> Todas las copias se suben bajo el prefijo `chatbot/`, que está **fijo en el
> código** (`APP_PREFIX` en `backend/app/services/backups.py`). No se configura
> y no depende del nombre del despliegue.
>
> Consecuencia: si apuntas **dos instalaciones al mismo bucket**, escriben en la
> misma carpeta y se ven las copias la una a la otra. Y como la retención borra
> por antigüedad dentro de ese prefijo, la limpieza de una **borra las copias de
> la otra**, sin avisar y sin que nada se ponga en rojo. La restauración también
> se mezcla: en el desplegable te salen copias de la instalación vecina.
>
> Regla: **un bucket por instalación**. Si tu proveedor te lo pone difícil, al
> menos una cuenta o unas claves distintas por instalación.

El guardia de disco (`BACKUP_MIN_FREE_GB`) solo bloquea la **escritura local**.
Nunca bloquea la subida: si hay bucket, la copia se hace igual. Es un fallo
fácil de cometer y caro: con destino bucket y el disco lleno, un guardia que
bloquease todo dejaría al sistema **sin ninguna copia**.

### Formatos del fichero cifrado

- `EKB1` — histórico, un solo bloque AES-GCM. Se sigue descifrando siempre.
- `EKB2` — por bloques (8 MB, `BACKUP_STREAM_CHUNK_MB`). Es el que permite
  cifrar y subir sin fichero intermedio, con un pico de memoria de un bloque
  en vez del doble del tamaño del dump. Cada bloque va atado a su posición y
  el último va marcado: un fichero cortado **no** descifra.

La descarga y la restauración distinguen los dos formatos solos.

> ⚠️ La imagen del backend lleva `postgresql-client-16` **y**
> `postgresql-client-17`, y usa el que corresponda a la versión del servidor
> (`backend/app/services/pg_tools.py`). Esto no es un adorno: un pg_dump más
> antiguo que el servidor aborta y no hace copia, y uno más nuevo hace una
> copia que **no se puede restaurar** sobre ese servidor. Si algún día subes la
> base de datos a la 18, añade `postgresql-client-18` en `backend/Dockerfile`
> en la misma pasada. Al arrancar, la aplicación avisa en el registro si el
> cliente y el servidor no cuadran. Ver `docs/version_de_postgres.md`.

## Qué se copia y qué no

**SÍ se copia** (dump completo de la BD con `pg_dump --format=custom`):

- Conversaciones y mensajes (todos los canales).
- Contactos y notas/actividad del CRM.
- Credenciales cifradas del panel de administración (cifradas con
  `ENCRYPTION_KEY`: para restaurarlas en otro despliegue hace falta la MISMA
  clave en el `.env`/config del deployment).
- Configuración del agente, usuarios, knowledge base (incluidos los embeddings
  pgvector), logs de auditoría, etc. — toda tabla de la BD.

**NO se copia:**

- Los **audios** y uploads (`/data/audios`, `/data/audios/uploads`): viven en el
  volumen `audios_data`, fuera de la BD.
- **Redis**. Casi todo lo que hay ahí es efímero (colas de Celery, buffers,
  throttles, límites por contacto) y se reconstruye solo. **Pero no todo**: los
  interruptores del agente viven ahí y **no caducan**
  (`backend/app/services/agent_pause.py`):

  | Clave | Qué es |
  |---|---|
  | `agent:paused` | El agente está pausado en TODOS los canales |
  | `agent:paused:{canal}` | Pausado solo en ese canal |
  | `agent:training:{canal}` | Ese canal está en modo Entrenamiento (genera borrador, no envía) |
  | `agent:transcribe_audio:{canal}` | Transcripción de notas de voz encendida/apagada en ese canal |

  Si pierdes Redis —se borra el volumen, se recrea el servicio, se vacía la
  base—, esas claves desaparecen y todo vuelve al valor por defecto: **agente
  activo, enviando**. Es decir: **un agente que apagaste a propósito se pone a
  contestar otra vez a clientes reales, y nada te avisa.** El panel te lo
  enseñará como activo porque para él lo está.

  Lo que hay que hacer: si tocas Redis (reinicio del servicio, cambio de
  versión, limpieza del volumen), **entra al panel y comprueba el estado de
  cada canal antes de darlo por hecho**. Y si vas a dejar el agente pausado un
  tiempo largo, apúntalo fuera de la aplicación; no es un dato que la copia de
  seguridad te devuelva.
- El `.env` / variables de deployment (en particular `ENCRYPTION_KEY` y
  `JWT_SECRET`): guárdalos aparte en un gestor de secretos.

## Dónde viven los backups

```
/data/audios/backups/<prefijo>_YYYYMMDD_HHMMSS.dump   (timestamp en UTC)
```

dentro de los contenedores **app** y **worker** (volumen persistente
`audios_data`, el único montado — por eso comparten ruta con los audios, igual
que `UPLOADS_PATH`).

(solo con destino "Servidor" o "Ambos": con destino "Bucket" no queda fichero).

- **Retención local**: por antigüedad, `BACKUP_RETENTION_DAYS` (7 días), **y
  por número**, `BACKUP_RETENTION_MAX_FILES` (14 ficheros). El tope por número
  es imprescindible con frecuencias altas: cada hora son 168 dumps antes de
  que el primero cumpla 7 días, y la retención por edad no libera un byte.
  La limpieza corre **antes** del guardia de disco (si lo que llenó el disco
  son dumps viejos, se limpian antes de decidir que no cabe la copia de hoy)
  y otra vez después del volcado. El dump más reciente nunca se borra.
- **Guardia de disco**: si quedan menos de `BACKUP_MIN_FREE_GB` (por defecto
  2 GB) libres, NO se escribe el dump a fichero y se alerta
  (`backup_skipped_low_disk`). Con bucket configurado la copia se hace igual,
  directa al bucket (ver tabla de destinos). Un fallo del dump alerta como
  `backup_failed`, intenta el rescate directo al bucket (destino "Ambos") y se
  reintenta una vez a los 5 minutos.

## Que la copia valga algo (verificación)

- **Tras cada subida** se comprueba el tamaño real del objeto en el bucket. Una
  subida cortada ya no se registra en verde.
- **Una vez al día** (lo dispara `backup_tick`, con candado en Redis):
  1. prueba de conexión al bucket — si el token de Cloudflare caduca se sabe
     ese día, no la noche que falle la copia;
  2. verificación real de la última copia: se descarga, se descifra y se
     comprueba que empieza por la cabecera de un dump de PostgreSQL. Queda en
     el histórico como tipo *Verificación*.
- También a mano: `POST /admin/backups/verify`.

**Tres resultados, no dos.** `verify_last_backup()` devuelve `(ok, mensaje,
estado)` (`backend/app/services/backups.py`):

| Estado | Cuándo | Qué pasa |
|---|---|---|
| `ok` | Se ha abierto una copia del bucket, descifra y es un dump de PostgreSQL | Verde. Entrada `verify` / `ok` en el histórico |
| `fallo` | Se ha intentado abrir y no ha salido: no descifra, está cortada, no es un dump, o no se pudo leer la configuración del bucket | Entrada `verify` / `failed`, aviso en el panel y push (`backup_verify_failed`) |
| `no_verificable` | **No se ha abierto nada**: sin bucket configurado, o el bucket todavía sin copias | Ni verde ni alerta, y no se registra nada: no hay nada que contar |

El tercer estado existe porque esos dos casos devolvían `True` y el panel los
pintaba en verde: decía "verificada" sin haber mirado absolutamente nada. Ahora
`no_verificable` viene con `ok=False` —por eso no hay verde— y el chequeo
diario **no** alerta por él: no es un fallo de la copia, es que no hay copia que
mirar.

De ahí lo importante: **una copia solo está verificada si vive en el bucket**.
Con el destino en "Servidor" no se verifica ninguna. Otra razón para dejar el
destino en "Bucket" o en "Ambos".

Caso aparte: una copia en el **formato antiguo** (bloque único) que pase de
`VERIFY_FULL_DOWNLOAD_MAX_BYTES` no cabe en memoria para descifrarla entera, así
que sale como `fallo` con ese motivo escrito. Se arregla solo: la siguiente
copia ya usa el formato por trozos y sí se verifica.

## Avisos

Las alertas de copias van al panel (**Sistema → Logs en vivo**) y,
además, como **notificación push a la PWA** del operador en los casos que
importan: copia fallida, copia saltada por disco, subida fallida, copia
degradada, bucket inalcanzable y última copia no restaurable. No hay
integración con Telegram ni con Slack (se eliminó del producto: los avisos no
salen de la aplicación).

## Runbook de restauración

> ⚠️ La restauración **PISA los datos actuales** de la BD (`--clean`). Hazla
> con el agente parado para que no entren escrituras a mitad.

Todos los comandos se ejecutan por SSH en el servidor (host Docker/EasyPanel).

**1. Localiza los contenedores** (los nombres reales varían según el deploy):

```bash
docker ps --format '{{.Names}}' | grep -Ei 'db|backend|worker'
# ejemplo de salida:
#   <proyecto>_db.1.xxxx        ← Postgres (pgvector/pg16 o pg17)
#   <proyecto>_backend.1.xxxx   ← API
#   <proyecto>_worker.1.xxxx    ← Celery worker (tiene los backups montados)
```

**2. Para el worker y la API** (evita escrituras durante la restauración). En
EasyPanel: botón *Stop* de los servicios app y worker. A mano:

```bash
docker stop <contenedor_backend> <contenedor_worker>
# (si paras el worker, copia ANTES el dump fuera, ver paso 3 con docker cp
#  directo al volumen, o usa la API en su lugar — ambos montan el volumen)
```

**3. Saca el dump del volumen al host y mételo en el contenedor de la BD:**

```bash
# listar los backups disponibles (vale cualquier contenedor que monte el volumen)
docker exec <contenedor_worker> ls -lh /data/audios/backups/

# volumen → host
docker cp <contenedor_worker>:/data/audios/backups/<dump>.dump /tmp/

# host → contenedor de la BD
docker cp /tmp/<dump>.dump <contenedor_db>:/tmp/
```

**4. Restaura** (el usuario y la base de datos los tienes en la
`DATABASE_URL` del deployment):

```bash
docker exec -it <contenedor_db> pg_restore \
  -U <usuario> -d <base_de_datos> \
  --clean --if-exists --no-owner \
  /tmp/<dump>.dump
```

> **Por qué la restauración se hace DENTRO del contenedor de la base de datos:**
> ahí las herramientas son exactamente de la versión del servidor, que es la
> única combinación que funciona siempre. Si lo intentas desde el contenedor de
> la aplicación tecleando `pg_restore` a secas, coge la versión más alta que
> tenga instalada y contra un servidor 16 falla con
> `unrecognized configuration parameter "transaction_timeout"`.

> **¿Te sale `unsupported version (1.16) in file header`?** Esa copia se hizo con
> herramientas más nuevas que tu servidor: es lo que dejaban las versiones
> anteriores de la imagen en las instalaciones con Postgres 16. El rodeo a mano,
> desde el contenedor de la aplicación (que tiene los dos clientes):
>
> ```bash
> docker compose exec app sh -c 'PGPASSWORD="<contraseña>" \
>   /usr/lib/postgresql/17/bin/pg_restore --clean --if-exists --no-owner \
>     --no-privileges -f - /data/audios/backups/<dump>.dump \
>   | grep -v "^SET transaction_timeout" \
>   | /usr/lib/postgresql/16/bin/psql --no-password -X -q -v ON_ERROR_STOP=1 \
>       --single-transaction -h db -U <usuario> -d <base_de_datos>'
> ```
>
> Convierte la copia a SQL con las herramientas que sí saben leerla, quita la
> línea que tu servidor no entiende y la carga en una sola transacción.
>
> Si esa copia está **en el bucket**, te ahorras el rodeo: el botón
> **Restaurar…** del panel detecta el caso y lo resuelve solo. Para una copia
> que solo existe como fichero en el servidor, el panel no sirve (no la ve) y
> hay que hacerlo con el comando de arriba.

- `--clean --if-exists`: borra cada objeto antes de recrearlo (sin fallar si
  no existe) — restauración idempotente sobre una BD viva.
- `--no-owner`: no intenta recrear los owners originales (todo queda del
  usuario con el que conectas).
- Avisos tipo `errors ignored on restore` sobre `DROP EXTENSION vector` o
  esquemas de sistema son normales con `--clean`; lo importante es que las
  tablas queden pobladas.

**5. Verifica y rearranca:**

```bash
docker exec -it <contenedor_db> psql -U <usuario> -d <base_de_datos> \
  -c "select count(*) from messages;"
docker start <contenedor_backend> <contenedor_worker>
```

Entra al panel y comprueba que conversaciones/contactos están. Las
credenciales cifradas solo descifran si `ENCRYPTION_KEY` es la misma que
cuando se hizo el backup.

## Descargar una copia fuera del servidor

Lo normal es hacerlo desde el panel ("Descargar última copia": la baja del
bucket y la descifra). A mano, contra el fichero local del servidor:

```bash
# Opción A — desde tu máquina, en un solo paso (ssh + docker cp en el server):
ssh root@SERVIDOR "docker cp \$(docker ps -qf name=worker | head -1):/data/audios/backups/<dump>.dump /tmp/" \
  && scp root@SERVIDOR:/tmp/<dump>.dump ./backups/

# Opción B — directamente del volumen en el host (sin pasar por el contenedor):
ssh root@SERVIDOR
docker volume inspect audios_data --format '{{.Mountpoint}}'
# → /var/lib/docker/volumes/audios_data/_data
exit
scp root@SERVIDOR:/var/lib/docker/volumes/audios_data/_data/backups/*.dump ./backups/
```

## Limitaciones que quedan (importante)

Una copia que vive en **el mismo disco del mismo servidor** que la BD protege
contra borrados accidentales, migraciones que salen mal y corrupción lógica,
pero **NO contra la pérdida total del servidor** (disco roto, borrado del VPS,
ransomware del host). Por eso el destino recomendado es el bucket: conecta
Cloudflare R2 en Admin → Copias de seguridad → *Proveedor de almacenamiento* y
elige "Bucket" o "Ambos". Sin bucket, descarga copias a mano periódicamente.

Y con bucket queda un cabo suelto que solo puedes atar tú: **la clave de
cifrado vive en el mismo servidor** (aviso del principio de este documento).
Copia `ENCRYPTION_KEY` a un gestor de contraseñas o la copia remota no te
servirá el día que pierdas la máquina.
