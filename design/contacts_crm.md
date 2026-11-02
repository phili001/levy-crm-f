# CRM de Contactos — plan

> **Aviso.** Este documento es un plan: describe la intención y en qué orden se
> hizo, no el árbol de ficheros de hoy. Para saber dónde está algo, manda el
> código: `frontend/src/pages/client/Contacts.tsx`,
> `pages/client/ContactDetail.tsx` y `backend/app/api/contacts.py`.

Evolucionar **Contactos + ficha** a un **CRM operativo**,
**genérico y adaptable**: los prototipos de partida eran de un negocio de **citas**
(citas, €, LTV, profesionales), y esto no da nada de eso por hecho. Igual que en el dashboard,
producto genérico; lo específico de cada negocio se modela con **campos
personalizados** (fase D).

## Criterios
- **Genérico**: NADA de citas/ingresos/LTV/profesionales hardcodeado.
- **Campos personalizados**: para **DESPUÉS** (fase D). Primero A–C con los campos
  fijos que ya existen.

## Backend: ya está medio hecho (CRM-ready)
- `Contact`: telefono, nombre, email, **estado** (pipeline 6: contacto →
  solicitud_presupuesto → seguimiento → cliente → perdido → no_cualifica), origen,
  servicio_interes, **notas_internas (cifrado)**, in_crm, created_at/updated_at;
  conversaciones enlazadas (FK).
- **Tags** m2m con color; endpoints crear/editar(PATCH)/borrar/asignar/quitar.
- Contacts API (`backend/app/api/contacts.py`): listado con `search`, `estado`,
  `tag_id`, `origen`, `segment`, `sort`, `include_outside_crm`, `page` y
  `page_size`; más `GET /stats`, `GET /export`, `POST /import`, get, create,
  `PATCH`, delete, `POST /{id}/merge`, `/conversations`, `/notes`, `/activity`,
  alta y baja de etiqueta, `/add-to-crm` y `/{id}/export`. `ContactOut` **sí**
  expone ya `created_at` y `updated_at` (se añadieron en la fase B).

## Fases
- **A ✅ Etiquetas en la ficha + menú limpio**: `components/TagPicker.tsx`
  (popover buscar / asignar-quitar / crear con color / borrar global) en la ficha;
  **eliminada la página `/tags`** (ruta + menú + archivo). Backend sin cambios.
- **B ✅ Lista de contactos CRM**: segmentos (Todos/Nuevos/Activos 30d),
  filtros (estado, canal/origen, etiqueta, orden), buscador, **alta rápida** (modal),
  paginación y tira de KPIs. Backend: filtros `origen`/`segment`/`sort` +
  `GET /contacts/stats` + `created_at`/`updated_at` en `ContactOut`.
- **C ✅ Ficha estilo CRM**: `pages/client/ContactDetail.tsx`. Hero, tarjeta de
  datos con edición inline, notas, conversaciones multicanal y "contacto desde"
  (`created_at`). Genérica, sin citas ni importes. `PageHeader` ya acepta
  `title: ReactNode` y `eyebrow`. Sub-navegación con **scroll-spy** y cinco
  secciones ancladas: **Resumen · Datos · Conversaciones · Notas · Actividad**.
  Backend: `GET/POST/DELETE /contacts/{id}/notes` (tabla `contact_note`,
  multi-autor) y `GET /contacts/{id}/activity` (tabla `contact_activity`).
- **D — Campos personalizados** (bloque grande, más adelante): modelo (secciones +
  definiciones con ~12 tipos + valores por contacto + audit), página de gestión en
  Sistema, edición inline en la ficha, y **lectura por el agente IA** (campos
  indexables). Es lo que vuelve el CRM realmente adaptable por negocio.

## Notas del prototipo (queda solo la fase D)
- **Lista**: es una **tabla** (no tarjetas) con casilla para acciones en bloque;
  columnas Contacto / Canales / Etiquetas / Última actividad / indicador de
  salud; KPIs arriba; pestañas de vista = segmentos guardados; filtros por canal
  con contador; paginación anterior/siguiente.
- **Ficha**: hecha en C. Las notas ya son multi-autor con tabla propia
  (`contact_note`); `contacts.notas_internas` sigue existiendo como campo
  cifrado, pero ya no es la única nota. La actividad tiene su tabla
  (`contact_activity`), no hace falta aproximarla.
- **Etiquetas**: chips más un "+ etiqueta" que abre el popover. Los colores son
  hex arbitrario (muestras en `TagPicker`); el prototipo usaba variantes
  semánticas.
- **Campos personalizados** (D, pendiente): tipos = texto corto y largo, número,
  dinero, fecha, booleano, desplegable, multi-chip, usuario, teléfono, correo,
  URL; por sección; obligatorio o no; visibilidad por rol (todos / admin / api);
  `visible_in_table`; `indexable_by_ai`; borrador o activo. **Hoy no existe
  ninguna tabla ni modelo de campos personalizados en el backend.**

Sistema visual: el de la app (`design/style_guide.md` y `frontend/src/index.css`).
