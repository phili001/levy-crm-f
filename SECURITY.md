# Seguridad — CRM Omnicanal

Este documento describe el modelo de seguridad de la app, qué ataques mitiga
y qué responsabilidades quedan del lado del operador (tú). Vivo, actualizar
con cada cambio.

> **Si vas a desplegar HOY, ve a [§ 6 Checklist pre-producción](#6-checklist-pre-producción).**
> El paso a paso de la instalación está en [`docs/avanzado/easypanel.md`](./docs/avanzado/easypanel.md);
> aquí solo está lo que afecta a la seguridad.

---

## 1. Modelo de confianza

| Actor | Confianza | Por qué |
|---|---|---|
| **Admin** (rol `admin`) | Alta | Configura credenciales, modelo, prompts. Toda acción queda auditada. |
| **Operador** (rol `cliente`) | Media | Lee/responde conversaciones. Puede borrar contactos/etiquetas. No accede a `/admin/*`. **Ve toda la base de contactos**, ver aviso debajo. |
| **Revisor** (rol `revisor`) | Baja | Cuenta temporal para la revisión de la app en Meta. Solo la bandeja de Instagram. Ver aviso debajo. |
| **Usuario final de cualquier canal** | **Nula** | Cualquier persona del mundo con tu número, tu Instagram, tu web, tu teléfono o tu correo. Toda entrada se considera adversaria. |
| **Proveedores de canal** (YCloud, Meta, Zernio, Retell, Google) | Alta | Cada webhook entrante se verifica antes de procesarlo. Detalle en [§ 2.3](#23-canales-de-entrada-quién-entra-y-qué-lo-protege). |
| **OpenAI / Anthropic / Gemini / Resend** | Alta | Conexiones HTTPS salientes, sin entrada de datos. |
| **Cliente MCP con token de agente** | Media | Entra por `/mcp`, en el dominio del CRM. Con ámbito de escritura puede reescribir el prompt del bot. Ver [§ 2.4](#24-servidor-mcp-y-tokens-de-agente). |
| **EasyPanel host** | Alta | Tiene acceso al volumen Postgres y a las env vars. |

La app es **single-tenant**: una instancia por cliente, sin aislamiento entre operadores dentro de la misma instancia.

### El rol `cliente` ve toda la base de contactos (decisión consciente)

Hay tres roles (`backend/app/models/user.py`), pero para el día a día solo
cuentan dos: `admin` guarda lo irreversible (exportar, borrar, importar,
credenciales, copias de seguridad) y `cliente` es "operador de bandeja". El
tercero, `revisor`, es una cuenta temporal para la revisión de Meta y tiene su
propio apartado más abajo.

Ser `cliente` incluye poder leer **todos** los contactos, todas las
conversaciones, las notas internas, los RFC, las direcciones y las
transcripciones de las llamadas de cualquier ficha. No puede exportarlo de una
tacada, pero sí ir pasando páginas.

No es un fallo: es el modelo de permisos tal y como está escrito, y encaja con
el producto —una instalación por negocio, con un equipo pequeño que atiende a
sus propios clientes—. Lo que implica en la práctica:

- **Da de alta como `cliente` solo a quien ya podría ver esa información**
  trabajando en el negocio. No es un rol para colaboradores externos, becarios
  de un solo canal ni proveedores.
- Si necesitas que alguien vea únicamente su parte, hoy **no se puede**: haría
  falta un tercer rol limitado a su propia bandeja.
- Si el negocio trata datos de salud, financieros o de menores, tenlo en cuenta
  al repartir los accesos: quien entra al panel entra a todo el fichero.

Toda acción sigue quedando en el registro de auditoría con usuario, IP y
momento (`/admin/audit`), así que el acceso no es anónimo.

### El tercer rol: `revisor` (cuenta temporal para Meta)

Existe desde la migración `0055_user_role_revisor`. Es la cuenta que le das a
quien revisa tu app en Meta cuando pides los permisos de Instagram: Meta exige
poder entrar y ver la integración funcionando, y esa persona no es de tu equipo.

**Qué ve:** la bandeja, filtrada a Instagram y solo a Instagram. Puede abrir una
conversación, leerla, responder, marcarla como leída, tomar el control,
devolvérsela al bot y abrir los adjuntos y las notas de voz de esos mismos
mensajes. Ve de qué cuenta de Instagram se trata y puede conectar **la suya** con
Business Login, que es justo lo que Meta necesita comprobar; ese token se guarda
en una fila aparte, así que conectarse no echa a tu cuenta ni toca el canal.

**Qué no ve:** contactos, base de conocimiento, llamadas, los demás canales
(WhatsApp, chat web, correo, voz) y todo `/admin/*`. El corte no lo hace la
interfaz: está arriba, en `get_current_user`, contra una lista blanca de rutas
(`RUTAS_DE_REVISOR` en `backend/app/api/deps.py`). Lo que no está en esa lista
devuelve 403 aunque teclees la URL a mano. En el frontend, el guard
`SoloRevisor` (`frontend/src/router.tsx`) mantiene la coherencia visual.

**El tiempo real tiene el mismo corte (1.3.3).** El WebSocket de la bandeja es
uno para todos, y hasta la 1.3.2 le reenviaba al revisor todos los avisos, con
el teléfono de WhatsApp del cliente dentro. Ahora cada aviso pasa por el mismo
criterio que el HTTP (`services/bandeja_acceso.py`): al revisor solo le llegan
los de conversaciones de Instagram, y si no se puede saber el canal, no le llega
nada. Además, los avisos de la bandeja ya no llevan el teléfono ni el correo de
nadie: solo ids y el canal.

**La cuenta se crea para una revisión y se borra al acabar.** No es un rol de
invitado ni un "usuario de solo lectura" para colaboradores: sigue leyendo
conversaciones reales con clientes reales. Está en el checklist de cierre de la
[§ 6.10](#610-al-acabar-la-revisión-de-meta-borra-la-cuenta-de-revisor).

---

## 2. Capas de defensa

### 2.1 Autenticación / autorización del panel
- JWT HS256 firmado con `JWT_SECRET` (mínimo 32 caracteres, validado al arranque en producción).
- Cada JWT incluye `jti` único. Logout añade el token a una blocklist en Redis hasta su `exp`.
- bcrypt para passwords (límite 72 bytes; pwds más largas se rechazan, no se truncan).
- Rate-limit en `/auth/login`: 5/min · 30/h por IP del cliente real (`X-Forwarded-For`).
- `get_current_user` solo acepta token en cabecera `Authorization: Bearer ...`. El WebSocket recibe el token por query-string vía un helper separado.
- Admin no puede auto-desactivarse ni auto-degradarse el rol.
- **Primer arranque (instalación en un clic, Fase 10.3b).** Con `SETUP_CODE`
  puesto y sin ningún administrador activo, `POST /api/v1/primer-arranque` crea
  el primero a quien traiga el código de arranque (`SERVICE_PASSWORD_ARRANQUE`:
  32 caracteres que genera Coolify y que solo se ven en su panel). El código se
  compara en tiempo constante; 5/min · 20/h por IP; un candado de Postgres
  impide dos altas a la vez; la contraseña pasa la misma política que el resto
  del panel, y el alta queda en la auditoría (`primer_arranque.admin_creado`).
  En cuanto hay un administrador activo responde 409 y no hace nada. Si algún
  día no quedara ninguno activo, la ventana se volvería a abrir para quien tenga
  el código: es la vía de recuperación, y por eso el código solo vive en Coolify.

### 2.2 Credenciales de proveedor
- Almacenadas cifradas con Fernet (`ENCRYPTION_KEY`) en la tabla `credentials`.
- Lectura desde la BD vía `services.credentials.get_credential` con caché en memoria de 30s e invalidación al actualizar.
- Las claves de OpenAI/YCloud/Meta/Resend **nunca** se leen de env vars en producción — solo desde la tabla cifrada.
- El admin las gestiona desde `/admin/connections` (pestaña **Servicios**).
- Las claves **por canal** de Instagram (`app_secret`, `page_access_token`,
  `verify_token`), de Zernio (`zernio_api_key`, `zernio_webhook_secret`) y de
  Retell (`api_key`, `webhook_secret`) van igual de
  cifradas, pero en otra columna: `channels.credentials_encrypted`, desde la
  migración 0054. Antes vivían en `channels.config`, que es JSONB en plano: con
  el `app_secret` de Meta a la vista se pueden firmar webhooks falsos y saltarse
  la comprobación de firma. La lista exacta de claves que se cifran está en
  `services/channel_secrets.py:SECRET_KEYS`. Se leen con
  `services.channel_secrets.channel_config` y se escriben con
  `apply_channel_values`; nada más debe tocar esas claves.
- Excepción a propósito: la `api_key` del chat web **no** se cifra porque es
  pública por diseño — viaja en el HTML de la web del cliente. Lo que la protege
  es la lista de dominios permitidos, no el secreto. Cómo se crea ese canal y
  dónde se pone esa lista: [§ 2.3.d](#23d-chat-web-cómo-se-crea-y-dónde-va-la-lista-de-dominios).

### 2.3 Canales de entrada: quién entra y qué lo protege

La app tiene **siete** vías por las que entra tráfico de gente desconocida. Cada
una tiene su dirección, su secreto y su forma de comprobar que quien llama es
quien dice ser. Si un secreto no está puesto, la puerta se cierra (no se abre).

Las direcciones que hay que pegar en el panel de cada proveedor las tienes
también en la app: **Conexiones → Canales**, tarjeta del canal, botón de copiar.

| Canal | Dirección de entrada | Secreto que lo protege | Cómo se comprueba |
|---|---|---|---|
| **WhatsApp (YCloud)** | `POST /api/v1/webhooks/ycloud` | `ycloud_webhook_secret` (tabla `credentials`, cifrado) | HMAC-SHA256 sobre `timestamp.cuerpo`, cabecera `YCloud-Signature` (`t=…,s=…`), comparación en tiempo constante. Sin secreto → **401**. |
| **WhatsApp (API oficial de Meta)** | `GET` + `POST /api/v1/webhooks/whatsapp/meta` | `meta_wa_verify_token` (alta del webhook) y `meta_wa_app_secret` (firma), ambos en `credentials`, cifrados | GET: comparación en tiempo constante del token, si no cuadra **403**. POST: HMAC-SHA256 del cuerpo crudo, cabecera `X-Hub-Signature-256`. Sin `app_secret` → **401**. |
| **Instagram (DM)** | `GET` + `POST /api/v1/webhooks/instagram` | `verify_token` y `app_secret` del canal (`channels.credentials_encrypted`, cifrados) | Igual que Meta: token en el alta, HMAC-SHA256 del cuerpo crudo en cada aviso. Sin credenciales → **403** / **401**. |
| **Instagram y Messenger (Zernio)** | `POST /api/v1/webhooks/zernio` | `zernio_webhook_secret` del canal (`channels.credentials_encrypted`, cifrado). Lo elige el CRM al registrar el webhook por API y no vuelve al navegador | HMAC-SHA256 hex del cuerpo crudo, cabecera `X-Zernio-Signature` (o `X-Late-Signature`), en tiempo constante. Sin firma o sin secreto → **401**. Una cuenta de Zernio que no es la guardada en el canal se ignora. Los mensajes de pacientes pasan por Zernio Software SL (España): lo dice el aviso de privacidad (`/privacidad`). |
| **Chat web (widget)** | `POST /api/v1/webchat/sessions`, `POST /messages`, `GET /history`, `WS /api/v1/webchat/ws/{id}` | `api_key` del canal — **pública a propósito**, va en el HTML — + token de sesión firmado con `JWT_SECRET` | No hay secreto que esconder: lo que decide quién puede usarlo es la **lista de dominios permitidos** (§ 2.3.d). La sesión se firma con HMAC-SHA256 y caduca. |
| **Voz (Retell)** | `WS /api/v1/voice/retell/llm-ws/{call_id}` y `POST /api/v1/voice/retell/webhook` | `webhook_secret` y `api_key` del canal (`channels.credentials_encrypted`, cifrados) | HMAC-SHA256 del cuerpo, cabecera `x-retell-signature`. El webhook además rechaza avisos con más de 5 minutos (anti-repetición). El WebSocket comprueba el `call_id` contra la API de Retell antes de atender. Sin credenciales → cierre `1008` / **401**. |
| **Correo (Gmail)** | **No hay dirección de entrada.** La app va a buscar el correo cada 2 minutos (tarea `poll_gmail`) | Token OAuth de Google, cifrado | No hay firma que comprobar porque nadie llama desde fuera: es la app la que llama a Google autenticada. Sin autorización → no lee nada y lo deja escrito en los registros. |

Además, en los tres webhooks de `/api/v1/webhooks/*`:

- Rate-limit **120 peticiones/minuto por IP** (`@limiter.limit("120/minute")` en
  `api/webhooks.py`), con contador compartido en Redis.
- Rechazo si `Content-Length > 1 MB`.
- Idempotencia por `provider_message_id` con guard atómico en Redis: si el
  proveedor reintenta, el mensaje no se procesa dos veces.

El chat web tiene sus propios límites, más bajos, porque su clave es pública:
10 sesiones/min, 30 mensajes/min, 60 consultas de historial/min y un tope de
2000 mensajes al día por instalación.

#### 2.3.d Chat web: cómo se crea y dónde va la lista de dominios

Este canal no existe hasta que lo creas. Se hace desde el panel:

1. **Conexiones → pestaña Servicios → tarjeta "Chat en tu web" → "Crear el chat web".**
   Eso genera la `api_key` y el trozo de código que pegas en tu web.
2. En esa misma tarjeta, **"Código y dominios"**, tienes el campo **Dominios
   permitidos**: uno por línea, acepta `ejemplo.com` y `*.ejemplo.com`.
   Guarda con "Guardar dominios".

**Si dejas la lista vacía, el chat funciona en cualquier web que pegue el
código.** No es un fallo, es el valor por defecto: la clave viaja en el HTML, así
que cualquiera puede copiarla del código fuente de tu página y montar el chat en
la suya. El agente contestará, y las llamadas al modelo las pagas tú. Rellena la
lista antes de publicar el widget.

La comprobación se hace contra la cabecera `Origin` (y `Referer` si no hay
`Origin`) en cada creación de sesión y en cada mensaje; si no encaja, **403**.
Una petición sin ninguna de las dos cabeceras también se rechaza, salvo que la
lista esté vacía.

Si la clave se te queda pegada en un sitio del que quieres sacarla, el botón de
rotarla está en la misma tarjeta: corta las sesiones abiertas y obliga a
actualizar el código en todas las webs donde esté puesto.

### 2.4 Servidor MCP y tokens de agente

Desde la 1.2.0 el servidor MCP lo sirve la propia API en **`/mcp`**, en el
mismo dominio que el panel (`backend/app/mcp_server.py`; el nginx del panel lo
reenvía). Ya no es un servicio aparte: **está en internet en toda instalación**,
igual que `/api`. Sirve para que un asistente (Claude Code, n8n u otro cliente
MCP) mire la instalación y trabaje sobre ella sin entrar al panel.

Lo que hay que tener claro:

- **El servidor MCP no guarda ninguna clave y no autentica nada por su cuenta.**
  Cada herramienta coge la cabecera `Authorization: Bearer …` de la petición y
  se la pasa tal cual a `/api/v1/agent-api`, por dentro del contenedor. Quien
  decide qué se puede hacer es la agent-api, a partir del token: los mismos
  ámbitos, el mismo límite y la misma auditoría que por HTTP.
- **Sin token no hace nada.** Cualquiera puede listar las herramientas (sus
  nombres y descripciones están en este repositorio), pero ninguna llama a la
  API sin un token válido. No existe un token «de reserva» en el servidor.
- Los identificadores que recibe tienen que ser UUID: una herramienta no puede
  usarse para llamar a otra ruta de la API desde dentro.
- Sin estado ni sesiones: cada petición se atiende sola. El cuerpo se limita a
  4 MB y el límite general por IP de la API también cuenta.
- La comprobación de `Host`/`Origin` del paquete MCP (contra el «DNS
  rebinding») está apagada a propósito: protege servidores sin autenticación en
  localhost, y aquí rechazaría el dominio de cada instalación. Lo que protege
  este endpoint es el token, que no viaja en cookies.
- **Los tokens de agente se crean en el panel**, en Conexiones → API / MCP.
  Empiezan por `agt_`, se enseñan **una sola vez** y en la base de datos solo
  queda su hash SHA-256. Si lo pierdes, se crea otro; no se recupera.
- Cada token lleva **ámbitos**. Estos son todos los que hay:

| Ámbito | Qué permite | Lectura / escritura |
|---|---|---|
| `monitor:read` | Estado, salud, canales y coste de modelo | Lectura |
| `interactions:read` | Listado de conversaciones y sus metadatos | Lectura |
| `kb:read` | Leer y buscar en la base de conocimiento | Lectura |
| `kb:write` | Crear y sobrescribir documentos de la base de conocimiento | **Escritura** |
| `prompts:write` | Listar agentes y **sobrescribir su prompt de sistema** | **Escritura** |
| `classifier:write` | Leer y cambiar las instrucciones del clasificador de no deseados y sus reglas duras | **Escritura** |
| `conversations:read` | Leer el hilo de una conversación (n8n) | Lectura de **contenido** |
| `conversations:reply` | Contestar en una conversación o pasarla a una persona (n8n) | **Escritura de contenido** |
| `contacts:write` | Cambiar nombre, correo, etapa y etiquetas de una ficha (n8n) | **Escritura de datos personales** |

> **Los tres de n8n son los únicos que tocan contenido.** Existen para «Sofía en
> n8n» (Fase 11): un flujo de n8n que hace de cerebro de un agente lee el hilo,
> contesta, pasa a una persona y actualiza la ficha. Quien tenga un token con
> ellos puede leer y escribirle a tus pacientes. Dalos solo al n8n de un agente
> (preset «Para n8n»), en un token propio, y revócalo si dejas de usarlo.
> Límites que no se saltan:
> - nunca contesta en una conversación que tiene una persona;
> - nunca con la ventana de 24 h cerrada;
> - nunca cambia el teléfono.
>
> Todo queda auditado con el token.

> **`prompts:write` es el ámbito peligroso.** Con él, quien tenga el token puede
> reescribir por completo lo que tu bot le dice a tus clientes, desde fuera y sin
> pasar por el panel. La capa fija de seguridad (§ 2.12) sigue por delante y no se
> puede quitar por ahí, pero todo lo demás sí: el tono, los precios que afirma,
> a quién deriva. Si el token se filtra, tu bot es de quien lo tenga.

> **`classifier:write` es el ámbito silencioso.** No cambia lo que el bot dice:
> cambia a quién **deja de contestar**. Quien lo tenga puede escribir una
> instrucción o una regla dura que mande a cuarentena a un cliente real, y nadie
> se entera —la conversación se queda retenida hasta que alguien la libera a mano
> en el panel—. Va aparte de `prompts:write` justo por eso. El modelo, el
> encendido y la acción sobre Gmail no se tocan por esta vía: siguen siendo solo
> del panel.

**Cómo usarlo con cabeza:**

- Da a cada token **solo los ámbitos que necesite**. Para vigilar la instalación
  sobra con `monitor:read` e `interactions:read`. No repartas `prompts:write`
  "por si acaso".
- **Ponles caducidad** al crearlos. Si no la pones, el token no caduca nunca.
- **Si no vas a conectar ningún agente, no crees ningún token.** `/mcp` contesta
  igual, pero sin token ninguna herramienta hace nada. Es la opción por defecto.
- Revoca el token en cuanto deje de hacer falta. Cada escritura queda en
  `/admin/audit` con el token que la hizo.

Lo que sí trae de serie: 120 peticiones/minuto por token (HTTP 429 al pasarse) y
auditoría de todas las escrituras. Lo que **no** trae: lista blanca de IPs.
El token es lo único que separa a un desconocido de tu prompt.

### 2.5 SSRF en descarga de audios
- Allowlist de hosts (`*.ycloud.com`; por Zernio, `*.fbcdn.net`, `*.fbsbx.com`,
  `*.cdninstagram.com` y `zernio.com`, en `providers/zernio/media.py`, que
  además comprueba el host final tras cada redirección).
- HTTPS obligado, `follow_redirects=False`.
- Resolución DNS comprobada: rechazo si apunta a IP privada/loopback/link-local.

### 2.6 Subida de documentos (KB)
- Sólo admin.
- Filename saneado a basename, longitud máxima.
- Validación de magic bytes (`%PDF-`, `PK\x03\x04`, UTF-8 para texto plano).
- Tamaño máximo 25 MB.
- Storage path resuelto y validado para evitar traversal.

### 2.7 Servir audios
- Endpoint `/audios/{filename}` autenticado.
- Path resuelto con `Path.resolve()`, debe estar bajo `_AUDIO_ROOT`.
- Frontend descarga vía `fetch` con `Authorization` y crea blob URL (el `<audio>` no puede llevar headers).

### 2.7b Herramientas de n8n y archivos que manda Sofía (Fase 11)
Conexiones → n8n deja dar de alta flujos de n8n como herramientas de Sofía
(`api/n8n.py`, `services/n8n_tools.py`).
- **Solo administradores** dan de alta, ven o regeneran herramientas. Ver el
  secreto entero queda en la auditoría (`n8n_tool.secreto_visto`).
- **Secreto por herramienta**: lo genera el CRM (`secrets.token_urlsafe(32)`),
  va cifrado en reposo (`EncryptedText`) y viaja en la cabecera
  `X-Omnicanal-Token`. No es HMAC a propósito: es lo que n8n comprueba sin código
  (Header Auth), y la llamada va por HTTPS.
- **SSRF**: solo URLs `https://` cuyo host resuelve a una IP pública, al guardar
  y en cada llamada; sin redirecciones (el secreto no viaja a otra URL).
- **Datos del paciente**: a n8n le llegan los ids, el canal y solo los datos que
  declara la herramienta. Nombre, teléfono y correo solo si el administrador
  marca «Mandar a n8n el nombre, el teléfono y el correo del paciente». Los
  datos marcados como sensibles no van a la traza.
- **Lo que contesta n8n es dato**: le llega a Sofía envuelto con un aviso de que
  no son instrucciones, recortado a 8 KB, y sin los enlaces de los archivos.
- **Escritura sin confirmar**: no se reintenta y la conversación pasa a una
  persona (Sofía no puede dar por hecho lo que no sabe).
- **Archivos** (`services/archivos_salientes.py`):
  - se bajan con la misma regla SSRF, con tope de tamaño por tipo y leyendo por
    trozos;
  - se rechaza lo que parece HTML, SVG, XML, script o ejecutable, y lo que no
    coincide con el tipo anunciado.
- **Enlace público firmado** (`GET /api/v1/media/p/{token}`, para Zernio y el
  widget):
  - HMAC-SHA256 con una llave derivada de `JWT_SECRET`, sobre el nombre del
    archivo y la caducidad (1 h);
  - sin listado; si el token es malo o caducó, 404;
  - `nosniff` y la ruta resuelta dentro de `UPLOADS_PATH`.

  Quien tenga el enlace puede bajar ese archivo mientras no caduque: es lo que
  ya pasa con el archivo que recibe el paciente.
- **Widget**: pinta los archivos con propiedades del DOM (nunca `innerHTML`) y
  solo desde enlaces `/api/v1/media/p/`.

### 2.7c Mensajes que Sofía manda sola (1.3.0)

Recordatorio de cita, «no llegó» y retomar a quien no agendó
(`services/seguimientos.py`, `services/retomar.py`). Son los únicos mensajes que
salen sin que el paciente acabe de escribir, así que tienen sus propios frenos:

- **Apagados de fábrica.** Los enciende un administrador en Asistente →
  Seguimientos, y queda en Auditoría (`seguimientos.updated`).
- **Nunca** a quien pidió la baja (`outbound_optout`) ni a un número bloqueado;
  nunca con el asistente apagado, el canal en pausa (también la del tope de
  gasto) o en Entrenamiento; nunca fuera de la franja horaria (8:00–20:00 por
  defecto, en la hora del negocio).
- **Una vez por cita o por conversación.** Se apunta en la fila antes de mandar
  (`UPDATE … WHERE … IS NULL`) y WhatsApp recibe una clave de idempotencia.
- **Meta:** fuera de la ventana de 24 h solo plantilla aprobada (WhatsApp); en
  Instagram y Messenger, solo dentro de las 24 h y **nunca** con la etiqueta
  HUMAN_AGENT, que Meta prohíbe para mensajes automáticos.
- **Datos en la plantilla:** solo los de una lista cerrada (nombre de pila,
  negocio, fecha, hora y servicio de la cita). Nada de notas, NIF ni dirección.
- **Retomar** le pasa la conversación al modelo del agente (los últimos 20
  mensajes), igual que un turno normal: no sale del proveedor de IA que ya usa
  Sofía.

### 2.8 Salida de servidor
- Sin Swagger/Redoc/openapi.json en producción.
- `/admin/health` y `/admin/credentials/{key}/test` devuelven mensajes genéricos; el detalle real va al log estructurado.
- Errores no controlados → `{"code":"INTERNAL_ERROR"}` sin stack.

### 2.9 Cabeceras HTTP (nginx)
- `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy` bloqueando cámara/micro/geolocalización.
- CSP estricta: `script-src 'self'`, `frame-ancestors 'none'`, `object-src 'none'`. `connect-src 'self' https: wss:` (se puede restringir a dominio fijo cuando se conozca en build-time).

### 2.10 Logging
- structlog JSON, en la API y también en el worker y el beat.
- Scrubber recursivo (dicts y listas) de teléfono, email, claves `sk-*` (también
  `sk-proj-…` y `sk-ant-…`), Bearer tokens y JWTs.
- Los registros de accesos (el nginx del panel y uvicorn) apuntan la ruta sin la
  query string: por ahí viajan el enlace para restablecer la contraseña, el token
  del chat de la web y lo que se busca en Clientes.
- Un error de la base no lleva los valores de la consulta (`hide_parameters`), y
  un traceback no vuelca las variables locales.
- Lo que el filtro no reconoce (nombres, texto libre) no se tapa: por eso ningún
  evento registra a propósito el texto de un mensaje.

### 2.11 Contenedor
- Backend corre como usuario `app` (uid 1000), no root.
- Directorios de datos (`/data/audios` y su subcarpeta `/data/audios/kb_docs`)
  propiedad de `app`. Los documentos de la base de conocimiento cuelgan del
  volumen de audios a propósito: es el único montado en `app` y en `worker`, y
  `worker` tiene que leer lo que `app` acaba de escribir
  (`backend/app/api/knowledge_base.py`).

### 2.12 Endurecimiento adicional
- **Capa de seguridad fija anti prompt-injection** antepuesta al prompt de TODOS
  los canales, no editable desde el panel (`runtime_config.SECURITY_GUARD`).
- **Rate-limit con storage Redis compartido** (límite real con N réplicas) e IP
  de cliente derivada del proxy de confianza (`TRUSTED_PROXY_COUNT`), no del
  primer `X-Forwarded-For` falsificable.
- **WebSocket de voz (Retell)** valida el `call_id` contra la API de Retell antes
  de atender (`RETELL_WS_VALIDATE`).
- **Fallback LLM**: la `base_url` del gateway se valida (https + rechazo de IP
  internas) → anti-SSRF/exfiltración.
- **Idempotencia de webhooks** con guard atómico Redis (evita doble proceso por
  reintentos del proveedor).
- **Outbound** respeta la blocklist; **secretos de canal** enmascarados en las
  respuestas de la API; **PII redactada** en las trazas del agente.
- `JWT_SECRET` por defecto inseguro → en no-producción se genera uno aleatorio;
  en producción `validate_production` aborta el arranque.
- Import CSV con tope de tamaño; export con neutralización de fórmulas + auditoría.

Pendiente conocido: el JWT del panel viaja en el navegador, no en una cookie
`httpOnly`.

---

## 3. Defensas contra abuso del agente

Valen para cualquier canal de entrada, no solo para WhatsApp: quien escribe al
bot es siempre alguien de fuera.

| Vector | Defensa | Archivo |
|---|---|---|
| Suplantación de contacto vía `crear_actualizar_contacto("telefono":"+34X")` | El teléfono se fuerza al de `ctx` (webhook), se ignora el de args, intento se notifica. | `agents/tools/contact_upsert.py` |
| Fuga PII vía `buscar_contacto(email="jefe@empresa.com")` | La tool no acepta parámetros, solo devuelve datos del propio contacto. | `agents/tools/contact_lookup.py` |
| Quedarse con la ficha de otro paciente diciendo su teléfono por Instagram («mi número es el +52…») | El teléfono dicho va a `telefono_declarado`, nunca a la llave, y solo genera una PROPUESTA de unir fichas. Unir (o descartar) lo decide un administrador desde el panel; ninguna fusión se hace sola con un dato que no verifica el canal. Hasta entonces, `buscar_contacto` sigue devolviendo solo la ficha de la conversación. | `agents/tools/contact_upsert.py`, `services/identity_linking.py`, `api/fusiones.py` |
| Dump del KB | `top_k≤8`, query ≤500 chars, contenido truncado a 1200 chars. | `agents/tools/kb_search.py` |
| Spam al canal del equipo vía `derivar_humano` | Cooldown 10 min por conversación, idempotente si ya derivada/cerrada, motivo/resumen escapados HTML y truncados. | `agents/tools/human_handoff.py` |
| DoS económico OpenAI | 10 msg/min · 60 llamadas LLM/h por contacto. Mensajes truncados a 4000 chars antes del LLM. Audios > 8 MB rechazados. | `services/agent_guardrails.py`, `services/audio_processor.py` |
| Blocklist persistente | 5 infracciones de rate-limit en 10 min → bloqueo **automático de 1 hora** (`AUTO_BLOCK_TTL_SECS`). El bloqueo **manual** desde el panel dura 24 h (`BLOCKLIST_TTL_SECS`), porque ahí decide una persona. Visible y desbloqueable en `/admin/blocklist`. | `services/agent_guardrails.py`, UI `BlocklistPage.tsx` |
| Contenido tóxico/jailbreak | API de moderación de OpenAI (`omni-moderation-latest`) antes del LLM. Si se marca → no se contesta, pasa a humano, alerta al canal. **Solo funciona si hay `openai_api_key`**, aunque uses otro proveedor de IA: ver [§ 9](#9-proveedor-de-ia-y-moderación-de-contenido). | `services/moderation.py` |
| Prompt injection | (a) Capa fija de seguridad antepuesta al prompt por el sistema, no editable desde el panel. (b) Prompt plantilla de los agentes con reglas explícitas. (c) Defensa en código: las tools no obedecen al LLM si intenta saltarse el contrato. | `services/runtime_config.py:SECURITY_GUARD`, seed `PROMPT_PLANTILLA_TEXTO` / `PROMPT_PLANTILLA_VOZ`, todas las tools |
| Inyección vía los avisos al equipo | Los avisos ya no salen de la aplicación: van al panel y a la notificación push del operador, **en texto plano**, con motivo y resumen truncados. No hay `parse_mode` HTML que envenenar y el panel los pinta como texto, no como marcado. | `agents/tools/human_handoff.py`, `services/security_alerts.py` |
| Iteración runaway del agente | `MAX_ITERATIONS=5` en el orchestrator. | `agents/orchestrator.py` |

### Alertas de seguridad

`services/security_alerts.notify_security(kind, title, details, throttle_key)`
publica el aviso **en el panel**, en **Sistema → Logs en vivo**, con
prefijo `[Seguridad]`. Throttling 10 min por `(kind, throttle_key)` para no
inundar.

Se dispara automáticamente en:

- `phone_override` — intento de suplantación de teléfono
- `audio_too_large` — audio rechazado por tamaño
- `llm_rate_limited` — rate-limit LLM excedido
- `moderation_flagged` — contenido marcado por moderación
- `phone_blocked` — contacto añadido a blocklist

**No hay integración con Telegram ni con Slack: se retiraron del producto.** Los
avisos no salen de la aplicación (LFPDPPP/RGPD: no se exfiltra nombre, teléfono ni
resumen del cliente a plataformas de terceros). Eso significa que **hay que
entrar a mirar el panel**: nadie te va a avisar al móvil salvo por la
notificación push de la PWA, que solo cubre lo del operador (derivaciones y
copias de seguridad), no todas estas alertas. Ponlo en tu rutina.

---

## 4. PII en reposo

| Campo | Cifrado | Motivo |
|---|---|---|
| `Message.audio_transcript` | Sí — Fernet (`EncryptedText`) | Voz del cliente, alta sensibilidad. |
| `Contact.notas_internas` | Sí — Fernet | Notas del operador, puede incluir datos médicos/sensibles. |
| `Conversation.resumen` | Sí — Fernet | Resumen humano de la conversación. |
| `ContactNote.texto` | Sí — Fernet | Notas de la ficha. Incluye el resumen automático de cada llamada, que puede traer datos de salud («me duele una muela»). Lo escribe el modelo del Agente de Voz a partir de la transcripción: la llamada no sale a ningún proveedor nuevo. |
| `Appointment.notas` | Sí — Fernet | Notas de la cita (motivo, síntomas): datos de salud. |
| `Appointment.servicio`, `starts_at` | No — en claro | La agenda los necesita para buscar, ordenar y evitar citas encimadas. El servicio también puede decir algo de salud («Endodoncia»): por eso las citas entran en la exportación ARCO y se borran con el contacto (`ON DELETE CASCADE`), y su copia en Google Calendar también (ver debajo). |
| `ScheduleBlock`, `OfferedSlot` | No hay PII | Tramos cerrados (con un motivo libre) y horarios ofrecidos a una conversación; estos se purgan a las 24 h. |
| `Contact.telefono` | No — en claro | Clave de búsqueda + UNIQUE. Cifrarla rompe `WHERE telefono = ...`. |
| `Contact.email`, `Contact.nombre` | No — en claro | Necesarios para búsqueda LIKE en `/contacts`. |
| `Contact.telefono_declarado`, `Contact.identidad_canal`, `MergeSuggestion.dato` | No — en claro | Hay que buscarlos por igualdad para proponer y para emparejar los mensajes con la ficha unida (Fase 10.1). Son el mismo tipo de dato que `telefono` y `email`; entran en la exportación ARCO y se van con el contacto al borrarlo. |
| `Message.contenido` | No — en claro | Alto volumen, se renderiza en cada vista. |
| Credenciales de proveedor | Sí — Fernet | Cifrado obligatorio desde día 1. |

**Copia en Google Calendar (Fase 9).** Si hay una cuenta de Google Calendar
conectada, cada cita se copia al calendario del negocio con el servicio y el
nombre de la persona (sin teléfono, sin notas y sin invitarla: Google no le
escribe). Es el mismo dato que ya ve el equipo en ese calendario; con
`calendar.google_sync = off` no sale nada.

**Borrar a un paciente borra también esa copia (1.3.3).** Antes de que el
borrado se lleve las citas, apunta cada evento en `calendar_event_deletions`
(solo el id del evento y el del contacto borrado, ningún dato personal), y una
tarea lo borra en Google en cuanto se confirma. Si Google está caído o el
permiso revocado, la barrida lo reintenta cada 10 minutos. Mientras quede un
evento, el informe del borrado en `/admin/audit` (`contact.data_erased`) dice
`complete: false` y cuántos faltan; al terminar queda `contact.calendar_erased`.

**Adjuntos del correo IMAP.** IMAP no deja pedir un adjunto después, así que la
ingesta lo guarda en `uploads/` al llegar y lo apunta en la ficha del mensaje
(`extra.attachments`, `local: true`). Desde la 1.3.3 entran en el borrado del
contacto y en la retención de adjuntos (`MEDIA_RETENTION_DAYS`, 180 días), igual
que los demás archivos. Los de Gmail no se guardan en el servidor: se piden a
Gmail cuando alguien los abre.

**Copias antes de cada actualización (Fase 10.3).** `omni.py update` deja en
el servidor, antes de tocar nada, una copia completa de la base en
`~/omnicanal-respaldos/<dominio>/` (carpeta 700 y archivos 600 del usuario de
SSH). Es SQL comprimido con todo lo que hay en la base: teléfonos, correos,
nombres y mensajes van en claro; lo cifrado (notas, transcripciones,
credenciales) sigue cifrado dentro y sin `ENCRYPTION_KEY` no se lee. No sale del
servidor ni se sube a ningún sitio. Se guardan las cinco últimas: la sexta
borra la más vieja. Un contacto borrado por una solicitud ARCO sigue en esas
copias hasta que roten; si tiene que desaparecer ya, borra la carpeta.
`omni.py uninstall` no la toca: al dar de baja una instalación, bórrala a mano.

**Dónde vive la llave de cifrado (Fase 10.3b).** En la instalación en un clic
la genera Coolify (`SERVICE_PASSWORD_64_CIFRADO`) y se queda en las variables
del service. El panel la enseña una sola vez, al crear el administrador, y no
deja entrar hasta que el dueño confirma que la guardó en su gestor de
contraseñas. La copia de Coolify sirve mientras exista ese servidor; para
restaurar una copia de la base en otro, hace falta la del gestor. `omni.py` no
la guarda en ninguna computadora. En las instalaciones anteriores la generaba
`omni.py secrets` y está en `~/.omnicanal/<dominio>.env` (hasta la 0.11.0, en
`.instalacion/`, junto al script), con permisos 600.

**Trade-off consciente**: cifrar a nivel campo `telefono/email/nombre` requiere HMAC determinista para búsqueda exacta + Fernet para almacenamiento. Es trabajo serio y rompe búsquedas LIKE.
La protección para esos campos depende de:
1. **Cifrado del volumen del disco** del proveedor (EasyPanel / VPS provider).
2. **Cifrado de las credenciales de BD** (`POSTGRES_PASSWORD` no se reutiliza).
3. **Acceso restringido al panel admin** (JWT + bcrypt + auditoría).

Si necesitas cifrado a nivel campo de teléfono/email, dejar como evolución futura.

---

## 5. Auditoría

`audit_log` registra:

- `auth.login.success`, `auth.login.failed`, `auth.logout`
- `user.created`, `user.updated`, `user.deleted`, `user.password_reset`
- `credential.created`, `credential.updated`
- `agent_config.activated`, `agent.prompt_update`
- `kb.note.created`, `kb.document.edited`
- `agent_token.created`, `agent_token.revoked`
- `phone.unblocked`
- `settings.calendar_updated`, `agenda.bloqueo_creado`, `agenda.bloqueo_borrado`
- `contacts.merged` (con `propuesta` si salió de «¿Es el mismo paciente?»),
  `contacts.merge_suggestion_dismissed`

Las citas no van a `audit_log` sino a la actividad de la ficha
(`appointment_booked`, `_rescheduled`, `_cancelled`, `_edited`, `_marked`), con
quién la hizo: el asistente (sin autor) o la persona del panel.

Cada entrada lleva `user_id`, `ip`, `user_agent`, `before/after` JSONB. Visible en `/admin/audit`.

Lo que hace un cliente MCP también queda aquí: las escrituras hechas con un token
de agente se registran sin `user_id` y con el token que las hizo dentro de
`after`. Si algún día te cambia el prompt del bot sin haberlo tocado tú, es donde
hay que mirar.

**Retención: 365 días.** Una tarea diaria (`backend/app/tasks/purge_internal_logs.py`,
04:10) borra por lotes las entradas más viejas que `AUDIT_RETENTION_DAYS`, que
por defecto son 365 (`backend/app/core/config.py`). En la misma pasada se
purgan las trazas del agente (`TRACE_RETENTION_DAYS`, 30 días) y el registro de
uso de LLM (`LLM_USAGE_RETENTION_DAYS`, 90).

**Si tu sector te obliga a conservar la auditoría más de un año, decídelo
antes, no después.** El panel no tiene botón de exportar auditoría, y las copias
de seguridad tampoco te salvan: se guardan por semanas, no por años, así que
para cuando eches en falta una fila de hace 400 días no quedará ninguna copia
que la contenga. Las dos salidas, las dos previas: subir
`AUDIT_RETENTION_DAYS` en las variables de entorno del despliegue, o sacar la
tabla `audit_log` a un fichero de vez en cuando (`pg_dump -t audit_log`) y
guardarlo fuera del servidor.

---

## 6. Checklist pre-producción

Esto es lo que hay que hacer **antes de abrir el chatbot a clientes reales**, una
vez, en una instalación nueva. El paso a paso de la instalación en sí está en
[`docs/avanzado/easypanel.md`](./docs/avanzado/easypanel.md); aquí solo está lo que, si te lo
saltas, deja la instalación abierta.

### 6.1 Genera tus secretos y no los metas en el repositorio

**En la instalación en un clic no generas nada.** Con `deploy/coolify.compose.yml`
Coolify crea la contraseña de la base, `JWT_SECRET`, la llave de cifrado y el
código de arranque como variables mágicas, una vez, al crear el service. Lo que
te toca es guardar la llave de cifrado cuando el panel te la enseña (§ 4, «Dónde
vive la llave de cifrado»). Lo que sigue es para los despliegues en los que
pones tú los secretos (EasyPanel, un VPS sin Coolify).

`JWT_SECRET`, `ENCRYPTION_KEY`, la contraseña de Postgres y la del administrador
inicial se generan una vez y van en las **variables de entorno del despliegue**,
nunca en un fichero que suba a git.

```bash
openssl rand -hex 24      # POSTGRES_PASSWORD (hex, no base64: viaja dentro de DATABASE_URL)
openssl rand -base64 48   # JWT_SECRET
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"   # ENCRYPTION_KEY
```

Los mismos valores van en `app`, `worker` y `beat`. Si no coinciden, unos
servicios no podrán descifrar lo que escriben los otros.

- **Si no lo haces:** con los valores de ejemplo, cualquiera que tenga este
  código puede firmarse un token de sesión válido y entrar al panel.
- **Guarda la `ENCRYPTION_KEY` en un gestor de contraseñas.** Cifra las
  credenciales de proveedor, las claves de los canales de Instagram y Retell, y
  las copias de seguridad. Si la pierdes, no se recupera nada de eso.
- Si algún día se te filtra alguno, rótalo. Cambiar `ENCRYPTION_KEY` obliga a
  volver a meter a mano todas las credenciales desde el panel.

### 6.2 Despliega con los ficheros de producción, no con los de desarrollo

| Producción | Desarrollo local — **no lo uses para desplegar** |
|---|---|
| `docker-compose.easypanel.yml` | `docker-compose.yml` |
| `.env.example` | `.env.desarrollo.example` |

- **Si te equivocas de fichero:** `.env.desarrollo.example` trae `APP_ENV=development`, y con
  eso `validate_production()` (`backend/app/core/config.py`) **no comprueba
  nada**. Arranca tan contento con el `JWT_SECRET` de fábrica, con URLs a
  localhost y con el usuario administrador de ejemplo que está escrito en este
  mismo repositorio. Resultado: una instalación abierta a internet en la que se
  entra con unas credenciales públicas.
- Comprueba después del despliegue que el servicio `app` tiene
  `APP_ENV=production` en su pestaña Environment. Es la comprobación de treinta
  segundos que evita el problema entero.

### 6.3 Deja que el primer arranque haga su trabajo

El `entrypoint.sh` del servicio `app` ejecuta, en este orden:

1. `alembic upgrade head` — crea el esquema y aplica las migraciones.
2. `python -m app.scripts.seed` — crea el usuario administrador inicial y siembra
   los dos agentes (**Agente de Texto** y **Agente de Voz**) con su prompt
   plantilla. Con `SETUP_CODE` y sin `INITIAL_ADMIN_*` propios siembra todo
   menos el administrador (`seed.admin.en_primer_arranque`): lo crea el dueño
   desde el panel con el código de arranque (§ 2.1).

El seed es *create-only*: en una instalación que ya funciona no pisa nada de lo
que hayas escrito.

Si el arranque se para, léelo en los registros antes de tocar nada. Las causas
habituales son a propósito:

- `JWT_SECRET` vacío, por defecto o de menos de 32 caracteres.
- `ENCRYPTION_KEY` vacía o sin formato Fernet válido.
- `INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD` con los valores de ejemplo
  (sin `SETUP_CODE`; con él, el código tiene que tener 16 caracteres o más).
- `APP_BASE_URL` o `FRONTEND_BASE_URL` apuntando a `localhost`.

### 6.4 Entra, cambia la contraseña y pon tus claves

Si el administrador vino en las variables, el primer login es con
`INITIAL_ADMIN_EMAIL` / `INITIAL_ADMIN_PASSWORD`. Si lo creaste en el panel con
el código de arranque, la contraseña ya es tuya: pasa al punto 2.

1. **Cambia la contraseña del administrador** en `/admin/users`. La inicial ha
   pasado por las variables de entorno y por tus notas: no vale como definitiva.
2. Pon las claves en `/admin/connections`, pestaña **Servicios**. Se cifran con
   Fernet antes de guardarse. Qué pasa si falta cada una:
   - **Clave de IA** (OpenAI, Anthropic o Gemini) → el agente no contesta.
   - **`openai_api_key`** → además, **la moderación de contenido queda apagada**
     aunque tengas otro proveedor funcionando. Léete la [§ 9](#9-proveedor-de-ia-y-moderación-de-contenido)
     antes de decidir.
   - **Claves del canal que vayas a usar** → sin ellas ese canal no opera.
3. **Las alertas de seguridad no se configuran: no hay nada que poner.** Se ven
   en **Sistema → Logs en vivo** y no salen de la aplicación (Telegram y
   Slack se retiraron del producto). Lo que hay que poner de tu parte es el
   hábito: mirar esa pantalla. Es lo que te avisa de que alguien está abusando
   del bot.

### 6.5 Rellena el prompt de los dos agentes

Con el perfil `dental-mx` (el de la instalación por defecto, `SEED_PROFILE`), los
dos agentes ya vienen escritos para la clínica del curso y solo quedan dos
huecos en **Ajustes → Sobre el negocio**: el teléfono y la dirección reales. Los
precios de la base de conocimiento son de ejemplo y se editan desde el panel.

Con el perfil `blank`, los agentes se siembran con una **plantilla**: las reglas
de comportamiento están escritas y funcionan, pero los datos del negocio vienen
vacíos y marcados con `{{PENDIENTE: …}}`.

Ve a **`/admin/agent/agents`**, abre cada agente y complétalo. El detalle del qué
y el cómo está en [`docs/avanzado/easypanel.md` § 5.b](./docs/avanzado/easypanel.md#5b-personaliza-el-prompt-de-tus-agentes-obligatorio-antes-de-abrir).
Los prompts plantilla son `PROMPT_PLANTILLA_TEXTO` y `PROMPT_PLANTILLA_VOZ`, en
`backend/app/scripts/seed.py`.

**No copies ni pegues ningún bloque de reglas de seguridad.** La capa fija
(`services/runtime_config.py:SECURITY_GUARD`) la antepone el sistema a cada
llamada al modelo, en todos los canales, y no se puede quitar desde el panel.
Pegarla a mano en el prompt solo consigue duplicarla y gastar contexto.

- **Si no lo haces:** el agente contestará a tus clientes con los marcadores
  `{{PENDIENTE: …}}` dentro del mensaje. El checklist de Inicio del panel no da
  el paso por hecho hasta que no queda ni un marcador en ningún agente activo ni
  en el bloque común de Ajustes.

### 6.6 Conecta y protege los canales que vayas a abrir

Solo los que vayas a usar. Para cada uno: crea el canal en **Conexiones**, pega
su clave, y copia la dirección del webhook al panel del proveedor. Las
direcciones, los secretos y la comprobación de firma de los seis canales están en
la tabla de la [§ 2.3](#23-canales-de-entrada-quién-entra-y-qué-lo-protege).

Dos que se olvidan:

- **Chat web**: rellena la lista de **Dominios permitidos** antes de publicar el
  widget. Vacía significa que cualquier web puede usar tu agente a tu costa
  ([§ 2.3.d](#23d-chat-web-cómo-se-crea-y-dónde-va-la-lista-de-dominios)).
- **`TRUSTED_PROXY_COUNT`**: ponlo explícitamente. Mal puesto, o deja el límite
  de intentos de login inservible, o deja a todo el mundo fuera del panel
  ([`docs/avanzado/easypanel.md` § 10](./docs/avanzado/easypanel.md#10-trusted_proxy_count-cuántos-saltos-hay-hasta-tu-backend)).

  Con la instalación de un solo dominio (Coolify), cada petición a la API pasa
  por **dos** proxies: Traefik, que termina el TLS, y el nginx del panel, que
  reenvía `/api`, `/widget.js`, `/audios` y `/health` (`frontend/nginx.conf`).
  El valor es **`2`**, y el compose de Coolify ya lo fija. Si además pones
  Cloudflare con proxy (nube naranja) delante, son **`3`**. En el código el
  valor por defecto sigue en `1`, que es lo correcto para `docker compose` en
  local, donde se llega a la API sin proxy delante o con uno solo.

### 6.7 Tokens de agente y servidor MCP: solo si los vas a usar

El servidor MCP va en `/mcp` del dominio del CRM en toda instalación, y sin un
token no hace nada. Si no vas a conectar ningún asistente, **no crees ningún
token**. Es la opción por defecto.

Si los vas a usar, crea cada token en Conexiones → API / MCP con **los ámbitos
mínimos y con caducidad**. `prompts:write` deja reescribir lo que tu bot dice a
tus clientes desde fuera del panel: no lo repartas por comodidad
([§ 2.4](#24-servidor-mcp-y-tokens-de-agente)).

### 6.8 Apaga el despliegue automático

Tal y como sale de fábrica, EasyPanel despliega en cuanto ve un cambio en `main`,
sin esperar a nada. La revisión automática del repositorio corre en paralelo, así
que **cuando se pone en rojo, la versión rota ya va camino del servidor**.

Los dos ajustes que lo convierten en una barrera de verdad —proteger `main` en
GitHub y apagar el auto-despliegue en EasyPanel— están explicados paso a paso en
[`docs/avanzado/easypanel.md` § 7.b](./docs/avanzado/easypanel.md#7b-los-dos-ajustes-que-faltan-y-que-solo-puedes-hacer-tú).
Son diez minutos y se hacen una vez.

- **Si no lo haces:** cualquier cambio que rompa los tests llega igual a
  producción, y te enteras por los clientes.

### 6.9 Verifica antes de abrir

- `curl https://<api>/health` → `{"status":"ok","db":"ok","redis":"ok","worker":"ok","worker_last_seen_seconds":<n>}`.
  Si `worker` dice `caido`, mira primero el contenedor `beat`; el código sigue
  siendo 200 a propósito.
- En `/admin/health`: base de datos, Redis y motor de tareas en verde.
- Login con la contraseña **nueva**, no con la inicial.
- Manda un mensaje de prueba por cada canal que hayas abierto y comprueba que
  llega al inbox y que el agente contesta sin marcadores `{{PENDIENTE`.
- `/admin/audit` debe mostrar tu login y los cambios que acabas de hacer.
- Programa las copias de seguridad y haz una a mano para comprobar que sale en
  verde (panel → **Copias de seguridad**).

### 6.10 Al acabar la revisión de Meta, borra la cuenta de revisor

Solo aplica si has pedido los permisos de Instagram y has creado una cuenta con
rol `revisor` para que Meta entre a mirar ([§ 1](#el-tercer-rol-revisor-cuenta-temporal-para-meta)).

Esa cuenta **se crea para una revisión y se borra al acabar**. En cuanto Meta te
comunique el resultado —lo aprueben o no—, entra en `/admin/users` y elimínala.
Mientras exista, alguien de fuera de tu empresa tiene una contraseña que abre la
bandeja de Instagram de tus clientes reales.

- **Si no lo haces:** te queda una cuenta activa, con acceso a conversaciones
  reales, en manos de una persona que ya no tiene ningún motivo para tenerla, y
  que no vas a volver a mirar. El borrado queda en `/admin/audit` como
  `user.deleted`.
- Comprueba también, en la misma pantalla, que no hay más cuentas `revisor` de
  revisiones anteriores. Se acumulan sin hacer ruido.

---

## 7. Operación día a día

| Cosa | Dónde |
|---|---|
| Ver eventos sensibles | `/admin/audit` |
| Ver contactos bloqueados | `/admin/blocklist` |
| Cambiar credencial de proveedor | `/admin/connections`, pestaña Servicios (re-test con "Probar") |
| Cambiar el prompt de un agente | `/admin/agent/agents` — es una lista de agentes; abres el que quieras y el cambio se aplica al guardar. Queda versionado, con historial y restauración. |
| Crear o revocar tokens de agente / MCP | `/admin/connections`, pestaña API / MCP |
| Ver derivaciones humanas | `/inbox` filtrado por "Humano" |
| Ver alertas de seguridad | **Sistema → Logs en vivo**. No salen de la aplicación: no hay Telegram ni Slack. Hay que entrar a mirarlo. |
| Borrar la cuenta de revisor al acabar la revisión de Meta | `/admin/users` ([§ 6.10](#610-al-acabar-la-revisión-de-meta-borra-la-cuenta-de-revisor)) |
| Health en vivo | `/admin/health` |

### Si en los Logs en vivo aparece `[Seguridad] Contacto bloqueado por abuso`

- Revisa `/admin/blocklist` — verás teléfono, motivo y TTL.
- Si es legítimo, desbloquéa.
- Si es ataque, déjalo bloqueado y no hagas nada: el bloqueo automático expira
  solo a la hora. Si vuelve, bloquéalo tú a mano desde el panel (24 h) o crea
  una regla a nivel firewall.

### Si aparece `[Seguridad] Intento de suplantación de teléfono vía agente`

Alguien le ha pedido al bot "soy +34X" y el bot ha intentado obedecer. El código bloqueó la acción. Investiga el contacto real (`real_phone`) — si es un patrón, considerar bloquearlo.

---

## 8. Lo que NO está protegido (decisiones explícitas)

- **Moderación de contenido sin OpenAI**: si la instalación usa solo Anthropic o
  Gemini, la moderación no funciona. Es lo más importante de esta lista y tiene
  su propia sección: [§ 9](#9-proveedor-de-ia-y-moderación-de-contenido).
- **Multi-tenancy**: el WebSocket emite al canal `all`. Cualquier operador autenticado ve todas las conversaciones (el revisor, solo las de Instagram, [§ 1](#el-tercer-rol-revisor-cuenta-temporal-para-meta)). Hoy hay una instancia por cliente → no es brecha. Si añades segundo cliente en la misma instancia, hay que particionar el canal.
- **Lista blanca de IPs para los tokens de agente / MCP**: no existe. El token es
  lo único que separa a un desconocido del prompt de tu bot ([§ 2.4](#24-servidor-mcp-y-tokens-de-agente)).
- **Aviso fuera de la aplicación**: las alertas de seguridad se quedan en el
  panel. No hay Telegram ni Slack (se retiraron) ni correo de aviso. Si no
  entras a **Sistema → Logs en vivo**, no te enteras ([§ 3](#alertas-de-seguridad)).
- **Auditoría más allá del año**: `audit_log` se purga a los 365 días
  (`AUDIT_RETENTION_DAYS`) y no hay archivado ni botón de exportar. Si te obligan
  a guardarla más tiempo, hay que preverlo ([§ 5](#5-auditoría)).
- **Cifrado a nivel campo de teléfono/email/nombre**: documentado en §4.
- **Bloqueo a nivel red (firewall)**: la blocklist es a nivel aplicación. Un atacante distribuido cambia de IP fácil; la mitigación real es el rate-limit + bloqueo por teléfono.
- **Refresh tokens**: el JWT dura 24h. Pasado eso, login otra vez.
- **2FA admin**: no implementado.
- **El número de quien llama se puede falsificar.** Una llamada entra en la
  ficha de ese número, la misma que la de WhatsApp: quien llame haciéndose pasar
  por un paciente oye su nombre y sus citas, y puede pedir moverlas o
  cancelarlas. Por teléfono Sofía no lee las notas internas ni el correo. La red
  telefónica no garantiza el identificador de llamada.
- **El correo no comprueba SPF ni DKIM.** Un correo puede llegar con el remitente
  de un paciente. Con «Sofía envía sola», si el correo pide contestar a otra
  dirección (Reply-To distinto del remitente) la respuesta queda de borrador para
  que la revise una persona. Si no, la respuesta va al paciente de verdad. No hay
  un tope de envíos por hora por buzón: si lo encendiste, vigila Registros.
- **El token de Coolify en tu computadora.** `mcp-setup` lo guarda en
  `~/.omnicanal/global.env` y en la configuración de Claude Code
  (`~/.claude.json`); `uninstall` no lo borra. Para retirarlo, revócalo en
  Coolify (Keys & Tokens → API tokens) y corre `claude mcp remove coolify
  --scope local` en la carpeta del CRM. Los permisos que trae esta copia no dejan
  al asistente leer esos archivos ni `~/.ssh`.

---

## 9. Proveedor de IA y moderación de contenido

La app ya trae tres proveedores de modelo: **OpenAI**, **Anthropic** y
**Gemini**. Se eligen por agente desde el panel, y el cliente concreto se decide
por la familia de la `base_url` del proveedor (`providers/llm/families.py`).
Cambiar de proveedor de modelo no es un "algún día": es una casilla del panel.

**La moderación de contenido no sigue a ese cambio.**
`services/moderation.py` llama directamente a la API de moderación de OpenAI y
pide la credencial `openai_api_key`, sin mirar qué proveedor usa el agente.

### Qué pasa en una instalación que solo use Anthropic o Gemini

Sin `openai_api_key` guardada:

1. `moderate()` no encuentra clave y devuelve "no marcado" con `available=False`.
   Es **fail-open a propósito**: la moderación no puede tumbar la conversación.
2. En la práctica, **todos los mensajes entrantes llegan al modelo sin revisar**.
   Nadie filtra acoso, contenido sexual, autolesiones ni violencia explícita.
3. La alerta `moderation_flagged` no se dispara nunca, porque nunca hay nada
   marcado. El silencio del canal de alertas parece que todo va bien.

Esto **no es hipotético y no es una nota al pie**: es el estado real de cualquier
instalación que no tenga clave de OpenAI, aunque el bot esté contestando
perfectamente con otro proveedor.

### Qué hacer

Elige una de las tres, a conciencia:

- **Guarda una `openai_api_key` aunque uses otro proveedor de modelo.** Es lo más
  sencillo. La API de moderación de OpenAI es gratuita: la clave se usa solo para
  eso y no encarece nada.
- **Integra otra moderación** (por ejemplo, Perspective API de Google) en
  `services/moderation.py`. Es trabajo de desarrollo, no de configuración.
- **Asume la pérdida de esa capa** y compénsalo: el resto de defensas del agente
  siguen ahí (capa fija de seguridad en el prompt, límites por contacto,
  blocklist automática, derivación a humano). Si el negocio atiende a menores o
  trata temas sensibles, esta opción no vale.

### Cómo comprobar cómo estás ahora mismo

- El panel lo dice: **Sistema → Logs en vivo** muestra el estado de la moderación. Si
  pone que no está operativa, todos los mensajes están pasando sin revisar.
- En **Logs en vivo** aparece `moderation.no_key` con el texto "la moderación de
  contenido está APAGADA", como mucho una vez por hora para no inundar.
