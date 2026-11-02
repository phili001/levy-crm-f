import type { MouseEvent } from "react";
import clsx from "clsx";
import type { AgendaVista, Appointment, ScheduleBlock } from "@/types";
import {
  ESTADO_CITA,
  diaDe,
  diaSemana,
  etiquetaDia,
  horaDe,
  hhmm,
  minutosDe,
  tramoEnMinutos,
} from "@/lib/agenda";
import { maskName } from "@/lib/mask";

/** Alto de una hora en la rejilla. Media hora = 28 px: da para dos líneas. */
export const PX_HORA = 56;

// Rayado de los bloqueos: con la tinta del tema, así se ve en claro y en oscuro.
const RAYADO = {
  backgroundImage:
    "repeating-linear-gradient(135deg, rgb(var(--ink-3-rgb) / 0.14) 0 6px, transparent 6px 12px)",
};

interface Props {
  vista: AgendaVista;
  dias: string[];
  citas: Appointment[];
  horaMin: number;
  horaMax: number;
  priv: boolean;
  onCita: (cita: Appointment) => void;
  onBloqueo: (bloqueo: ScheduleBlock) => void;
  onHueco: (dia: string, hora: string) => void;
}

interface Colocada {
  cita: Appointment;
  carril: number;
  carriles: number;
}

/** Reparte en carriles las citas que se pisan (una cancelada y la nueva, por ejemplo). */
function enCarriles(citas: Appointment[]): Colocada[] {
  const orden = [...citas].sort((a, b) => a.inicio_local.localeCompare(b.inicio_local));
  const out: Colocada[] = [];
  let grupo: { cita: Appointment; carril: number }[] = [];
  let finGrupo = "";
  const cerrar = () => {
    const carriles = Math.max(...grupo.map((g) => g.carril)) + 1;
    grupo.forEach((g) => out.push({ ...g, carriles }));
    grupo = [];
  };
  for (const cita of orden) {
    if (grupo.length && cita.inicio_local >= finGrupo) cerrar();
    const ocupados = grupo.filter((g) => g.cita.fin_local > cita.inicio_local).map((g) => g.carril);
    let carril = 0;
    while (ocupados.includes(carril)) carril++;
    grupo.push({ cita, carril });
    finGrupo = grupo.length === 1 || cita.fin_local > finGrupo ? cita.fin_local : finGrupo;
  }
  if (grupo.length) cerrar();
  return out;
}

/** Minuto del día de un "AAAA-MM-DDTHH:MM", recortado al día `dia`. */
function minutoEnDia(local: string, dia: string): number {
  if (diaDe(local) < dia) return 0;
  if (diaDe(local) > dia) return 24 * 60;
  return minutosDe(horaDe(local));
}

/**
 * Rejilla de la agenda: una columna por día, una fila por hora, en la hora del
 * negocio. Lo cerrado (fuera de tramos, festivos) va sombreado; los bloqueos,
 * rayados; cada cita, coloreada por estado. Un clic en un hueco vacío abre la
 * cita nueva en ese día y hora.
 */
export function AgendaGrid({ vista, dias, citas, horaMin, horaMax, priv, onCita, onBloqueo, onHueco }: Props) {
  const alto = (horaMax - horaMin) * PX_HORA;
  const tramos = vista.work_hours.map(tramoEnMinutos);
  const y = (min: number) => ((min - horaMin * 60) * PX_HORA) / 60;
  const horas = Array.from({ length: horaMax - horaMin }, (_, i) => horaMin + i);

  function cerrado(dia: string): boolean {
    return !vista.work_days.includes(diaSemana(dia)) || vista.holidays.includes(dia);
  }

  /** Franjas sin atención de un día abierto: antes, entre y después de los tramos. */
  function franjasCerradas(dia: string): [number, number][] {
    if (cerrado(dia)) return [[horaMin * 60, horaMax * 60]];
    const franjas: [number, number][] = [];
    let cursor = horaMin * 60;
    for (const [a, b] of [...tramos].sort((p, q) => p[0] - q[0])) {
      if (a > cursor) franjas.push([cursor, a]);
      cursor = Math.max(cursor, b);
    }
    if (cursor < horaMax * 60) franjas.push([cursor, horaMax * 60]);
    return franjas;
  }

  function clicEnHueco(dia: string, e: MouseEvent<HTMLDivElement>) {
    if (e.target !== e.currentTarget) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const minuto = horaMin * 60 + ((e.clientY - rect.top) * 60) / PX_HORA;
    const paso = vista.slot_step_min || 30;
    onHueco(dia, hhmm(Math.floor(minuto / paso) * paso));
  }

  const ahoraDia = diaDe(vista.ahora_local);
  const ahoraMin = minutosDe(horaDe(vista.ahora_local));

  return (
    <div className="min-w-[640px]">
      {/* Cabecera de días, fija al hacer scroll. */}
      <div
        className="grid sticky top-0 z-10 bg-card border-b border-line"
        style={{ gridTemplateColumns: `3.5rem repeat(${dias.length}, minmax(0, 1fr))` }}
      >
        <div />
        {dias.map((dia) => {
          const hoy = dia === vista.hoy;
          return (
            <div key={dia} className="px-2 py-2 border-l border-line text-center">
              <div className={clsx("text-[12px] font-semibold capitalize", hoy ? "text-brand-ink" : "text-ink2")}>
                {etiquetaDia(dia)}
              </div>
              {vista.holidays.includes(dia) && <div className="text-[10px] text-ink3">Festivo</div>}
              {!vista.holidays.includes(dia) && cerrado(dia) && <div className="text-[10px] text-ink3">Cerrado</div>}
            </div>
          );
        })}
      </div>

      <div className="grid" style={{ gridTemplateColumns: `3.5rem repeat(${dias.length}, minmax(0, 1fr))` }}>
        {/* Eje de horas. */}
        <div className="relative" style={{ height: alto }}>
          {horas.map((h) => (
            <div
              key={h}
              className="absolute right-2 -translate-y-1/2 text-[10px] text-ink3 numbers"
              style={{ top: y(h * 60) }}
            >
              {h > horaMin ? `${String(h).padStart(2, "0")}:00` : ""}
            </div>
          ))}
        </div>

        {dias.map((dia) => {
          const delDia = citas.filter((c) => diaDe(c.inicio_local) <= dia && diaDe(c.fin_local) >= dia);
          const bloqueos = vista.bloqueos.filter(
            (b) => diaDe(b.inicio_local) <= dia && b.fin_local > `${dia}T00:00`,
          );
          return (
            <div
              key={dia}
              className="relative border-l border-line cursor-cell"
              style={{ height: alto }}
              onClick={(e) => clicEnHueco(dia, e)}
              title="Clic en un hueco para agendar a esa hora"
            >
              {franjasCerradas(dia).map(([a, b]) => (
                <div
                  key={`c${a}`}
                  className="absolute inset-x-0 bg-paper2 pointer-events-none"
                  style={{ top: y(a), height: y(b) - y(a) }}
                />
              ))}
              {horas.map((h) => (
                <div
                  key={h}
                  className="absolute inset-x-0 border-t border-line2 pointer-events-none"
                  style={{ top: y(h * 60) }}
                />
              ))}
              {bloqueos.map((b) => {
                const ini = Math.max(minutoEnDia(b.inicio_local, dia), horaMin * 60);
                const fin = Math.min(minutoEnDia(b.fin_local, dia), horaMax * 60);
                if (fin <= ini) return null;
                return (
                  <button
                    key={b.id}
                    type="button"
                    onClick={() => onBloqueo(b)}
                    className="absolute inset-x-0 px-1.5 text-left text-[10px] text-ink3 font-medium border-y border-line overflow-hidden"
                    style={{ ...RAYADO, top: y(ini), height: y(fin) - y(ini) }}
                    title={b.motivo ?? "Bloqueado"}
                  >
                    {b.motivo || "Bloqueado"}
                  </button>
                );
              })}
              {enCarriles(delDia).map(({ cita, carril, carriles }) => {
                const ini = Math.max(minutoEnDia(cita.inicio_local, dia), horaMin * 60);
                const fin = Math.min(minutoEnDia(cita.fin_local, dia), horaMax * 60);
                if (fin <= ini) return null;
                const estilo = ESTADO_CITA[cita.estado];
                return (
                  <button
                    key={cita.id}
                    type="button"
                    onClick={() => onCita(cita)}
                    className={clsx(
                      "absolute rounded-coro-sm border-l-[3px] px-1.5 py-0.5 text-left overflow-hidden text-[11px] leading-tight shadow-coro-1 hover:ring-1 hover:ring-brand-ink focus-visible:ring-2 focus-visible:ring-brand-ink",
                      estilo.bloque,
                    )}
                    style={{
                      top: y(ini) + 1,
                      height: Math.max(18, y(fin) - y(ini) - 2),
                      left: `calc(${(carril / carriles) * 100}% + 2px)`,
                      width: `calc(${100 / carriles}% - 4px)`,
                    }}
                    title={`${horaDe(cita.inicio_local)} · ${cita.servicio} · ${estilo.label}${cita.confirmada_at ? " · confirmó" : ""}`}
                  >
                    <span className="numbers font-semibold">{horaDe(cita.inicio_local)}</span>{" "}
                    {cita.confirmada_at && (
                      <span className="text-state-ok font-semibold" aria-label="Confirmó">
                        ✓{" "}
                      </span>
                    )}
                    <span className="font-medium">{maskName(cita.contact_nombre, priv) || "Sin nombre"}</span>
                    <span className="block truncate text-ink3">{cita.servicio}</span>
                  </button>
                );
              })}
              {dia === ahoraDia && ahoraMin >= horaMin * 60 && ahoraMin <= horaMax * 60 && (
                <div
                  className="absolute inset-x-0 border-t-2 border-brand-ink pointer-events-none"
                  style={{ top: y(ahoraMin) }}
                  aria-hidden="true"
                />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
