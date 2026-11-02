# Qué versión de PostgreSQL usa esto

**Resumen: la 16, y no hay nada que hacer.** Si tu instalación corre la 17
tampoco hay nada que hacer. Las dos funcionan.

## Qué trae el paquete

Los dos ficheros de despliegue (`docker-compose.yml` y
`docker-compose.easypanel.yml`) levantan `pgvector/pgvector:pg16`. PostgreSQL 16
tiene soporte oficial hasta **noviembre de 2028**.

La imagen del backend lleva dentro las herramientas de copia de seguridad de la
16 **y** de la 17, y al hacer una copia pregunta al servidor qué versión es para
usar las que tocan. Por eso la misma imagen vale para las dos.

## Por qué no se sube a la 17

Cambiar de versión mayor de PostgreSQL no es cambiar una línea: los ficheros que
la 16 deja en el volumen no los abre la 17, así que hay que volcar los datos,
borrar el volumen y recargar. Eso es media hora con la instalación parada y un
rato de riesgo, a cambio de nada que se note: la 16 y la 17 reciben los mismos
parches de seguridad, el mismo día.

Cuando se acerque 2028 tocará subir, y entonces el volcado se hará con la base
mucho más grande. Es un trabajo aplazado, no evitado — pero aplazado a un
momento en que se puede planificar.

## Cómo saber qué versión tienes

```bash
docker compose exec db psql -U <usuario> -d <base_de_datos> -c "select version();"
```

- Dice `PostgreSQL 16.x` → estás en lo que trae el paquete. No toques nada.
- Dice `PostgreSQL 17.x` → también correcto. **No bajes la imagen a la 16**: una
  versión mayor de PostgreSQL nunca se baja sobre un volumen con datos, el
  servicio no arrancaría. Deja el `image:` de tu servicio `db` en `pg17` cuando
  actualices el resto del paquete.

## Y las copias de seguridad, ¿se restauran?

Sí, en los dos casos, y también las que hiciste antes de esta versión.

Las herramientas de PostgreSQL no son compatibles en las dos direcciones: una
copia hecha con la 17 no se puede recargar tal cual sobre un servidor 16. Las
versiones anteriores de este paquete llevaban solo el cliente 17 dentro, así que
una instalación con Postgres 16 estaba haciendo copias que **no** se podían
restaurar, y nada lo avisaba. Ya está arreglado de dos maneras:

1. Las copias nuevas se hacen con las herramientas de la versión de tu servidor.
2. Las copias antiguas se detectan al restaurarlas y se recargan por un camino
   de compatibilidad, sin que tengas que hacer nada.

Además, al arrancar, la aplicación mira si sus herramientas cuadran con tu
servidor y deja un aviso en el registro si no. Detalle técnico en
`backend/app/services/pg_tools.py`.

## Anexo — subir a la 17 a mano

No hace falta y no se recomienda (arriba está el porqué). Se deja escrito por si
algún día lo necesitas. El procedimiento está ejecutado de principio a fin, no
escrito de memoria.

**Todo lo que sigue es opcional.** Si tu instalación va con la 16 —que es lo que
trae el paquete—, aquí no tienes nada que hacer.

### Antes de empezar

- **No cambies `ENCRYPTION_KEY`.** Las credenciales de los proveedores (OpenAI,
  YCloud, Google…) están cifradas con ella dentro de la base de datos. Si la
  cambias en el mismo movimiento, se recargan pero ya no descifran y el panel
  te las enseña en blanco.
- **El volumen de audios no se toca.** Audios, adjuntos y los ficheros de copia
  viven en `audios_data`, que es otro volumen. En este procedimiento solo se
  borra el de la base de datos (`db_data`).
- Ten a mano el usuario y la base de datos: están dentro de tu `DATABASE_URL`.
- Hazlo con la instalación parada de cara al público. Los mensajes que entren
  entre el volcado y la recarga no estarán en el volcado.
- Comprueba antes de nada qué versión corres (comando de
  [Cómo saber qué versión tienes](#cómo-saber-qué-versión-tienes)). Si ya dice
  `17.x`, no hay nada que migrar.

### Procedimiento

**1. Volcar la base de datos con el cliente 17**

El volcado NO se hace desde el contenedor de la base de datos (ese lleva solo
las herramientas de la 16). Se hace desde el contenedor de la aplicación, que
lleva las de la 16 y las de la 17, y aquí se pide **la 17 por su ruta completa**:
el volcado va a acabar en un servidor 17, así que tiene que hacerlo el cliente
17.

```bash
docker compose exec app sh -c 'PGPASSWORD="<contraseña>" \
  /usr/lib/postgresql/17/bin/pg_dump \
  -h db -p 5432 -U <usuario> -d <base_de_datos> \
  --format=custom --no-owner --no-privileges \
  -f /data/audios/migracion_pg16.dump'
```

El fichero queda en el volumen de audios, que es el que **no** vamos a borrar.
Antes de seguir, comprueba que existe y que pesa algo:

```bash
docker compose exec app ls -la /data/audios/migracion_pg16.dump
```

Si quieres una copia fuera del servidor (recomendable), sácala también al host:

```bash
docker cp <contenedor_app>:/data/audios/migracion_pg16.dump ./migracion_pg16.dump
```

**2. Parar y borrar SOLO el volumen de la base de datos**

```bash
docker compose down
docker volume ls | grep db_data          # localiza el nombre exacto
docker volume rm <proyecto>_db_data
```

Fíjate en que borras el que termina en `db_data`. `audios_data` y `redis_data`
se quedan como están.

**3. Cambiar la imagen a la 17 y ENTONCES levantar**

Este paso es el que hace la migración. El paquete trae `pgvector/pgvector:pg16`,
así que **antes de levantar nada** edita el servicio `db` de tu fichero de
despliegue (`docker-compose.yml` o `docker-compose.easypanel.yml`) y cambia la
imagen:

```yaml
  db:
    image: pgvector/pgvector:pg17    # antes: pg16
```

Si levantas sin cambiarla, Docker vuelve a crear una base 16 vacía y la recarga
del paso 4 —que va con el cliente 17— falla contra ella. Comprueba el cambio
antes de seguir:

```bash
grep pgvector docker-compose.yml
```

Y ahora sí:

```bash
docker compose up -d
```

El arranque aplica las migraciones sobre una base vacía y siembra el usuario
inicial. Es normal y no molesta: el paso siguiente lo pisa todo con tus datos.

**4. Recargar el volcado**

```bash
docker compose exec app sh -c 'PGPASSWORD="<contraseña>" \
  /usr/lib/postgresql/17/bin/pg_restore \
  --clean --if-exists --no-owner --no-privileges --single-transaction \
  --no-password -h db -p 5432 -U <usuario> -d <base_de_datos> \
  /data/audios/migracion_pg16.dump'
```

`--single-transaction` es todo o nada: si algo falla a mitad, no te deja la
base a medias.

**5. Reiniciar la aplicación y comprobar**

```bash
docker compose restart app worker beat
docker compose exec db psql -U <usuario> -d <base_de_datos> \
  -c "select version(); select count(*) from messages;"
docker compose exec app curl -s http://localhost:8000/health
```

El `select version()` tiene que decir ya `PostgreSQL 17.x`, y el conteo de
`messages` cuadrar con lo que tenías antes del volcado.

El reinicio del paso 5 no es opcional: la aplicación guarda en memoria el plano
de las tablas y, después de recargar, la primera petición contra cada conexión
falla con un 500 aunque todo esté bien. Reiniciando te lo ahorras.

Entra al panel y comprueba tres cosas: que están las conversaciones, que en
Conexiones siguen apareciendo las credenciales (eso confirma que
`ENCRYPTION_KEY` es la misma) y que el buscador de la base de conocimiento
responde.

### En EasyPanel

Lo mismo, pero los comandos van en la consola de cada servicio y el borrado del
volumen se hace desde el propio panel:

1. Volcado: consola del servicio `app`, comando del paso 1.
2. Sácate el fichero fuera del servidor antes de tocar nada.
3. Para el proyecto y borra el volumen de la base de datos en la pestaña de
   volúmenes del servicio `db`.
4. **Antes de desplegar**, cambia la imagen del servicio `db` a
   `pgvector/pgvector:pg17` (viene con `pg16`). Y ahora despliega.
5. Recarga: consola del servicio `app`, comando del paso 4 del procedimiento.
6. Reinicia `app`, `worker` y `beat`, y comprueba con el paso 5.

### Si algo sale mal

Mientras conserves el fichero `migracion_pg16.dump` no has perdido nada. Para
volver a la 16: pon otra vez `pgvector/pgvector:pg16` en el compose, borra el
volumen de la base de datos, levanta y recarga el mismo volcado con el paso 4.
El volcado en formato `custom` lo lee igual la 16 que la 17 — pero para
recargarlo sobre un servidor 16 usa el cliente 16
(`/usr/lib/postgresql/16/bin/pg_restore`), no el 17.

### Comprobado

Este procedimiento está ejecutado de principio a fin, no escrito de memoria:
base 16.14 con datos → volcado con cliente 17 → volumen borrado → 17.11
levantada → recarga. Al terminar, las 39 tablas, las 55 migraciones en su sitio
(cabeza `0055`), pgvector 0.8.6, el dato de prueba en su fila y el login del
panel respondiendo 200.
