// La apariencia viva del panel: la lee del servidor, la aplica al documento y
// la guarda en localStorage para la siguiente carga.
//
// El caché no es una optimización: es lo que evita que cada carga enseñe
// primero el cobalto de fábrica y medio segundo después el color del cliente.
// public/theme.js lo aplica ANTES del primer paint (el CSS ya calculado, la
// hoja de la tipografía y el título), y aquí se vuelve a pedir al servidor por
// si el admin lo cambió desde otro navegador.
import { create } from "zustand";

import {
  cssDeApariencia,
  hrefDeTipografia,
  type Apariencia,
} from "@/lib/apariencia";
import { appName, appNameParts, fijarNombrePropio, nombreDeInstalacion } from "@/lib/appName";
import { apiBaseHttp } from "@/services/api";

// Mismo nombre que lee public/theme.js. Si cambias la forma, cámbiala allí.
const CLAVE_CACHE = "apariencia";
const ID_ESTILO = "apariencia-css";
const ID_FUENTE = "apariencia-fuente";

interface CacheApariencia {
  ap: Apariencia;
  css: string;
  fuente: string | null;
  titulo: string;
}

interface AparienciaState {
  // La guardada en el servidor (o la del caché mientras llega).
  guardada: Apariencia | null;
  // URL lista para un <img>: la del servidor o, si la API vive en otro
  // dominio, un blob: (la CSP del panel solo deja cargar imágenes del propio).
  logoSrc: string | null;
  init: () => void;
  recargar: () => Promise<void>;
  // Tras guardar en Apariencia: aplica, cachea y avisa a quien pinta la marca.
  fijar: (ap: Apariencia) => void;
  // Vista previa sin guardar: solo color, tipografía y trazo, sin caché.
  // `null` vuelve a la guardada.
  previsualizar: (ap: Pick<Apariencia, "color_marca" | "tipografia" | "trazo_iconos"> | null) => void;
}

function elemento<T extends HTMLElement>(id: string, crear: () => T): T {
  const existente = document.getElementById(id) as T | null;
  if (existente) return existente;
  const nuevo = crear();
  nuevo.id = id;
  document.head.appendChild(nuevo);
  return nuevo;
}

function aplicarVisual(ap: Pick<Apariencia, "color_marca" | "tipografia" | "trazo_iconos">) {
  elemento(ID_ESTILO, () => document.createElement("style")).textContent = cssDeApariencia(ap);
  const href = hrefDeTipografia(ap.tipografia);
  const link = document.getElementById(ID_FUENTE) as HTMLLinkElement | null;
  if (href) {
    const l =
      link ??
      elemento(ID_FUENTE, () => {
        const n = document.createElement("link");
        n.rel = "stylesheet";
        return n;
      });
    if (l.href !== href) l.href = href;
  } else if (link) {
    link.remove();
  }
}

function aplicarIdentidad(logoSrc: string | null) {
  const titulo = appName();
  document.title = titulo;
  document
    .querySelector('meta[name="apple-mobile-web-app-title"]')
    ?.setAttribute("content", titulo);
  const icono = document.querySelector('link[rel="icon"]') as HTMLLinkElement | null;
  if (icono) {
    if (logoSrc) {
      icono.removeAttribute("type");
      icono.href = logoSrc;
    } else {
      icono.type = "image/svg+xml";
      icono.href = "/favicon.svg";
    }
  }
}

function guardarCache(ap: Apariencia) {
  const cache: CacheApariencia = {
    ap,
    css: cssDeApariencia(ap),
    fuente: hrefDeTipografia(ap.tipografia),
    titulo: appName(),
  };
  try {
    localStorage.setItem(CLAVE_CACHE, JSON.stringify(cache));
  } catch {
    /* sin localStorage (modo privado): cada carga empieza en fábrica */
  }
}

function leerCache(): Apariencia | null {
  try {
    const crudo = localStorage.getItem(CLAVE_CACHE);
    return crudo ? (JSON.parse(crudo) as CacheApariencia).ap : null;
  } catch {
    return null;
  }
}

let blobAnterior: string | null = null;

async function resolverLogo(logoUrl: string | null): Promise<string | null> {
  if (blobAnterior) {
    URL.revokeObjectURL(blobAnterior);
    blobAnterior = null;
  }
  if (!logoUrl) return null;
  const url = apiBaseHttp() + logoUrl;
  if (new URL(url, window.location.href).origin === window.location.origin) return url;
  try {
    const r = await fetch(url);
    if (!r.ok) return null;
    blobAnterior = URL.createObjectURL(await r.blob());
    return blobAnterior;
  } catch {
    return null;
  }
}

export const useApariencia = create<AparienciaState>((set, get) => ({
  guardada: null,
  logoSrc: null,

  init: () => {
    const ap = leerCache();
    if (ap) {
      fijarNombrePropio(ap.nombre);
      set({ guardada: ap });
      aplicarVisual(ap);
      void resolverLogo(ap.logo_url).then((logoSrc) => {
        set({ logoSrc });
        aplicarIdentidad(logoSrc);
      });
    }
    void get().recargar();
  },

  recargar: async () => {
    try {
      const r = await fetch(apiBaseHttp() + "/api/v1/apariencia");
      if (!r.ok) return;
      get().fijar((await r.json()) as Apariencia);
    } catch {
      // Sin API (arrancando, sin red): se queda lo del caché o lo de fábrica.
      // No es motivo para enseñar un error en el login.
    }
  },

  fijar: (ap) => {
    const logoAnterior = get().guardada?.logo_url ?? null;
    fijarNombrePropio(ap.nombre);
    aplicarVisual(ap);
    guardarCache(ap);
    set({ guardada: ap });
    if (ap.logo_url !== logoAnterior || (ap.logo_url && !get().logoSrc)) {
      void resolverLogo(ap.logo_url).then((logoSrc) => {
        set({ logoSrc });
        aplicarIdentidad(logoSrc);
      });
    } else {
      aplicarIdentidad(get().logoSrc);
    }
  },

  previsualizar: (ap) => {
    const guardada = get().guardada;
    if (ap) aplicarVisual(ap);
    else if (guardada) aplicarVisual(guardada);
    else {
      document.getElementById(ID_ESTILO)?.remove();
      document.getElementById(ID_FUENTE)?.remove();
    }
  },
}));

// Para quien pinta la marca (menú, login, 404): se vuelve a pintar cuando
// cambia el nombre o el logotipo, sin recargar la página.
export function useMarca() {
  const propio = useApariencia((s) => s.guardada?.nombre ?? null);
  const logoSrc = useApariencia((s) => s.logoSrc);
  const nombre = propio || nombreDeInstalacion();
  return { nombre, partes: appNameParts(nombre), logoSrc };
}
