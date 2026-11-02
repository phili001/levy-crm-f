# Pantallas del panel — dónde están de verdad

> **Este documento ya no describe pantallas.** La versión anterior era un
> wireframe del diseño inicial y describía siete pantallas que hoy o no
> existen o funcionan de otra manera. Era concreto y falso a la vez, que es la
> peor combinación: un agente lo leía como especificación y "arreglaba" cosas
> que funcionaban. Se ha sustituido por el mapa real.
>
> **La lista completa y única de rutas es `frontend/src/router.tsx`.** Ábrelo.
> El menú lateral está en `frontend/src/components/Layout.tsx`. El sistema
> visual, en [`style_guide.md`](style_guide.md), que sí sale del CSS real.

## Rutas reales

Comprobadas contra `frontend/src/router.tsx`. Lo que no esté aquí cae en
`NotFound`.

### Fuera de sesión

`/login` · `/forgot-password` · `/reset-password`

### Con sesión

| Ruta | Componente | Quién |
|---|---|---|
| `/` | redirección | `admin` → `/admin`; el resto → `/inbox` |
| `/inbox` | `pages/client/Inbox` | todos |
| `/calls` | `pages/client/Calls` | admin y operador |
| `/contacts` | `pages/client/Contacts` | admin y operador |
| `/contacts/:id` | `pages/client/ContactDetail` | admin y operador |
| `/knowledge-base` | `pages/client/KnowledgeBase` | admin y operador |
| `/profile` | `pages/ProfilePage` | todos |
| `/conexiones` | `pages/revisor/ConexionesRevisor` | solo `revisor` |

### Rama `/admin` (solo rol `admin`)

| Ruta | Componente |
|---|---|
| `/admin` | `AdminHome` — es la home del admin, no hay `/dashboard` |
| `/admin/users` | `UsersPage` |
| `/admin/connections` | `ConnectionsPage` — claves y canales, agrupados por servicio |
| `/admin/settings` | `SettingsPage` |
| `/admin/backups` | `BackupsPage` |
| `/admin/outbound` | `OutboundPage` |
| `/admin/agent/dashboard` | `AgentDashboard` |
| `/admin/agent/flow` | `FlowDashboard` |
| `/admin/agent/agents` | `AgentsPage` — con pestañas `?tab=classifier`, `?tab=model`, `?tab=internal` |
| `/admin/agent/learning` | `LearningPage` — "Aprendizajes" |
| `/admin/audit` | `AuditPage` |
| `/admin/health` | `HealthPage` |
| `/admin/blocklist` | `BlocklistPage` |
| `/admin/system/logs` | `SystemLogs` |
| `/admin/internal-agent/prompt` | `InternalAgentPromptPage` |

### Redirecciones (rutas viejas que se mantienen para no romper marcadores)

Estas **no son pantallas**. No implementes nada detrás de ellas.

| Ruta vieja | Va a |
|---|---|
| `/admin/credentials` | `/admin/connections?tab=services` |
| `/admin/agent/prompt` | `/admin/agent/agents` |
| `/admin/agent/config` | `/admin/agent/agents?tab=model` |
| `/admin/agent/classifier` | `/admin/agent/agents?tab=classifier` |
| `/admin/internal-agent` | `/admin/agent/agents?tab=internal` |

### Rutas que NO existen

`/dashboard`, `/tags` y `/admin/notifications` aparecían en el wireframe viejo.
Ninguna existe.

- El dashboard del operador es `/admin` (`AdminHome`).
- Las etiquetas se gestionan **dentro de la ficha de contacto**, con
  `components/TagPicker.tsx`. La página suelta se eliminó a propósito.
- No hay pantalla de notificaciones porque no hay Telegram ni Slack: los avisos
  al operador van por web push (`api/push.py`) y por el registro en vivo.

## Menú lateral

`Layout.tsx`. Para el admin va en cuatro secciones: **Operativa** ·
**Agente IA** · **Admin** · **Sistema**. El operador ve solo la parte de
Operativa; el revisor, únicamente Inbox y Conexiones.

## Si necesitas saber cómo es una pantalla

Ábrela. Los componentes están en `frontend/src/pages/` y llevan comentarios que
explican por qué están hechos así. Este documento no los repite: repetirlos es
justo lo que hizo que la versión anterior mintiera.
