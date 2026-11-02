import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import clsx from "clsx";
import { Ban, CalendarPlus, ChevronLeft, ChevronRight, Loader2, Plus, X } from "lucide-react";
import { PageHeader } from "@/components/PageHeader";
import { ConfirmModal } from "@/components/ConfirmModal";
import { AppointmentModal } from "@/components/AppointmentModal";
import { BlockModal } from "@/components/BlockModal";
import { useInboxSocket } from "@/hooks/useInboxSocket";
import { borrarBloqueo, getAgenda } from "@/services/agenda";
import type { AgendaVista, Appointment, AppointmentEstado, ScheduleBlock } from "@/types";
import {
  ESTADO_CITA,
  diaDe,
  diaSemana,
  etiquetaDiaLarga,
  etiquetaRango,
  horaDe,
  hoyDelNavegador,
  lunesDe,
  minutosDe,
  sumarDias,
  tramoEnMinutos,
} from "@/lib/agenda";
import { errorDetail } from "@/lib/errors";
import { usePrivacy } from "@/store/privacy";
import { AgendaGrid } from "./agenda/AgendaGrid";
import { AppointmentPanel } from "./agenda/AppointmentPanel";
import { DayList } from "./agenda/DayList";

type Modo = "dia" | "semana";

const ORDEN_LEYENDA: AppointmentEstado[] = ["agendada", "reagendada", "completada", "no_asistio", "cancelada"];

function rangoBloqueo(b: ScheduleBlock): string {
  const mismoDia = diaDe(b.inicio_local) === diaDe(b.fin_local);
  if (mismoDia) return `${etiquetaDiaLarga(diaDe(b.inicio_local))}, de ${horaDe(b.inicio_local)} a ${horaDe(b.fin_local)}`;
  return `Del ${etiquetaDiaLarga(diaDe(b.inicio_local))} (${horaDe(b.inicio_local)}) al ${etiquetaDiaLarga(diaDe(b.fin_local))} (${horaDe(b.fin_local)})`;
}

/**
 * La agenda de la clínica (Fase 9): día o semana, en la hora del negocio. Aquí
 * aparece al momento lo que Sofía agenda por cualquier canal, y el equipo
 * agenda, mueve, marca o cancela, y bloquea horarios.
 */
export default function Agenda() {
  const priv = usePrivacy((s) => s.enabled);
  const [params, setParams] = useSearchParams();
  const ancho = typeof window !== "undefined" && window.matchMedia("(min-width: 768px)").matches;
  const modo: Modo =
    params.get("vista") === "dia" || (!params.get("vista") && !ancho) ? "dia" : "semana";
  const ancla = params.get("fecha");

  const [vista, setVista] = useState<AgendaVista | null>(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [servicio, setServicio] = useState("");
  const [verCanceladas, setVerCanceladas] = useState(false);
  const [seleccion, setSeleccion] = useState<Appointment | null>(null);
  const [bloqueoSel, setBloqueoSel] = useState<ScheduleBlock | null>(null);
  const [nueva, setNueva] = useState<{ fecha: string; hora: string | null } | null>(null);
  const [mover, setMover] = useState<Appointment | null>(null);
  const [bloquear, setBloquear] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const [quitandoBloqueo, setQuitandoBloqueo] = useState(false);

  const base = ancla ?? vista?.hoy ?? hoyDelNavegador();
  const desde = modo === "semana" ? lunesDe(base) : base;
  const hasta = modo === "semana" ? sumarDias(desde, 6) : base;

  const cargar = useCallback(async () => {
    try {
      setVista(await getAgenda(desde, hasta));
      setError(null);
    } catch (e) {
      setError(errorDetail(e, "No se pudo cargar la agenda."));
    } finally {
      setCargando(false);
    }
  }, [desde, hasta]);

  useEffect(() => {
    setCargando(true);
    void cargar();
  }, [cargar]);

  // Lo que se agenda por cualquier canal aparece sin recargar la página.
  useInboxSocket(
    (ev) => {
      if (ev.type === "agenda.changed") void cargar();
    },
    () => void cargar(),
  );

  function irA(fecha: string | null, nuevoModo: Modo = modo) {
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev);
        if (fecha) p.set("fecha", fecha);
        else p.delete("fecha");
        p.set("vista", nuevoModo);
        return p;
      },
      { replace: true },
    );
  }

  const citasVisibles = useMemo(
    () =>
      (vista?.citas ?? []).filter(
        (c) => (verCanceladas || c.estado !== "cancelada") && (!servicio || c.servicio === servicio),
      ),
    [vista, verCanceladas, servicio],
  );

  // En la semana, los días que se atienden (y cualquier otro que tenga citas).
  const dias = useMemo(() => {
    if (modo === "dia" || !vista) return [desde];
    const semana = Array.from({ length: 7 }, (_, i) => sumarDias(desde, i));
    const conAlgo = semana.filter(
      (d) => vista.work_days.includes(diaSemana(d)) || citasVisibles.some((c) => diaDe(c.inicio_local) === d),
    );
    return conAlgo.length ? conAlgo : semana;
  }, [modo, vista, desde, citasVisibles]);

  // Filas de la rejilla: del primer tramo al último, ampliadas si hay citas fuera.
  const [horaMin, horaMax] = useMemo(() => {
    let min = 24 * 60;
    let max = 0;
    for (const t of vista?.work_hours ?? []) {
      const [a, b] = tramoEnMinutos(t);
      min = Math.min(min, a);
      max = Math.max(max, b);
    }
    for (const c of citasVisibles) {
      if (!dias.includes(diaDe(c.inicio_local))) continue;
      min = Math.min(min, minutosDe(horaDe(c.inicio_local)));
      max = Math.max(max, diaDe(c.fin_local) > diaDe(c.inicio_local) ? 24 * 60 : minutosDe(horaDe(c.fin_local)));
    }
    if (min >= max) return [8, 18];
    return [Math.floor(min / 60), Math.min(24, Math.ceil(max / 60))];
  }, [vista, citasVisibles, dias]);

  const servicios = useMemo(() => {
    const nombres = new Set((vista?.servicios ?? []).map((s) => s.nombre));
    (vista?.citas ?? []).forEach((c) => nombres.add(c.servicio));
    return [...nombres].sort((a, b) => a.localeCompare(b, "es"));
  }, [vista]);

  async function quitarBloqueo() {
    if (!bloqueoSel) return;
    setQuitandoBloqueo(true);
    try {
      await borrarBloqueo(bloqueoSel.id);
      setBloqueoSel(null);
      setAviso("Bloqueo quitado: ese horario vuelve a ofrecerse.");
      void cargar();
    } catch (e) {
      setError(errorDetail(e, "No se pudo quitar el bloqueo."));
    } finally {
      setQuitandoBloqueo(false);
    }
  }

  const etiqueta = modo === "semana" ? etiquetaRango(desde, hasta) : etiquetaDiaLarga(desde);

  return (
    <div className="h-full flex flex-col bg-paper">
      <PageHeader
        eyebrow="Atención"
        title="Agenda"
        description={vista ? `Las citas de todos los canales, en la hora de ${vista.zona}.` : "Las citas de todos los canales."}
        actions={
          <>
            <button type="button" className="btn-ghost btn-sm" onClick={() => setBloquear(true)}>
              <Ban /> Bloquear horario
            </button>
            <button
              type="button"
              className="btn-primary btn-sm hidden sm:inline-flex"
              onClick={() => setNueva({ fecha: modo === "dia" ? desde : (vista?.hoy ?? desde), hora: null })}
            >
              <CalendarPlus /> Nueva cita
            </button>
          </>
        }
      />

      <div className="px-4 md:px-6 py-2.5 border-b border-line bg-card flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-coro-sm bg-paper2 p-0.5" role="group" aria-label="Vista">
          {(["dia", "semana"] as const).map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => irA(base, m)}
              aria-pressed={modo === m}
              className={clsx(
                "px-2.5 py-1 rounded-coro-sm text-[12px] font-medium transition-colors",
                modo === m ? "bg-brand text-brand-on" : "text-ink2 hover:bg-paper3",
              )}
            >
              {m === "dia" ? "Día" : "Semana"}
            </button>
          ))}
        </div>
        <div className="inline-flex items-center gap-1">
          <button
            type="button"
            className="btn-icon btn-sm"
            onClick={() => irA(sumarDias(base, modo === "semana" ? -7 : -1))}
            aria-label={modo === "semana" ? "Semana anterior" : "Día anterior"}
          >
            <ChevronLeft />
          </button>
          <button type="button" className="btn-ghost btn-sm" onClick={() => irA(null)}>
            Hoy
          </button>
          <button
            type="button"
            className="btn-icon btn-sm"
            onClick={() => irA(sumarDias(base, modo === "semana" ? 7 : 1))}
            aria-label={modo === "semana" ? "Semana siguiente" : "Día siguiente"}
          >
            <ChevronRight />
          </button>
        </div>
        <div className="text-sm font-semibold text-ink first-letter:uppercase">{etiqueta}</div>
        {cargando && <Loader2 className="w-4 h-4 animate-spin text-ink3" aria-label="Cargando" />}
        <div className="flex-1" />
        <select
          className="input text-xs py-1 w-auto"
          value={servicio}
          onChange={(e) => setServicio(e.target.value)}
          aria-label="Filtrar por servicio"
        >
          <option value="">Todos los servicios</option>
          {servicios.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <label className="inline-flex items-center gap-1.5 text-[12px] text-ink2 cursor-pointer select-none">
          <input
            type="checkbox"
            className="accent-brand w-4 h-4"
            checked={verCanceladas}
            onChange={(e) => setVerCanceladas(e.target.checked)}
          />
          Canceladas
        </label>
      </div>

      {(error || aviso) && (
        <div className="px-4 md:px-6 pt-3">
          <div
            className={clsx(
              "text-xs rounded-coro-sm px-3 py-2 flex items-start gap-2 border",
              error ? "text-state-bad bg-state-bad/10 border-state-bad/30" : "text-ink2 bg-paper2 border-line",
            )}
            role={error ? "alert" : "status"}
          >
            <span className="flex-1">{error ?? aviso}</span>
            <button
              type="button"
              onClick={() => (error ? void cargar() : setAviso(null))}
              className="font-semibold hover:underline shrink-0"
            >
              {error ? "Reintentar" : <X className="w-3.5 h-3.5" aria-label="Cerrar el aviso" />}
            </button>
          </div>
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-auto">
        {vista && (
          <>
            <div className={clsx(modo === "dia" && "hidden md:block")}>
              <AgendaGrid
                vista={vista}
                dias={dias}
                citas={citasVisibles}
                horaMin={horaMin}
                horaMax={horaMax}
                priv={priv}
                onCita={setSeleccion}
                onBloqueo={setBloqueoSel}
                onHueco={(fecha, hora) => setNueva({ fecha, hora })}
              />
            </div>
            {modo === "dia" && (
              <div className="md:hidden">
                <DayList
                  dia={desde}
                  citas={citasVisibles}
                  bloqueos={vista.bloqueos}
                  priv={priv}
                  onCita={setSeleccion}
                  onBloqueo={setBloqueoSel}
                />
              </div>
            )}
          </>
        )}
      </div>

      <div className="px-4 md:px-6 py-2 border-t border-line bg-card flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink3">
        {ORDEN_LEYENDA.map((e) => (
          <span key={e} className="inline-flex items-center gap-1.5">
            <span className={clsx("w-2.5 h-2.5 rounded-full", ESTADO_CITA[e].punto)} aria-hidden="true" />
            {ESTADO_CITA[e].label}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-sm bg-paper3" aria-hidden="true" /> Cerrado o bloqueado
        </span>
      </div>

      {/* En el móvil, "Nueva cita" como botón flotante. */}
      <button
        type="button"
        className="sm:hidden fixed bottom-5 right-5 z-30 w-12 h-12 rounded-full bg-brand text-brand-on shadow-coro-3 flex items-center justify-center"
        onClick={() => setNueva({ fecha: desde, hora: null })}
        aria-label="Nueva cita"
      >
        <Plus className="w-5 h-5" />
      </button>

      {seleccion && vista && (
        <AppointmentPanel
          key={seleccion.id}
          cita={seleccion}
          zona={vista.zona}
          priv={priv}
          onClose={() => setSeleccion(null)}
          onMover={(c) => {
            setSeleccion(null);
            setMover(c);
          }}
          onCambiada={(c) => {
            setSeleccion(c);
            void cargar();
          }}
        />
      )}

      {(nueva || mover) && (
        <AppointmentModal
          modo={mover ? "mover" : "crear"}
          servicios={vista?.servicios ?? []}
          fechaInicial={nueva?.fecha ?? desde}
          horaInicial={nueva?.hora}
          cita={mover}
          onClose={() => {
            setNueva(null);
            setMover(null);
          }}
          onSaved={(c) => {
            setAviso(
              `${mover ? "Cita movida" : "Cita agendada"}: ${etiquetaDiaLarga(diaDe(c.inicio_local))} a las ${horaDe(c.inicio_local)}.`,
            );
            setNueva(null);
            setMover(null);
            void cargar();
          }}
        />
      )}

      {bloquear && (
        <BlockModal
          fechaInicial={modo === "dia" ? desde : (vista?.hoy ?? desde)}
          onClose={() => setBloquear(false)}
          onSaved={(b) => {
            setBloquear(false);
            setAviso(
              b.citas_afectadas > 0
                ? `Horario bloqueado. Hay ${b.citas_afectadas} cita${b.citas_afectadas === 1 ? "" : "s"} dentro: no se movieron, muévelas o cancélalas.`
                : "Horario bloqueado.",
            );
            void cargar();
          }}
        />
      )}

      <ConfirmModal
        open={!!bloqueoSel}
        title={bloqueoSel?.motivo ? `Bloqueado: ${bloqueoSel.motivo}` : "Horario bloqueado"}
        description={bloqueoSel ? `${rangoBloqueo(bloqueoSel)}. Si lo quitas, ese horario vuelve a ofrecerse.` : undefined}
        confirmLabel="Quitar bloqueo"
        cancelLabel="Cerrar"
        tone="danger"
        busy={quitandoBloqueo}
        onConfirm={() => void quitarBloqueo()}
        onCancel={() => setBloqueoSel(null)}
      />
    </div>
  );
}
