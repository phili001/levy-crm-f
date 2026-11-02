import clsx from "clsx";
import { Ban } from "lucide-react";
import type { Appointment, ScheduleBlock } from "@/types";
import { ESTADO_CITA, diaDe, horaDe } from "@/lib/agenda";
import { maskName } from "@/lib/mask";

interface Props {
  dia: string;
  citas: Appointment[];
  bloqueos: ScheduleBlock[];
  priv: boolean;
  onCita: (cita: Appointment) => void;
  onBloqueo: (bloqueo: ScheduleBlock) => void;
}

/** Hora del bloqueo dentro del día (o "todo el día" si lo cubre entero). */
function rangoEnDia(b: ScheduleBlock, dia: string): string {
  const ini = diaDe(b.inicio_local) < dia ? "00:00" : horaDe(b.inicio_local);
  const fin = diaDe(b.fin_local) > dia ? "24:00" : horaDe(b.fin_local);
  return ini === "00:00" && fin === "24:00" ? "Todo el día" : `${ini}–${fin}`;
}

/**
 * El día en lista, para el móvil: hora · paciente · servicio · estado. En una
 * pantalla estrecha la rejilla no cabe y una lista se lee de un vistazo.
 */
export function DayList({ dia, citas, bloqueos, priv, onCita, onBloqueo }: Props) {
  const filas = [
    ...bloqueos
      .filter((b) => diaDe(b.inicio_local) <= dia && b.fin_local > `${dia}T00:00`)
      .map((b) => ({ orden: diaDe(b.inicio_local) < dia ? "00:00" : horaDe(b.inicio_local), bloqueo: b })),
    ...citas
      .filter((c) => diaDe(c.inicio_local) === dia)
      .map((c) => ({ orden: horaDe(c.inicio_local), cita: c })),
  ].sort((a, b) => a.orden.localeCompare(b.orden));

  if (filas.length === 0) {
    return <div className="text-center text-ink3 text-sm py-12 font-display">Sin citas este día.</div>;
  }

  return (
    <ul className="divide-y divide-line">
      {filas.map((f) =>
        "cita" in f && f.cita ? (
          <li key={f.cita.id}>
            <button
              type="button"
              onClick={() => onCita(f.cita!)}
              className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-paper2"
            >
              <span className="numbers text-sm font-semibold text-ink w-12 shrink-0">{horaDe(f.cita.inicio_local)}</span>
              <span className="flex-1 min-w-0">
                <span className="block text-sm text-ink truncate">
                  {maskName(f.cita.contact_nombre, priv) || "Sin nombre"}
                </span>
                <span className="block text-[12px] text-ink3 truncate">
                  {f.cita.servicio} · hasta {horaDe(f.cita.fin_local)}
                </span>
              </span>
              <span className={clsx("pill text-[10px] shrink-0", ESTADO_CITA[f.cita.estado].pill)}>
                {ESTADO_CITA[f.cita.estado].label}
              </span>
            </button>
          </li>
        ) : "bloqueo" in f && f.bloqueo ? (
          <li key={f.bloqueo.id}>
            <button
              type="button"
              onClick={() => onBloqueo(f.bloqueo!)}
              className="w-full text-left px-4 py-3 flex items-center gap-3 bg-paper2 hover:bg-paper3"
            >
              <Ban className="w-4 h-4 text-ink3 shrink-0" />
              <span className="text-sm text-ink2 flex-1">
                {rangoEnDia(f.bloqueo, dia)} · {f.bloqueo.motivo || "Bloqueado"}
              </span>
            </button>
          </li>
        ) : null,
      )}
    </ul>
  );
}
