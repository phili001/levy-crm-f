// Clientes que llegan desde un anuncio de Meta que abre WhatsApp (Click to
// WhatsApp). El backend guarda el anuncio en `metadata.anuncio` del mensaje
// entrante (backend/app/services/anuncios.py, donde está explicada la regla).
//
// La ventana gratuita se calcula aquí igual que allí, con los mensajes que ya
// tiene el chat: se abre con la primera respuesta si llega dentro de las 24 h
// del mensaje del anuncio, y dura 72 h desde esa respuesta. Es solo de COSTO:
// el texto libre sigue atado a las 24 h desde el último mensaje del cliente.
import type { Message } from "@/types";

export interface Anuncio {
  tipo?: string;
  id?: string;
  url?: string;
  titulo?: string;
  texto?: string;
  medio?: string;
  imagen?: string;
  video?: string;
  ctwa_clid?: string;
}

const HORA = 3600_000;
const PARA_RESPONDER = 24 * HORA;
const GRATUITA = 72 * HORA;

export function anuncioDe(m: Message): Anuncio | null {
  if (m.rol !== "user") return null;
  const a = (m.metadata as { anuncio?: unknown } | null | undefined)?.anuncio;
  return a && typeof a === "object" ? (a as Anuncio) : null;
}

export function ventanaGratuita(mensajes: Message[]): Date | null {
  let llegada: number | null = null;
  let hasta: number | null = null;
  for (const m of mensajes) {
    const t = new Date(m.created_at).getTime();
    if (anuncioDe(m)) {
      llegada = t;
      hasta = null;
    } else if (llegada !== null && hasta === null && (m.rol === "assistant" || m.rol === "operator")) {
      if (t - llegada <= PARA_RESPONDER) hasta = t + GRATUITA;
      llegada = null;
    }
  }
  return hasta !== null && hasta > Date.now() ? new Date(hasta) : null;
}

const FORMATO = new Intl.DateTimeFormat("es-MX", {
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
});

export function fechaCorta(d: Date): string {
  return FORMATO.format(d);
}
