---
titulo: Actualizar a la última versión
grupo: principal
resumen: Sube la instalación a la versión del zip nuevo, con una copia de la base antes y vuelta atrás si falla.
---
Actualiza mi CRM (dominio: <dominio>) a la versión de esta carpeta, que es la del zip nuevo que bajé del classroom.

1. Dime qué versión trae esta carpeta (la primera `## [x.y.z]` de `backend/CHANGELOG.md`) y qué cambia, y espera a que te diga que sí. Aquí no hay `git pull`. Si estoy en Windows y `python3` no existe, usa `py` o `python`.
2. Corre `python3 scripts/omni.py update --instance <dominio>` en segundo plano y sigue su salida hasta el final: con una vuelta atrás puede pasar de 10 minutos. Al empezar dice de qué versión a cuál sube; si ya está en la de esta carpeta, dímelo, que me falta bajar el zip nuevo. Antes de tocar nada comprueba Coolify, el servidor y que la versión esté publicada, y deja una copia de la base en el servidor.
3. Si falta el token de Coolify, dime que corra `python3 scripts/omni.py mcp-setup` en mi segunda terminal; nunca me lo pidas por el chat. Si no sabes la IP del servidor, pregúntamela (no es secreta) y pásala con `--ip`. Si el servidor pide contraseña por SSH, dime que corra `ssh-copy-id root@<ip>` en mi terminal (en Windows no existe: el comando está en INSTALAR.md, «Si usas Windows»).
4. Si la versión nueva no arranca y la base no cambió, `update` vuelve sola a la anterior. Si la base cambió, se para: el `rollback` lo corro yo en mi terminal (`python3 scripts/omni.py rollback --instance <dominio>`), porque restaura la copia y se pierde lo que haya entrado desde entonces.
5. Al terminar, resume en cuatro líneas: de qué versión a cuál, si los datos quedaron intactos, qué trae y qué dijo `verify` (o, si se lo saltó, cómo guardo mi usuario del panel para que lo haga).

Las migraciones de la base de datos se aplican solas al arrancar: no las corras a mano ni entres a la base.
