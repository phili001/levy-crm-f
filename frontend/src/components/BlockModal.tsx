import { useState } from "react";
import { Ban, Loader2, X } from "lucide-react";
import { crearBloqueo } from "@/services/agenda";
import type { ScheduleBlock } from "@/types";
import { errorDetail } from "@/lib/errors";
import { sumarDias } from "@/lib/agenda";

interface Props {
  fechaInicial: string;
  onClose: () => void;
  onSaved: (bloqueo: ScheduleBlock & { citas_afectadas: number }) => void;
}

/**
 * Cerrar un tramo de la agenda: unas vacaciones (días enteros) o la hora de la
 * comida. Mientras dure no se ofrecen horarios y, por teléfono, se toma el
 * recado en vez de pasar la llamada. Las citas que ya estuvieran dentro no se
 * tocan: el servidor dice cuántas son y alguien decide si moverlas.
 */
export function BlockModal({ fechaInicial, onClose, onSaved }: Props) {
  const [diaEntero, setDiaEntero] = useState(false);
  const [desde, setDesde] = useState(fechaInicial);
  const [hasta, setHasta] = useState(fechaInicial);
  const [horaDesde, setHoraDesde] = useState("13:00");
  const [horaHasta, setHoraHasta] = useState("14:00");
  const [motivo, setMotivo] = useState("");
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      // Días enteros: hasta el final del último día, es decir, las 00:00 del siguiente.
      const inicio = diaEntero ? `${desde}T00:00` : `${desde}T${horaDesde}`;
      const fin = diaEntero ? `${sumarDias(hasta, 1)}T00:00` : `${desde}T${horaHasta}`;
      onSaved(await crearBloqueo({ starts_at: inicio, ends_at: fin, motivo: motivo.trim() || null }));
    } catch (e) {
      setError(errorDetail(e, "No se pudo bloquear ese tramo."));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-ink/30"
      onClick={() => !guardando && onClose()}
    >
      <div
        className="card p-5 w-full max-w-md"
        role="dialog"
        aria-modal="true"
        aria-labelledby="bloqueo-titulo"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-1">
          <h3 id="bloqueo-titulo" className="font-display text-ink" style={{ fontSize: 20 }}>
            Bloquear horario
          </h3>
          <button type="button" className="btn-icon" onClick={onClose} disabled={guardando} aria-label="Cerrar">
            <X className="w-4 h-4" />
          </button>
        </div>
        <p className="text-[12px] text-ink3 mb-4">
          En ese tramo no se ofrecen citas y, por teléfono, se toma el recado en vez de pasar la
          llamada.
        </p>

        <label className="inline-flex items-center gap-2 text-sm text-ink2 mb-3 cursor-pointer select-none">
          <input
            type="checkbox"
            className="accent-brand w-4 h-4"
            checked={diaEntero}
            onChange={(e) => setDiaEntero(e.target.checked)}
          />
          Días completos (vacaciones, cierre)
        </label>

        {diaEntero ? (
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label" htmlFor="bloqueo-desde">Desde</label>
              <input id="bloqueo-desde" type="date" className="input" value={desde} onChange={(e) => setDesde(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="bloqueo-hasta">Hasta (incluido)</label>
              <input id="bloqueo-hasta" type="date" className="input" value={hasta} min={desde} onChange={(e) => setHasta(e.target.value)} />
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-3 gap-3">
            <div className="col-span-3 sm:col-span-1">
              <label className="label" htmlFor="bloqueo-dia">Día</label>
              <input id="bloqueo-dia" type="date" className="input" value={desde} onChange={(e) => setDesde(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="bloqueo-de">De</label>
              <input id="bloqueo-de" type="time" step={300} className="input" value={horaDesde} onChange={(e) => setHoraDesde(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="bloqueo-a">A</label>
              <input id="bloqueo-a" type="time" step={300} className="input" value={horaHasta} onChange={(e) => setHoraHasta(e.target.value)} />
            </div>
          </div>
        )}

        <div className="mt-3">
          <label className="label" htmlFor="bloqueo-motivo">Motivo <span className="text-ink4">(opcional)</span></label>
          <input
            id="bloqueo-motivo"
            className="input"
            value={motivo}
            onChange={(e) => setMotivo(e.target.value)}
            maxLength={200}
            placeholder={diaEntero ? "Vacaciones" : "Comida"}
          />
        </div>

        {error && (
          <div className="mt-4 text-xs text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
            {error}
          </div>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <button type="button" className="btn-ghost" onClick={onClose} disabled={guardando}>
            Cancelar
          </button>
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-1.5"
            onClick={() => void guardar()}
            disabled={guardando || !desde || (diaEntero && hasta < desde)}
          >
            {guardando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Ban className="w-4 h-4" />}
            Bloquear
          </button>
        </div>
      </div>
    </div>
  );
}
