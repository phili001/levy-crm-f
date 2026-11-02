# Operación: qué consume el CRM y cómo se instala en Coolify

## Memoria por contenedor

Medido el 2026-09-19 en el VPS de staging (Hostinger, 8 GB) con la versión
0.1.0 recién desplegada, en reposo y con el perfil `dental-mx` sembrado:

| Contenedor | RAM | Notas |
|---|---|---|
| `worker` | 160 MB | Con `WORKER_CONCURRENCY=2`. Cada proceso extra suma ~60 MB |
| `app` | 131 MB | La API |
| `beat` | 63 MB | El reloj |
| `db` | 26 MB | Sube con el uso: Postgres va tomando caché |
| `redis` | 4 MB | |
| `frontend` | 3,5 MB | nginx sirviendo estáticos |
| **Total** | **≈ 390 MB** | En reposo, sin conversaciones en curso |

Eso deja el objetivo de la Fase 2 (≤ 2 GB para todo el stack) con mucho margen.
En un VPS de 2 GB entra, contando con que Postgres crece con el uso y que cada
conversación activa con IA suma memoria en el worker. Si el servidor va justo,
lo primero que se toca es `WORKER_CONCURRENCY`, nunca el número de instancias
de `app`, `worker` o `beat`: están pensados para correr de uno en uno.

## Requisitos del servidor

- 2 GB de RAM y 20 GB de disco para empezar. Las copias y los audios crecen con
  el uso; las retenciones por defecto (`docs/avanzado/easypanel.md`, anexo) las
  mantienen a raya.
- Nada de `docker login`: desde la 0.12.0 las imágenes de GHCR son públicas y el
  servidor las baja sin token. (Si publicas las tuyas como privadas, hace falta
  `omni.py login-registry`, y el token de GitHub con el permiso
  **`read:packages`**: sin él, el `pull` responde `403 Forbidden` aunque el
  `docker login` haya dicho `Login Succeeded`.)

## Instalación en Coolify (camino B)

Es la instalación en un clic. El paso a paso para quien instala está en
`INSTALAR.md`; esto es lo que hace Coolify por debajo.

1. Proyecto → **+ New → Docker Compose Empty**, y se pega
   `deploy/coolify.compose.yml` tal cual.
2. Coolify crea el dominio del servicio `frontend` (variable mágica
   `SERVICE_FQDN_FRONTEND_80`) y pide el certificado. **Un solo dominio**: el
   panel, la API, el widget y los WebSockets salen de ahí. El propio se pone en
   el campo *Domains* del componente.
3. Nadie teclea secretos. Al crear el service, Coolify genera una vez las
   variables mágicas y las guarda en *Environment Variables*:
   `SERVICE_PASSWORD_POSTGRES` (la base), `SERVICE_PASSWORD_64_JWT` (las
   sesiones), `SERVICE_PASSWORD_64_CIFRADO` (la llave de cifrado) y
   `SERVICE_PASSWORD_ARRANQUE` (el código de arranque). Son letras y números,
   sin símbolos, así que la de la base viaja sin problema dentro de
   `DATABASE_URL` (como en las plantillas del propio Coolify). La llave de
   cifrado no tiene formato Fernet: el backend la deriva y lo avisa en los
   registros (`encryption.key.derivada`). Lo que no se puede es cambiarla
   después: por eso el compose no se pega nunca encima de una instalación que ya
   existe.
4. Deploy. El primer arranque migra y siembra sin administrador
   (`seed.admin.en_primer_arranque` en los registros de `app`): el panel pide
   crearlo con el código de arranque, y la API cierra esa puerta en cuanto hay
   un administrador activo.

`APP_VERSION` trae por defecto la versión del `CHANGELOG.md` del mismo paquete
(`test_omni.py` lo comprueba) y nunca `latest`. El servidor MCP no es un
servicio de este compose: lo sirve `app` en `/mcp` desde la 1.2.0, así que llega
con cada `update` sin tocar el compose que Coolify guardó al instalar.

Comprobado en staging el 2026-09-19 con la 0.1.0: `/health` en verde con el
worker latiendo, panel y `/demo` a 200, `/widget.js` servido por el mismo
dominio, cinco logins fallidos bloquean **solo** esa IP (las demás siguen
entrando) y, tras reiniciar el stack, lo cifrado con la clave derivada se sigue
leyendo.
