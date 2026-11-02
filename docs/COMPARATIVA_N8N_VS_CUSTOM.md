# Chatbot en n8n vs sistema custom (Python/Celery)

Comparativa sin marketing y sin exageraciones: lo que cada opción hace
bien y dónde tiene su límite.

---

## Cómo funciona n8n por dentro (queue mode)

n8n separa los procesos en dos roles:

- **Main**: recibe los webhooks de WhatsApp / Telegram / lo que sea. Su
  único trabajo es validar y meter la "ejecución" en una cola Redis.
- **Workers**: procesos aparte que sacan ejecuciones de la cola y ejecutan
  el workflow paso a paso.

Cuando llega un mensaje:

1. WhatsApp → webhook al `main` de n8n
2. `main` mete la ejecución en Redis
3. El primer `worker` libre la coge
4. Ejecuta el workflow nodo por nodo (Switch, OpenAI, Postgres, etc)
5. Responde a WhatsApp

Para escalar: lanzas más workers. Cada uno procesa N ejecuciones en
paralelo según `EXECUTIONS_PROCESS_MAX_CONCURRENCY` (suele rondar 5-10
por worker para no agotar la memoria).

**Detalle importante**: cada ejecución carga el workflow entero en memoria
(definición de nodos, conexiones, estado), no solo el código del nodo que
está corriendo. Eso pesa.

---

## Cómo funciona el sistema custom (Python + Celery)

Mismo patrón conceptual:

- **app (FastAPI)**: recibe webhooks, valida firma, guarda en BD, encola
  la tarea en Redis.
- **worker (Celery)**: 4 trabajadores en paralelo (`--concurrency=4`, pool
  `prefork`, que es lo que traen los dos ficheros de despliegue). Cada uno coge
  una tarea de la cola y la ejecuta.
- **beat**: cron interno para tareas periódicas (limpieza, healthchecks).

Además del trío de arriba, la pila lleva `db` (Postgres 16 con pgvector),
`redis` y `frontend` (el panel): seis servicios en total. El servidor MCP va
dentro de `app`, en `/mcp`.

Cuando llega un mensaje:

1. WhatsApp → webhook al `app`
2. `app` guarda en BD + encola tarea "procesar mensaje X" en Redis
3. El primer trabajador del worker que esté libre la coge
4. Ejecuta el flujo del agente (moderation + LLM + RAG + send)
5. Responde a WhatsApp

Para escalar: subir `--concurrency`, cambiar el pool a `threads` (que es lo
apropiado para un trabajo que se pasa el rato esperando a APIs, aunque **no
viene puesto**), añadir más réplicas del worker, o subir el plan de OpenAI (que
normalmente es el cuello real).

**Detalle importante**: no hay workflow que cargar. Lo que se mueve por la cola
es un identificador de mensaje; el código del bot ya está en memoria. Mucho más
ligero por ejecución que n8n, incluso con `prefork`.

**Detalle importante 2**: el buffer no bloquea. Un trabajador que recibe un
mensaje lo mete en el buffer, agenda el procesado para dentro de unos segundos y
queda libre al momento. Por eso 4 trabajadores aguantan mucho más de lo que su
número sugiere. Detalle en `docs/ARQUITECTURA_CONCURRENCIA.md`.

---

## Comparativa honesta (lo que importa)

| Aspecto | n8n (queue mode) | Custom (Python/Celery) |
|---|---|---|
| **Velocidad para prototipar** | Muy alta (clicas y conectas) | Baja (hay que escribir código) |
| **Curva de aprendizaje** | Suave (visual) | Empinada (necesitas saber Python o usar IA bien) |
| **Coste por ejecución** | Más RAM por ejecución (Node.js + workflow completo cargado) | Menos RAM por ejecución (Python + solo el dato) |
| **Concurrencia por euro de VPS** | 5-10 ejecuciones por worker, normalmente | 4 trabajadores de fábrica, y suben sin cambiar de VPS (el buffer no bloqueante los deja casi siempre libres) |
| **Customización** | Limitada a lo que ofrecen los nodos. Custom = JS embebido o nodos Code | Total. Lo que se te ocurra |
| **Debugging visual** | Excelente (ves cada paso del workflow) | Tienes logs, no visual |
| **Versionado** | Workflows guardados en BD. Backup manual | Git nativo, ramas, PRs |
| **Tests automatizados** | Difícil | Estándar (pytest) |
| **Costes de mantenimiento** | Sube el plan o más workers | Cambias 1 línea, redeploy |
| **Comunidad y ejemplos** | Enorme. Hay nodos para casi todo | Pequeña, hay que saber buscar |

---

## Qué aguanta cada uno (cifras orientativas honestas)

Para un chatbot de WhatsApp con flujo típico (moderation + LLM + envío):

| Config | Aguanta hasta | Cuándo se rompe |
|---|---|---|
| **n8n 1 worker, 5 concurrency, VPS 2GB RAM** | ~30-50 mensajes/min sostenidos | Si entran ráfagas grandes, la cola sube y los workers tardan más |
| **n8n 3 workers x 5 concurrency, VPS 4GB RAM** | ~100-150 mensajes/min | Empieza a notar OpenAI como cuello |
| **Custom tal y como se despliega (4 trabajadores), VPS 2GB RAM** | ~50 mensajes/min sostenidos | Los propios trabajadores; el siguiente paso es subir `--concurrency` |
| **Custom con el pool cambiado a threads (16)** — *no viene puesto* | ~150 mensajes/min | Rate limit de OpenAI (tier 1 estándar) |

Ojo con la tercera y la cuarta fila: **la que te llevas es la tercera**. La
cuarta es lo que darían unos cambios en el comando del worker que tendrías que
hacer y probar tú.

NOTA: estas cifras son orientativas y dependen mucho de:
- Cuánto tarda OpenAI en responder ese día
- Si el workflow/agente usa RAG (suma 1-2s por mensaje)
- Si hay tools que llaman APIs externas
- El tamaño del contexto de cada conversación

No te dejes engañar por benchmarks que prometen "10.000 mensajes/segundo":
prácticamente nunca es el bottleneck del bot.

---

## Cuándo usar cada uno (esto es lo importante)

### Usa n8n si:

- Estás empezando y necesitas tener algo funcionando esta semana.
- El flujo es **lineal y simple**: webhook → procesar → responder. Pocos
  caminos condicionales.
- Vas a tener volumen bajo o medio (<10.000 mensajes/día) y no te
  preocupa pagar un plan de hosting más caro.
- Necesitas que **alguien no técnico** del equipo pueda tocar el flujo
  (cambiar el prompt, añadir un paso).
- Cambias cosas a menudo y necesitas iterar visualmente.

### Usa custom (Python/Celery) si:

- Vas a tener volumen alto o crecimiento esperado.
- El flujo es complejo: muchas tools del agente, integración fina con BD,
  reglas de negocio que cambian seguido y necesitan tests.
- Necesitas **control total** sobre la lógica (rate limits propios,
  cifrado de PII, auditoría detallada, etc).
- Tienes un equipo (o un asistente IA tipo Claude Code) capaz de mantener
  código.
- El presupuesto de OpenAI/infra empieza a doler y quieres optimizar.

### El camino habitual:

Lo normal es empezar por **n8n**, porque es lo más rápido para tener
resultados. Cuando se llega al límite (volumen, complejidad,
personalización), se da el salto al sistema custom apoyándose en un
asistente de código para no escribirlo todo a mano. Es la migración
natural.

---

## Lo que NO suele contarte la gente

- n8n a partir de cierto volumen necesita un VPS gordo. **El precio del
  hosting puede crecer rápido** si no lo planificas.
- El sistema custom necesita **alguien que entienda el código** cuando se
  rompe. Un asistente de IA ayuda mucho, pero hay que saber qué pedirle.
- Ambos dependen de **OpenAI**. Si OpenAI cae o cambia precios, da igual
  qué framework uses por debajo: te afecta.
- "Real-time" en chatbots es un mito. La latencia real de un mensaje son
  5-15 segundos por culpa del LLM, no de tu infra. Pelear por bajar de
  100ms a 50ms en tu webhook no cambia nada.

---

## En resumen

Si te están vendiendo "el framework más rápido" te están vendiendo humo.
La velocidad real del bot la dicta el LLM y el rate limit de OpenAI.
La diferencia real entre n8n y custom es **flexibilidad, control,
mantenibilidad y coste a largo plazo**, no velocidad bruta.

Lo bueno es que no son excluyentes: muchas veces conviven en el mismo
negocio (n8n para automatizaciones genéricas + bot custom para el caso
de uso principal).
