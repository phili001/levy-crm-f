import { useState } from "react";
import { Link } from "react-router-dom";
import clsx from "clsx";
import {
  CalendarClock,
  Check,
  CircleSlash,
  CloudOff,
  CloudUpload,
  Loader2,
  MessageSquare,
  User,
  X,
  XCircle,
} from "lucide-react";
import { ConfirmModal } from "@/components/ConfirmModal";
import { cambiarCita, cancelarCita } from "@/services/agenda";
import type { Appointment } from "@/types";
import { ESTADO_CITA, diaDe, etiquetaDiaLarga, horaDe, origenCita } from "@/lib/agenda";
import { errorDetail } from "@/lib/errors";
import { maskName, maskPhone } from "@/lib/mask";

interface Props {
  cita: Appointment;
  zona: string;
  priv: boolean;
  onClose: () => void;
  onMover: (cita: Appointment) => void;
  onCambiada: (cita: Appointment) => void;
}

/**
 * Detalle de una cita, a la derecha (a pantalla completa en el móvil). Lo que
 * se puede hacer depende de si sigue activa: moverla, marcarla como
 * completada o no asistió, o cancelarla. Las notas se editan aquí mismo.
 */
export function AppointmentPanel({ cita, zona, priv, onClose, onMover, onCambiada }: Props) {
  const [notas, setNotas] = useState(cita.notas ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmarCancelar, setConfirmarCancelar] = useState(false);
  const estilo = ESTADO_CITA[cita.estado];

  async function hacer(que: string, accion: () => Promise<Appointment>) {
    setBusy(que);
    setError(null);
    try {
      onCambiada(await accion());
    } catch (e) {
      setError(errorDetail(e, "No se pudo guardar el cambio."));
    } finally {
      setBusy(null);
      setConfirmarCancelar(false);
    }
  }

  return (
    <>
      <div className="fixed inset-0 z-40 bg-ink/20" onClick={() => !busy && onClose()}>
        <aside
          className="absolute right-0 top-0 h-full w-full sm:w-96 bg-card border-l border-line shadow-coro-3 flex flex-col"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cita-detalle-titulo"
          onClick={(e) => e.stopPropagation()}
        >
          <header className="px-5 py-4 border-b border-line flex items-start gap-3">
            <div className="flex-1 min-w-0">
              <span className={clsx("pill text-[11px]", estilo.pill)}>{estilo.label}</span>
              <h2 id="cita-detalle-titulo" className="text-lg font-semibold text-ink mt-1.5 leading-tight">
                {cita.servicio}
              </h2>
              <div className="text-sm text-ink2 mt-0.5 first-letter:uppercase">
                {etiquetaDiaLarga(diaDe(cita.inicio_local))} · {horaDe(cita.inicio_local)}–{horaDe(cita.fin_local)}
              </div>
              <div className="text-[11px] text-ink3">Hora de {zona}</div>
            </div>
            <button type="button" className="btn-icon" onClick={onClose} aria-label="Cerrar" disabled={!!busy}>
              <X className="w-4 h-4" />
            </button>
          </header>

          <div className="flex-1 overflow-auto px-5 py-4 space-y-4 text-sm">
            <div>
              <div className="eyebrow mb-1">Paciente</div>
              <div className="text-ink font-medium">{maskName(cita.contact_nombre, priv) || "Sin nombre"}</div>
              <div className="text-[12px] text-ink3 font-mono">{maskPhone(cita.contact_telefono, priv)}</div>
              <div className="flex gap-2 mt-2">
                <Link to={`/contacts/${cita.contact_id}`} className="btn-ghost btn-sm">
                  <User /> Ficha
                </Link>
                {cita.conversation_id && (
                  <Link to={`/inbox?conversation=${cita.conversation_id}`} className="btn-ghost btn-sm">
                    <MessageSquare /> Chat
                  </Link>
                )}
              </div>
            </div>

            <div>
              <div className="eyebrow mb-1">Quién la agendó</div>
              <div className="text-ink2">{origenCita(cita.creada_por, cita.canal)}</div>
            </div>

            <AvisosDeLaCita cita={cita} />

            {(cita.en_google || cita.google_sync_pendiente) && (
              <div className="flex items-center gap-1.5 text-[12px] text-ink3">
                {cita.google_sync_pendiente ? (
                  <>
                    <CloudOff className="w-3.5 h-3.5" /> La copia en Google Calendar está pendiente
                  </>
                ) : (
                  <>
                    <CloudUpload className="w-3.5 h-3.5" /> Copiada en Google Calendar
                  </>
                )}
              </div>
            )}

            <div>
              <label className="eyebrow mb-1 block" htmlFor="cita-notas-panel">
                Notas para el equipo
              </label>
              <textarea
                id="cita-notas-panel"
                rows={3}
                className="input"
                value={notas}
                onChange={(e) => setNotas(e.target.value)}
                maxLength={1000}
                placeholder="Sin notas"
                disabled={!cita.activa}
              />
              {cita.activa && notas !== (cita.notas ?? "") && (
                <button
                  type="button"
                  className="btn-primary btn-sm mt-2"
                  disabled={!!busy}
                  onClick={() => void hacer("notas", () => cambiarCita(cita.id, { notas: notas.trim() || null }))}
                >
                  {busy === "notas" ? <Loader2 className="animate-spin" /> : <Check />} Guardar notas
                </button>
              )}
            </div>

            {error && (
              <div className="text-xs text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
                {error}
              </div>
            )}
          </div>

          {cita.activa && (
            <footer className="px-5 py-4 border-t border-line grid grid-cols-2 gap-2">
              <button type="button" className="btn-primary btn-sm justify-center" disabled={!!busy} onClick={() => onMover(cita)}>
                <CalendarClock /> Mover
              </button>
              <button
                type="button"
                className="btn-ghost btn-sm justify-center"
                disabled={!!busy}
                onClick={() => void hacer("completada", () => cambiarCita(cita.id, { estado: "completada" }))}
              >
                {busy === "completada" ? <Loader2 className="animate-spin" /> : <Check />} Completada
              </button>
              <button
                type="button"
                className="btn-ghost btn-sm justify-center"
                disabled={!!busy}
                onClick={() => void hacer("no_asistio", () => cambiarCita(cita.id, { estado: "no_asistio" }))}
              >
                {busy === "no_asistio" ? <Loader2 className="animate-spin" /> : <CircleSlash />} No asistió
              </button>
              <button
                type="button"
                className="btn-ghost btn-sm justify-center text-state-bad"
                disabled={!!busy}
                onClick={() => setConfirmarCancelar(true)}
              >
                <XCircle /> Cancelar cita
              </button>
            </footer>
          )}
        </aside>
      </div>

      {/* Fuera del fondo del panel: un clic en el diálogo no debe cerrar el panel. */}
      <ConfirmModal
        open={confirmarCancelar}
        title="¿Cancelar la cita?"
        description="La cita queda en la ficha como cancelada y el horario vuelve a estar libre. No se avisa a la persona: díselo tú."
        confirmLabel="Cancelar cita"
        cancelLabel="No, dejarla"
        tone="danger"
        busy={busy === "cancelar"}
        onConfirm={() => void hacer("cancelar", () => cancelarCita(cita.id))}
        onCancel={() => setConfirmarCancelar(false)}
      />
    </>
  );
}

const CANAL_DEL_AVISO: Record<string, string> = {
  whatsapp: "WhatsApp",
  instagram_dm: "Instagram",
  messenger: "Messenger",
  email: "correo",
};

function cuando(iso: string): string {
  return new Date(iso).toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

/** Lo que Sofía le mandó sola por esta cita y si confirmó (SPEC-18). */
function AvisosDeLaCita({ cita }: { cita: Appointment }) {
  const lineas: Array<{ ok: boolean; texto: string }> = [];
  if (cita.confirmada_at) lineas.push({ ok: true, texto: `✓ Confirmó su asistencia (${cuando(cita.confirmada_at)})` });
  const nombres = { recordatorio: "Recordatorio", inasistencia: "Mensaje de «no llegó»" } as const;
  for (const tipo of ["recordatorio", "inasistencia"] as const) {
    const aviso = cita.avisos?.[tipo];
    const fecha = tipo === "recordatorio" ? cita.recordatorio_enviado_at : cita.inasistencia_enviada_at;
    if (!aviso || !fecha) continue;
    lineas.push(
      aviso.resultado === "enviado"
        ? {
            ok: true,
            texto: `${nombres[tipo]} enviado por ${CANAL_DEL_AVISO[aviso.canal ?? ""] ?? aviso.canal} (${cuando(fecha)})`,
          }
        : { ok: false, texto: `${nombres[tipo]} sin enviar: ${aviso.motivo ?? "sin motivo"}` },
    );
  }
  if (lineas.length === 0) return null;
  return (
    <div>
      <div className="eyebrow mb-1">Avisos</div>
      <ul className="space-y-0.5">
        {lineas.map((l) => (
          <li key={l.texto} className={clsx("text-[12px]", l.ok ? "text-ink2" : "text-state-warn")}>
            {l.texto}
          </li>
        ))}
      </ul>
    </div>
  );
}
