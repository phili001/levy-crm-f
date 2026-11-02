# Arquitectura de concurrencia del chatbot

Cómo se procesan los mensajes entrantes y qué volumen aguanta el sistema.
Para gente no técnica (el administrador / cliente final).

Las cifras de este documento son las del **despliegue tal y como sale de fábrica**:
`docker-compose.yml` y `docker-compose.easypanel.yml` arrancan el worker con
`celery -A app.tasks worker --concurrency=4`. Si tú cambias ese número, cambia
todo lo de abajo.

El ejemplo va con WhatsApp porque es el canal más habitual, pero el camino es el
mismo para el chat web, Instagram, el correo y la voz: cambia quién avisa al
principio y por dónde sale la respuesta al final.

---

## El recorrido de un mensaje, paso a paso

```
1. Cliente escribe en WhatsApp
2. YCloud (proveedor WhatsApp) recibe y reenvía el mensaje al webhook
3. Container `app` (FastAPI):
     - Verifica firma de YCloud
     - Guarda el mensaje en BD (PostgreSQL)
     - Encola tarea "procesar mensaje X" en Redis
     - Responde 200 OK a YCloud (~1s)
4. Container `worker` (Celery):
     - 4 trabajadores en paralelo escuchan la cola
     - El primer libre coge la tarea
     - Lo mete en el buffer (agrupar ráfagas) y agenda el drenado
     - Termina inmediatamente, queda libre
5. Unos segundos después (Celery countdown, 8s por defecto):
     - Una nueva tarea drena el buffer y procesa SI fue el último mensaje
     - Llama Moderation (OpenAI) → Chat (LLM) → quizá KB search → YCloud
6. Cliente recibe respuesta por WhatsApp (~10-20s total)
```

---

## Por qué hay buffer (y cuánto espera cada canal)

Sin buffer, si el cliente escribe rápido:

```
"Hola"          → bot: "Hola, ¿en qué te ayudo?"
"oye"           → bot: "Dime"
"una pregunta"  → bot: "Claro, pregunta"
"del horario"   → bot: "Sí, dime"
```

Cuatro respuestas descontextualizadas. Pésimo.

Con buffer: el bot espera unos segundos desde el último mensaje, junta
todo el texto ("Hola oye una pregunta del horario") y responde **una sola
vez** entendiendo el contexto completo.

Desde la Fase 10.2 el valor es **del canal**, no del agente (el agente de texto
es uno solo para todos los canales): WhatsApp, Instagram y Messenger esperan
**8 segundos**, el chat web **3** (en una web nadie escribe en ráfagas y se nota
la espera) y el correo **nada**. Está en `backend/app/core/channel_capabilities.py`
y se puede cambiar por canal en `channels.config["capacidades"]`. WhatsApp,
Instagram y Messenger tienen además un **suelo de 6 segundos**
(`MIN_TEXT_BUFFER_SECONDS`): por debajo de eso el agrupado no sirve de nada,
porque una persona escribiendo seguido deja huecos de uno o dos segundos. La
**voz** no pasa por el buffer: la llamada está en vivo.

---

## Qué "pool" usa el worker (prefork, 4 procesos)

Celery tiene varios "pools" (formas de ejecutar tareas en paralelo):

| Pool | Cómo funciona | Memoria | Bueno para |
|------|---------------|---------|------------|
| `prefork` | N procesos OS independientes | Alta (N × bundle) | CPU-bound (cálculo intensivo) |
| `threads` | N hilos dentro de un proceso | Baja (heap compartido) | **I/O-bound** (esperar HTTP, BD) |
| `gevent`/`eventlet` | N coroutines en 1 proceso | Mínima | Miles de conexiones simultáneas |

**Lo que se despliega hoy es `prefork` con 4 procesos.** El comando del worker
no lleva `--pool`, y sin ese parámetro Celery usa `prefork`, que es su valor por
defecto. Cuatro procesos independientes, cada uno con su copia del código en
memoria.

> **Mejora estudiada, no aplicada:** nuestro trabajo es **I/O-bound** (la mayor
> parte del tiempo el worker está esperando a OpenAI, a la base de datos o a
> Redis, sin hacer nada). Para eso `--pool=threads` con más trabajadores daría
> bastante más concurrencia por la misma RAM. **No está puesto en el compose**,
> así que no cuentes con ello: si lo quieres, es un cambio que tienes que hacer
> tú, probar y asumir. Lo mismo con subir `--concurrency`.

---

## Buffer no bloqueante (esto sí está hecho)

Esta parte no es teoría: es lo que hace el código
(`backend/app/services/conversation.py` → `drain_task.apply_async(countdown=…)`).

**Cómo NO se hace:**

```
Trabajador 1 recibe msg de Juan → push al buffer → DORMIR 8s
                                                   ↑ trabajador bloqueado
                                                     no puede hacer nada
Trabajador 2 recibe msg de María → push al buffer → DORMIR 8s
...
```

Con 4 trabajadores y una ráfaga de 4 personas, los 4 quedarían dormidos.
La persona 5 esperaría en cola sin que nadie estuviera haciendo nada.

**Cómo se hace:**

```
Trabajador 1 recibe msg de Juan → push al buffer → agendar drain a +8s → LIBRE
Trabajador 2 recibe msg de María → push al buffer → agendar drain a +8s → LIBRE
...
8s después, Celery dispara drain_buffer(juan), drain_buffer(maria)...
Esos sí ejecutan el procesamiento real (Moderation + LLM + send), uno por
ráfaga finalizada. Cada uno tarda 5-8s reales.
```

Los 4 trabajadores nunca quedan dormidos esperando — siempre están
procesando o libres para coger lo siguiente. Es lo que hace que 4 trabajadores
aguanten mucho más de lo que su número sugiere.

---

## Volumen que aguantamos

Asumiendo que cada procesamiento real (Moderation + LLM + KB + send) dura
~5 segundos, un trabajador saca unos 12 mensajes por minuto.

| Config | Trabajadores | Cuello de botella | Mensajes/min |
|--------|--------------|-------------------|--------------|
| **La que se despliega: 4 prefork** | 4 | los propios trabajadores | **~50** |
| 8 prefork (subir `--concurrency`) | 8 | RAM del worker (8 procesos) | ~100 |
| 16 con `--pool=threads` *(no aplicado)* | 16 | rate limit de OpenAI | ~150 |

Las dos últimas filas **no son el estado actual**: son lo que pasaría si tocas
el comando del worker. Ninguna está en el compose que te llevas.

**Cuando el cuello deja de ser el worker**, pasa a serlo el rate limit de
OpenAI. Plan Tier 1 (el de por defecto) permite ~500 peticiones/minuto en chat.
Cada conversación hace típicamente 3 llamadas (moderación + chat + a veces RAG).
Eso son ~166 conversaciones/min como techo. Subir el plan de OpenAI sube el
límite.

Para hacerte una idea: **~50 mensajes/minuto sostenidos son unos 70.000 al día.**
Un comercio pequeño no se acerca ni de lejos. Si tú sí, el primer paso está más
abajo.

---

## Componentes del sistema

```
┌─────────────┐    HTTPS POST    ┌─────────────┐
│   YCloud    │ ───────────────> │  app (Web)  │
│ (WhatsApp)  │                  │   FastAPI   │
└─────────────┘                  └──────┬──────┘
                                        │ encola tarea
                                        ▼
                                 ┌─────────────┐
                                 │   Redis     │ ◄─── pub/sub eventos
                                 │  (broker)   │      tiempo real
                                 └──────┬──────┘
                                        │ workers escuchan
                                        ▼
                                 ┌─────────────┐
                                 │   worker    │ ── 4 procesos
                                 │   Celery    │    en paralelo
                                 └──────┬──────┘
                                        │ guarda + lee
                                        ▼
                                 ┌─────────────┐
                                 │ PostgreSQL  │
                                 │  + pgvector │
                                 │    (pg16)   │
                                 └─────────────┘
```

El despliegue completo son **seis servicios**: los cuatro del diagrama (`app`,
`redis`, `worker`, `db`) más `beat` (el cron interno que dispara las tareas
periódicas) y `frontend` (el panel, React servido por nginx). El servidor MCP no
es un servicio aparte: lo sirve `app` en `/mcp`.

---

## Qué pasa si falla algo

| Falla | Qué ocurre | Recuperación |
|-------|------------|--------------|
| Worker se cae | Tareas en cola Redis quedan guardadas | Easypanel reinicia worker (~30s), retoma desde la cola |
| `app` se cae | YCloud no recibe 200, reintenta el webhook | Easypanel reinicia, próximo webhook entra |
| OpenAI 500 | Celery reintenta automáticamente 3 veces con backoff | Se recupera solo, si no eventualmente se loguea |
| BD se cae | Health check falla, Easypanel reinicia containers | Cuando BD vuelve, todo retoma |
| Redis se cae | Cola y eventos en tiempo real caen | Easypanel reinicia, mensajes nuevos llegan; los que estaban en cola se pierden (poco común) |
| Redis se **vacía** (volumen borrado, servicio recreado) | Además de lo anterior: se pierden las pausas del agente. Un canal que apagaste a propósito vuelve a estar activo | **Entra al panel y comprueba el estado de cada canal.** Nadie te avisa; ver `docs/backups.md`, "Qué se copia y qué no" |

---

## Próximos pasos de escala

Cuando el negocio crezca, en este orden:

1. **Subir `--concurrency`** del worker (de 4 a 8) en el comando del compose. Es
   una línea y no cambia nada más. Vigila la RAM: con `prefork` cada trabajador
   es un proceso entero.
2. **Cambiar a `--pool=threads`** con más trabajadores (16 es un valor razonable
   para el perfil I/O-bound de este sistema). Es el cambio con mejor relación
   entre lo que cuesta y lo que da, pero **no está probado en este paquete**:
   pruébalo fuera de producción antes.
3. **Réplicas `app=2`, `worker=2`** en EasyPanel: sin corte en los despliegues y
   tolerancia a fallos.
4. **Plan OpenAI Tier 2+**: sube el rate limit a 5000 RPM.
5. **PgBouncer o réplicas de lectura** para la BD si llegas a miles de
   mensajes/min.
6. **CDN delante del frontend** si los operadores conectan desde muchos sitios.

Para el volumen normal (decenas o cientos de mensajes al día) no se necesita
nada de esto. Los 4 trabajadores con el buffer no bloqueante aguantan de sobra.
