# Servidor MCP: ahora vive dentro de la API

Desde la **1.2.0**, el servidor MCP ya no es un contenedor aparte. Lo sirve la
propia API en **`https://<tu-dominio>/mcp`**, y el código está en
[`backend/app/mcp_server.py`](../backend/app/mcp_server.py). Esta carpeta solo
queda como aviso para quien la busque.

## Por qué cambió

Coolify nunca desplegaba el contenedor del MCP, así que la tarjeta «Conectar por
MCP» del panel apuntaba a una URL que no contestaba. Y no había forma de
añadirlo: `omni.py update` solo cambia la versión y vuelve a desplegar, y el
compose que Coolify guardó al instalar no cambia, así que un servicio nuevo no
llegaría nunca a las instalaciones que ya existen. Dentro de la API llega con una
actualización normal, sin imagen nueva ni dominio propio.

## Cómo conectarse

1. En el panel, **Conexiones → API / MCP**, crea un token con los ámbitos que
   necesites. Se enseña una sola vez.
2. Copia el comando de la tarjeta «Conectar por MCP». Es este:

   ```bash
   claude mcp add --transport http crm https://<tu-dominio>/mcp \
     --header "Authorization: Bearer agt_XXXXXXXX"
   ```

Las 17 herramientas, sus ámbitos y el contrato de la API que envuelven están en
[`docs/agent_api.md`](../docs/agent_api.md).

## Si tenías el servicio `mcp` aparte (EasyPanel o local)

- Borra el servicio `mcp` y su dominio: ya no hace falta y su carpeta ya no
  trae `Dockerfile`, así que no volverá a construir.
- En el servicio del panel (`frontend`), deja **`MCP_BASE_URL` vacía**. Así el
  panel enseña la URL de la API con `/mcp`, que es la buena.
- Los tokens que ya tenías siguen valiendo: solo cambia la URL.
- La variable `AGENT_API_TOKEN` (el token «de reserva» del servidor viejo) ya no
  existe, a propósito: un endpoint público con un token de reserva quedaría
  abierto a cualquiera. Cada cliente manda el suyo.
