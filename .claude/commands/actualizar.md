---
description: Sube la instalación a una versión nueva, con copia antes y vuelta atrás si falla
---

1. Esta carpeta tiene que ser la de la versión nueva: la persona baja el zip de
   esa versión del classroom y abre Claude Code en su carpeta (aquí no hay
   `git pull`). Enséñale qué versión trae (la primera `## [x.y.z]` de
   `backend/CHANGELOG.md`) y qué cambia, y espera su «sí»: esto despliega de
   verdad. En Windows, si `python3` no existe, usa `py` o `python`.
2. Corre `python3 scripts/omni.py update --instance <dominio>` **en segundo
   plano** y sigue su salida hasta que termine: con una vuelta atrás puede pasar
   de 10 minutos, que es lo más que espera un comando normal. Al empezar dice de
   qué versión a cuál sube; si ya está en la de esta carpeta, no hace nada:
   díselo y que baje el zip nuevo. Para otra versión, `--version X.Y.Z` (exacta,
   nunca `latest`). Antes de tocar nada comprueba Coolify, el servidor y que la
   versión esté publicada, y deja una copia de la base en el servidor.
3. La primera vez puede faltar:
   - **El token de Coolify.** Pídele que corra `python3 scripts/omni.py
     mcp-setup` en su segunda terminal (o `secret set COOLIFY_BASE_URL` y
     `secret set COOLIFY_TOKEN`). Nunca se lo pidas por el chat.
   - **La IP del servidor.** No es secreta: pregúntasela y pásala con `--ip`;
     queda apuntada para la próxima vez.
   - **Entrar por SSH con llave.** Si falla porque el servidor pide contraseña,
     que corra `ssh-copy-id root@<ip>` en su terminal. En Windows no existe:
     el comando está en `INSTALAR.md`, «Si usas Windows».
4. Si la versión nueva no arranca y la base no cambió, `update` vuelve sola a la
   anterior. Si la base cambió, se para y pide `rollback`: **ese lo corre la
   persona en su terminal** (`python3 scripts/omni.py rollback --instance
   <dominio>`), porque restaura la copia y se pierde lo que haya entrado desde
   entonces. Tú no lo corres; explícale eso antes de que lo haga.
5. Al terminar, resume: de qué versión a cuál, si los datos quedaron intactos,
   qué trae y qué dijo `verify`. Si se lo saltó porque no tiene el usuario del
   panel, dile cómo guardarlo (lo imprime el propio `update`).

Con varias instalaciones, `update --all` las recorre una detrás de otra y se
para en la primera que falla.

Las migraciones de base de datos se aplican solas al arrancar. No las corras a
mano ni entres a la base de datos.
