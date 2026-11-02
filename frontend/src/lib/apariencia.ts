// Apariencia de la instalación (marca blanca): del color que elige el cliente
// a las variables CSS que consume todo el panel.
//
// El cliente elige UN color. Los demás tonos se derivan aquí y no se le piden,
// porque cada uno tiene un trabajo con un mínimo de contraste que no se ve a
// ojo:
//   · brand-on   → texto encima de un botón de marca: blanco o casi negro,
//                  el que más contraste dé (con cualquier color, uno de los dos
//                  pasa de 4.5:1).
//   · brand-ink  → la marca usada como TEXTO sobre el fondo. Se oscurece (claro)
//                  o aclara (oscuro) hasta 7:1, que es donde está la de fábrica
//                  (7.7 y 8.8). Un amarillo de marca no se puede leer como texto;
//                  su brand-ink sí.
//   · 600 / 700  → hover y pulsado: el mismo color mezclado con negro.
//   · soft       → fondo suave de chips y filas seleccionadas.
//
// Con el color de fábrica no se deriva nada: se quedan los valores escritos a
// mano en index.css, que son los medidos.

export interface Apariencia {
  nombre: string | null;
  paleta: string;
  color_marca: string;
  tipografia: string;
  trazo_iconos: "fino" | "normal" | "grueso";
  logo_url: string | null;
  tipografias: string[];
  trazos: string[];
}

export const COLOR_DE_FABRICA = "#2350E6";
export const TIPOGRAFIA_DE_FABRICA = "Figtree";

export const PALETAS: { id: string; nombre: string; color: string }[] = [
  { id: "cobalto", nombre: "Cobalto", color: COLOR_DE_FABRICA },
  { id: "petroleo", nombre: "Petróleo", color: "#0E7490" },
  { id: "violeta", nombre: "Violeta", color: "#6D28D9" },
  { id: "rubi", nombre: "Rubí", color: "#BE123C" },
  { id: "ambar", nombre: "Ámbar", color: "#B45309" },
  { id: "grafito", nombre: "Grafito", color: "#374151" },
];

export const TRAZO_PX: Record<Apariencia["trazo_iconos"], number> = {
  fino: 1.5,
  normal: 2,
  grueso: 2.5,
};

type Rgb = [number, number, number];

// Fondos contra los que se mide (los `--paper-rgb` de index.css).
const PAPEL_CLARO: Rgb = [244, 246, 249];
const PAPEL_OSCURO: Rgb = [15, 18, 24];
const BLANCO: Rgb = [255, 255, 255];
const NEGRO: Rgb = [0, 0, 0];
// Las dos tintas candidatas para brand-on: la de fábrica y la tinta del panel.
const ON_CLARO: Rgb = [247, 249, 255];
const ON_OSCURO: Rgb = [24, 28, 36];

const OBJETIVO_TEXTO_MARCA = 7;

export function hexARgb(hex: string): Rgb | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function luminancia([r, g, b]: Rgb): number {
  const canal = (v: number) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b);
}

export function contraste(a: Rgb, b: Rgb): number {
  const [x, y] = [luminancia(a), luminancia(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

function mezclar(a: Rgb, b: Rgb, t: number): Rgb {
  return [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * t)) as Rgb;
}

// Acerca `color` a `hacia` a pasos de 4% hasta que contra `fondo` llegue al
// objetivo. Siempre termina: el negro y el blanco puros pasan de 7:1 contra
// cualquiera de los dos papeles.
function hastaContraste(color: Rgb, hacia: Rgb, fondo: Rgb, objetivo: number): Rgb {
  for (let t = 0; t <= 1; t += 0.04) {
    const c = mezclar(color, hacia, t);
    if (contraste(c, fondo) >= objetivo) return c;
  }
  return hacia;
}

const canales = (c: Rgb) => c.join(" ");

export interface TonosDeMarca {
  brand: Rgb;
  brand600: Rgb;
  brand700: Rgb;
  on: Rgb;
  inkClaro: Rgb;
  inkOscuro: Rgb;
  softClaro: Rgb;
  // Contraste del color tal cual contra el papel claro: por debajo de 3:1 un
  // botón de marca apenas se distingue del fondo. No se bloquea (un amarillo de
  // marca es legítimo), pero Apariencia lo avisa.
  contrasteSobrePapel: number;
}

export function derivarTonos(hex: string): TonosDeMarca | null {
  const brand = hexARgb(hex);
  if (!brand) return null;
  const on =
    contraste(ON_CLARO, brand) >= contraste(ON_OSCURO, brand) ? ON_CLARO : ON_OSCURO;
  return {
    brand,
    brand600: mezclar(brand, NEGRO, 0.2),
    brand700: mezclar(brand, NEGRO, 0.38),
    on,
    inkClaro: hastaContraste(brand, NEGRO, PAPEL_CLARO, OBJETIVO_TEXTO_MARCA),
    inkOscuro: hastaContraste(brand, BLANCO, PAPEL_OSCURO, OBJETIVO_TEXTO_MARCA),
    // 11% de marca sobre blanco: es la proporción del #E6ECFF de fábrica.
    softClaro: mezclar(BLANCO, brand, 0.11),
    contrasteSobrePapel: contraste(brand, PAPEL_CLARO),
  };
}

type AparienciaVisual = Pick<Apariencia, "color_marca" | "tipografia" | "trazo_iconos">;

// Las reglas que sobrescriben los tokens de index.css. `html:root` y
// `html.dark` en vez de `:root` y `.dark`: un punto más de especificidad para
// ganar sin depender del orden en que se carguen las hojas (en desarrollo Vite
// inyecta la suya DESPUÉS de esta).
export function cssDeApariencia(ap: AparienciaVisual): string {
  const claro: string[] = [];
  const oscuro: string[] = [];

  const tonos =
    ap.color_marca.toUpperCase() === COLOR_DE_FABRICA ? null : derivarTonos(ap.color_marca);
  if (tonos) {
    claro.push(
      `--brand-rgb: ${canales(tonos.brand)}`,
      `--brand-600-rgb: ${canales(tonos.brand600)}`,
      `--brand-700-rgb: ${canales(tonos.brand700)}`,
      `--brand-on-rgb: ${canales(tonos.on)}`,
      `--brand-ink-rgb: ${canales(tonos.inkClaro)}`,
      `--brand-soft: rgb(${canales(tonos.softClaro)})`
    );
    // `html:root` le gana en especificidad al `.dark` de index.css, así que
    // TODO lo que aquí se fije para claro y cambie en oscuro hay que repetirlo
    // en `html.dark`. Sin esta línea el fondo suave salía claro en modo oscuro.
    oscuro.push(
      `--brand-ink-rgb: ${canales(tonos.inkOscuro)}`,
      "--brand-soft: rgb(var(--brand-rgb) / 0.20)"
    );
  }
  if (ap.tipografia !== TIPOGRAFIA_DE_FABRICA) {
    // Comillas escapadas por si acaso: el backend ya solo acepta los nombres
    // de su lista, pero esto acaba dentro de una hoja de estilos.
    claro.push(`--font-sans: "${ap.tipografia.replace(/["\\]/g, "")}"`);
  }
  if (ap.trazo_iconos !== "normal") {
    claro.push(`--icono-trazo: ${TRAZO_PX[ap.trazo_iconos] ?? 2}`);
  }

  let css = "";
  if (claro.length) css += `html:root{${claro.join(";")}}`;
  if (oscuro.length) css += `html.dark{${oscuro.join(";")}}`;
  return css;
}

// Hoja de Google Fonts de la tipografía elegida, o null para la de fábrica
// (que ya carga index.html). Los pesos son los que usa el panel.
export function hrefDeTipografia(tipografia: string): string | null {
  if (tipografia === TIPOGRAFIA_DE_FABRICA) return null;
  const familia = encodeURIComponent(tipografia).replace(/%20/g, "+");
  return `https://fonts.googleapis.com/css2?family=${familia}:wght@400;500;600;700;800&display=swap`;
}
