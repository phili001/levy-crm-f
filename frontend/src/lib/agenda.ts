import type { AppointmentEstado } from "@/types";

// Fechas de la agenda como texto "AAAA-MM-DD" del negocio, nunca como Date del
// navegador: la recepción puede estar en otro huso y un Date movería las citas
// de día. Las cuentas se hacen en UTC, donde no hay cambios de horario.

const DIA_MS = 86_400_000;

export const DIAS_CORTOS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"];
export const DIAS_LARGOS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"];
export const MESES_CORTOS = [
  "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic",
];
export const MESES_LARGOS = [
  "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
  "septiembre", "octubre", "noviembre", "diciembre",
];

function partes(iso: string): [number, number, number] {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return [y, m, d];
}

function utc(iso: string): number {
  const [y, m, d] = partes(iso);
  return Date.UTC(y, m - 1, d);
}

/** Hoy según el navegador ("AAAA-MM-DD"): solo como punto de partida, hasta
 *  saber el día del negocio. */
export function hoyDelNavegador(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function sumarDias(iso: string, n: number): string {
  return new Date(utc(iso) + n * DIA_MS).toISOString().slice(0, 10);
}

/** 1 = lunes … 7 = domingo (como `work_days` del backend). */
export function diaSemana(iso: string): number {
  const d = new Date(utc(iso)).getUTCDay();
  return d === 0 ? 7 : d;
}

export function lunesDe(iso: string): string {
  return sumarDias(iso, 1 - diaSemana(iso));
}

/** "mar 29" */
export function etiquetaDia(iso: string): string {
  return `${DIAS_CORTOS[diaSemana(iso) - 1]} ${partes(iso)[2]}`;
}

/** "martes 29 de septiembre" */
export function etiquetaDiaLarga(iso: string): string {
  const [, m, d] = partes(iso);
  return `${DIAS_LARGOS[diaSemana(iso) - 1]} ${d} de ${MESES_LARGOS[m - 1]}`;
}

/** "28 sep – 4 oct 2026" */
export function etiquetaRango(desde: string, hasta: string): string {
  const [y1, m1, d1] = partes(desde);
  const [y2, m2, d2] = partes(hasta);
  const ini = m1 === m2 && y1 === y2 ? `${d1}` : `${d1} ${MESES_CORTOS[m1 - 1]}`;
  return `${ini} – ${d2} ${MESES_CORTOS[m2 - 1]} ${y2}`;
}

/** "2026-09-29T10:00" → "2026-09-29" */
export function diaDe(local: string): string {
  return local.slice(0, 10);
}

/** "2026-09-29T10:00" → "10:00" */
export function horaDe(local: string): string {
  return local.slice(11, 16);
}

export function minutosDe(hhmm: string): number {
  const [h, m] = hhmm.split(":").map(Number);
  return h * 60 + (m || 0);
}

export function hhmm(minutos: number): string {
  const h = Math.floor(minutos / 60);
  const m = minutos % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

/** "08:00-17:00" → [480, 1020] */
export function tramoEnMinutos(tramo: string): [number, number] {
  const [a, b] = tramo.split("-");
  return [minutosDe(a), minutosDe(b)];
}

/** Cómo se enseña cada estado. `activa` = ocupa hueco en la agenda. */
export const ESTADO_CITA: Record<
  AppointmentEstado,
  { label: string; bloque: string; pill: string; punto: string }
> = {
  agendada: {
    label: "Agendada",
    bloque: "bg-brand-soft border-brand text-ink",
    pill: "bg-brand-soft text-brand-ink",
    punto: "bg-brand",
  },
  reagendada: {
    label: "Reagendada",
    bloque: "bg-state-warn/10 border-state-warn text-ink",
    pill: "bg-state-warn/15 text-state-warn",
    punto: "bg-state-warn",
  },
  completada: {
    label: "Completada",
    bloque: "bg-state-ok/10 border-state-ok text-ink",
    pill: "bg-state-ok/15 text-state-ok",
    punto: "bg-state-ok",
  },
  cancelada: {
    label: "Cancelada",
    bloque: "bg-paper2 border-line text-ink3 line-through",
    pill: "bg-paper3 text-ink3",
    punto: "border border-ink3",
  },
  no_asistio: {
    label: "No asistió",
    bloque: "bg-state-bad/10 border-state-bad text-ink2",
    pill: "bg-state-bad/10 text-state-bad",
    punto: "border border-state-bad",
  },
};

/** Quién la puso: "Sofía por WhatsApp" o "Desde el panel". */
export function origenCita(creadaPor: string, canal: string | null): string {
  if (creadaPor === "operador") return "Desde el panel";
  const nombres: Record<string, string> = {
    whatsapp: "WhatsApp",
    web: "el chat web",
    instagram_dm: "Instagram",
    messenger: "Messenger",
    email: "correo",
    retell_voice: "teléfono",
  };
  return canal ? `El asistente, por ${nombres[canal] ?? canal}` : "El asistente";
}
