// Nombre visible de la app (login, sidebar, título de pestaña…). Dos fuentes,
// por orden:
//   1. El que el admin pone en Apariencia (lo fija el store de apariencia al
//      leerlo del servidor o del caché).
//   2. El de la instalación: el contenedor lo inyecta en runtime vía config.js
//      (env APP_NAME → window.__APP_CONFIG__.appName). Respaldos: el valor de
//      build (VITE_APP_NAME) y un default neutro.
let nombrePropio: string | null = null;

export function fijarNombrePropio(nombre: string | null): void {
  nombrePropio = nombre?.trim() || null;
}

export function nombreDeInstalacion(): string {
  return (
    window.__APP_CONFIG__?.appName ||
    import.meta.env.VITE_APP_NAME ||
    "CRM Omnicanal"
  );
}

export function appName(): string {
  return nombrePropio || nombreDeInstalacion();
}

// Partes de la marca para el sidebar: primera palabra en grande y el resto
// como subtítulo uppercase. Con un nombre de una sola palabra no hay subtítulo.
export function appNameParts(nombre: string = appName()): { main: string; sub: string } {
  const [main, ...rest] = nombre.trim().split(/\s+/);
  return { main: main || "CRM Omnicanal", sub: rest.join(" ") };
}
