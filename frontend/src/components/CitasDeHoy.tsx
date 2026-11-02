import { useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays } from "lucide-react";
import { cambiarCita, type CitasDeHoy as Datos } from "@/services/agenda";
import { horaDe } from "@/lib/agenda";
import { maskName } from "@/lib/mask";

const MAX_FILAS = 6;

/**
 * «Citas de hoy», arriba de Para hacer (Fase 9). Sustituye al recordatorio del
 * beat que pedía el plan: Para hacer no es una lista de tareas sino un filtro
 * sobre conversaciones, así que las citas se leen en vivo de la agenda. Cada
 * fila abre el chat de esa persona (o su ficha, si la cita se puso a mano).
 *
 * Las que ya pasaron y nadie marcó llevan «Vino / No vino» (SPEC-18): el
 * mensaje de «no llegó» sale cuando se marca, y si marcarlo depende de entrar
 * a la Agenda, nadie lo hace.
 */
export function CitasDeHoy({ datos, priv, onCambio }: { datos: Datos; priv: boolean; onCambio?: () => void }) {
  const [marcando, setMarcando] = useState<string | null>(null);
  const n = datos.citas.length;
  if (n === 0) return null;
  const ahora = Date.now();

  async function marcar(id: string, estado: "completada" | "no_asistio") {
    setMarcando(id);
    try {
      await cambiarCita(id, { estado });
      onCambio?.();
    } catch {
      // Sin red, la fila se queda como estaba: se puede volver a intentar.
    } finally {
      setMarcando(null);
    }
  }

  return (
    <section className="mx-3 mt-3 rounded-coro border border-line bg-card" aria-label="Citas de hoy">
      <div className="px-3 py-2 flex items-center gap-2 border-b border-line">
        <CalendarDays className="w-4 h-4 text-brand-ink" aria-hidden="true" />
        <span className="text-[13px] font-semibold text-ink">Citas de hoy</span>
        <span className="numbers text-[11px] text-ink3">{n}</span>
        <div className="flex-1" />
        <Link to={`/agenda?vista=dia&fecha=${datos.hoy}`} className="text-[12px] text-brand-ink hover:underline">
          Ver agenda
        </Link>
      </div>
      <ul className="divide-y divide-line">
        {datos.citas.slice(0, MAX_FILAS).map((c) => (
          <li key={c.id} className="flex items-center">
            <Link
              to={c.conversation_id ? `/inbox?conversation=${c.conversation_id}` : `/contacts/${c.contact_id}`}
              className="flex-1 min-w-0 px-3 py-1.5 flex items-center gap-2 hover:bg-paper2 text-[12px]"
            >
              <span className="numbers font-semibold text-ink w-10 shrink-0">{horaDe(c.inicio_local)}</span>
              <span className="flex-1 min-w-0 truncate text-ink">
                {c.confirmada_at && (
                  <span className="text-state-ok font-semibold" title="Confirmó">
                    ✓{" "}
                  </span>
                )}
                {maskName(c.contact_nombre, priv) || "Sin nombre"}
              </span>
              <span className="truncate text-ink3 max-w-[45%]">{c.servicio}</span>
            </Link>
            {new Date(c.starts_at).getTime() < ahora && (
              <span className="flex gap-1 pr-2 shrink-0">
                <button
                  type="button"
                  className="btn-ghost btn-sm text-[11px] px-1.5"
                  disabled={marcando === c.id}
                  onClick={() => void marcar(c.id, "completada")}
                >
                  Vino
                </button>
                <button
                  type="button"
                  className="btn-ghost btn-sm text-[11px] px-1.5 text-state-bad"
                  disabled={marcando === c.id}
                  onClick={() => void marcar(c.id, "no_asistio")}
                >
                  No vino
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {n > MAX_FILAS && (
        <div className="px-3 py-1.5 text-[11px] text-ink3">
          y {n - MAX_FILAS} más en la agenda
        </div>
      )}
    </section>
  );
}
