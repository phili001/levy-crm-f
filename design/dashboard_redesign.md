# Rediseño del Dashboard → Home principal

> **Aviso.** Este documento es un plan: describe la intención y en qué orden se
> hizo, no el árbol de ficheros de hoy. Para saber dónde está algo, manda el
> código: la pantalla es `frontend/src/pages/admin/AdminHome.tsx` y los datos
> salen de `backend/app/services/dashboard.py`.

Enfoque: **dashboard genérico del producto, adaptable por negocio.**

El diseño de referencia es el que implementa el propio panel; la guía de estilo está en `design/style_guide.md`.

> **Sin tiempo real (WebSocket).** Para un operador único
> con un bot que responde solo, el "wallboard en vivo" (colas, visitas web ahora,
> latencia, traza siempre corriendo) es vanidad y sobre-ingeniería. La Home será
> **"viva por polling" (~30s)**, indistinguible para este caso y mucho más simple.
> La señal que importa ("¿hay alguien esperando que el bot no atendió?") ya la da
> `/admin/dashboard/attention`. La traza en vivo se queda **bajo demanda** donde ya vive:
> traza por conversación + `/admin/system/logs`. **Se elimina la Fase 3 (WebSocket).**

## Objetivo

Convertir el dashboard en la **pantalla de inicio** del operador: visión "de un
vistazo" del estado del negocio + accesos rápidos a la operación. **Adaptable**:
las zonas específicas (citas/agenda) solo aparecen si el negocio usa esa
capacidad (calendario conectado).

## Layout (6 zonas del prototipo)

1. **Hero** — saludo + resumen en lenguaje natural + tarjeta **"Estado actual"** (bot activo/pausado por canal, handoffs pendientes ahora, conversaciones hoy). Refrescada por **polling**, no WebSocket.
2. **KPI band (4)** — conversaciones hoy (vs ayer), % resueltas por IA, [métrica de negocio configurable], coste/uso LLM. Con tendencia.
3. **Actividad** — gráfico de área "conversaciones por hora" (hoy vs media 7d) + donut "canal de origen".
4. **Necesitan atención** — handoffs pendientes + conversaciones sin respuesta > N min.
5. **Salud del sistema** + acceso **on-demand** a la traza (por conversación) y a `/admin/system/logs`. Sin widget de traza en vivo.
6. **Ticker** de novedades — opcional, baja prioridad.

## Adaptable por negocio (clave de la decisión)

- **Zonas universales** (1, 2 parcial, 3, 4, 5, 6): para cualquier negocio.
- **Citas/Agenda e "ingreso por citas"**: SOLO si el negocio tiene calendario
  conectado. Si el tuyo no trabaja con cita previa, esas zonas se ocultan y en
  su hueco va la métrica que sí signifique algo para ti. Ejemplo: una tienda
  puede poner pedidos abiertos; una asesoría, expedientes en curso. Es una
  decisión de cada instalación, no del producto.
- El **4.º KPI** y la **métrica de negocio** deben ser condicionales o
  configurables.

## Datos: qué hay vs. qué falta

**Ya disponible (endpoints):** uso de tokens/coste, estado de pausa, salud de
canales, traza (`agent_trace_event`), conversaciones (lista + filtros por canal).

**Creado (Fase 1, ✅):**
- `GET /admin/dashboard/summary` — KPIs agregados (hoy/ayer, % resueltas por IA,
  derivadas), serie por hora (hoy vs media 7d), mix por canal.
- `GET /admin/dashboard/attention` — handoffs pendientes + sin respuesta > N min.

**Estado actual (Hero):** se compone con datos **ya existentes y baratos** vía
polling — `/admin/agent/pause` (bot por canal), `/admin/dashboard/attention`
(handoffs ahora) y `/admin/dashboard/summary` (hoy). Sin endpoints de "en vivo"
nuevos. **Los tres van con el prefijo `/api/v1/admin/` y con `require_admin`.**

## Rutas

- La Home es `/admin` (`AdminHome.tsx`) y es la **ruta de inicio del admin**.
  El operador y el revisor entran por `/inbox`; el reparto lo hace
  `HomeRedirect` en `frontend/src/router.tsx`. No hay ruta `/dashboard`.
- El dashboard de agente (`AgentDashboard.tsx`: pausa global y por canal,
  presupuesto, lista blanca de demo) **no se tiró**: sigue en
  `/admin/agent/dashboard`, enlazado desde el menú.

## Fases

1. ✅ **Backend**: endpoints de agregados (`summary` enriquecido + `attention`) + `services/dashboard.py` + setting `DASHBOARD_TIMEZONE`. Tests 39/39.
2. ✅ **Frontend**: `AdminHome.tsx` con layout Coro (6 zonas), datos reales de los endpoints, **refresco por polling (~30s)**. Gráficos SVG a mano (área + donut).
3. **Adaptabilidad** (parcial): ya es genérico (sin citas/ingreso hardcodeado; usa métricas disponibles). Falta que la métrica de negocio del 4.º KPI sea configurable por negocio.
4. ✅ **Routing**: Home como inicio del admin (operador → inbox) + entrada "Inicio" en el menú lateral.
5. **Verificación E2E** + seed con datos realistas para que todas las zonas rendericen.

> ~~Fase WebSocket "en vivo"~~ — **eliminada** (decisión 2026-06-02, ver arriba).

## Notas

- Respetar el sistema **Coro** (`design/style_guide.md`). El prototipo ya lo usa.
- Los prototipos de partida traían datos de ejemplo de un negocio con agenda
  (citas, profesionales). Eran solo eso, ejemplos del caso piloto: **no hay nada
  de citas ni de ingresos escrito a fuego en el código.** El diseño es genérico.
