import { useEffect, useMemo, useState } from "react";
import { CalendarPlus, Loader2, Search, X } from "lucide-react";
import clsx from "clsx";
import { listContacts } from "@/services/contacts";
import { cambiarCita, crearCita, getHorariosLibres, type HorariosLibres } from "@/services/agenda";
import type { AgendaServicio, Appointment } from "@/types";
import { errorDetail } from "@/lib/errors";
import { maskName, maskPhone } from "@/lib/mask";
import { usePrivacy } from "@/store/privacy";
import { diaDe, etiquetaDiaLarga, horaDe } from "@/lib/agenda";

export interface ContactoCita {
  id: string;
  nombre: string | null;
  telefono: string;
}

interface Props {
  modo: "crear" | "mover";
  servicios: AgendaServicio[];
  fechaInicial: string;
  horaInicial?: string | null;
  /** Crear: la persona, si ya se sabe (desde la ficha). Sin ella, se busca. */
  contacto?: ContactoCita | null;
  /** Mover: la cita que se mueve. */
  cita?: Appointment | null;
  onClose: () => void;
  onSaved: (cita: Appointment) => void;
}

const OTRO = "__otro";

/**
 * Nueva cita o mover una desde el panel. Enseña los horarios libres del día
 * (horario − citas − bloqueos) para elegir con un clic, pero la persona que
 * agenda manda: con «Otra hora» puede poner una fuera de la lista (fuera de
 * horario o encima de un bloqueo). Encima de otra cita no: el servidor lo
 * rechaza y aquí se dice por qué.
 */
export function AppointmentModal({
  modo,
  servicios,
  fechaInicial,
  horaInicial,
  contacto: contactoInicial,
  cita,
  onClose,
  onSaved,
}: Props) {
  const priv = usePrivacy((s) => s.enabled);
  const [contacto, setContacto] = useState<ContactoCita | null>(contactoInicial ?? null);
  const [busqueda, setBusqueda] = useState("");
  const [resultados, setResultados] = useState<ContactoCita[]>([]);
  const servicioInicial = cita?.servicio ?? servicios[0]?.nombre ?? "";
  const enCatalogo = servicios.some((s) => s.nombre === servicioInicial);
  const [servicioSel, setServicioSel] = useState(enCatalogo || !servicioInicial ? servicioInicial : OTRO);
  const [servicioLibre, setServicioLibre] = useState(enCatalogo ? "" : servicioInicial);
  const [fecha, setFecha] = useState(cita ? diaDe(cita.inicio_local) : fechaInicial);
  const [hora, setHora] = useState<string>(horaInicial ?? "");
  const [notas, setNotas] = useState("");
  const [libres, setLibres] = useState<HorariosLibres | null>(null);
  const [cargandoLibres, setCargandoLibres] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const servicio = servicioSel === OTRO ? servicioLibre.trim() : servicioSel;

  // Buscar a la persona (solo al crear sin ficha delante).
  useEffect(() => {
    if (contacto || modo !== "crear") return;
    const q = busqueda.trim();
    if (q.length < 2) {
      setResultados([]);
      return;
    }
    const t = window.setTimeout(() => {
      listContacts({ search: q, page_size: 6, include_outside_crm: true })
        .then((p) =>
          setResultados(p.items.map((c) => ({ id: c.id, nombre: c.nombre, telefono: c.telefono }))),
        )
        .catch(() => setResultados([]));
    }, 250);
    return () => window.clearTimeout(t);
  }, [busqueda, contacto, modo]);

  // Horarios libres del día para ese servicio (su duración cuenta).
  useEffect(() => {
    if (!fecha) return;
    let vigente = true;
    setCargandoLibres(true);
    getHorariosLibres({ fecha, servicio: servicio || undefined, excluir_cita: cita?.id })
      .then((r) => vigente && setLibres(r))
      .catch(() => vigente && setLibres(null))
      .finally(() => vigente && setCargandoLibres(false));
    return () => {
      vigente = false;
    };
  }, [fecha, servicio, cita?.id]);

  const listo = !!(contacto || modo === "mover") && !!servicio && !!fecha && /^\d{2}:\d{2}$/.test(hora);
  const fueraDeLista = !!hora && !!libres && !libres.horarios.includes(hora);

  const titulo = modo === "crear" ? "Nueva cita" : "Mover cita";
  const antes = useMemo(
    () => (cita ? `${etiquetaDiaLarga(diaDe(cita.inicio_local))} a las ${horaDe(cita.inicio_local)}` : ""),
    [cita],
  );

  async function guardar() {
    if (!listo) return;
    setGuardando(true);
    setError(null);
    try {
      const inicio = `${fecha}T${hora}`;
      const guardada =
        modo === "crear" && contacto
          ? await crearCita({
              contact_id: contacto.id,
              servicio,
              starts_at: inicio,
              notas: notas.trim() || null,
            })
          : await cambiarCita(cita!.id, {
              starts_at: inicio,
              ...(servicio !== cita!.servicio ? { servicio } : {}),
            });
      onSaved(guardada);
    } catch (e) {
      setError(errorDetail(e, "No se pudo guardar la cita."));
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
        className="card p-5 w-full max-w-lg max-h-[90vh] overflow-auto"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cita-titulo"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-1">
          <h3 id="cita-titulo" className="font-display text-ink" style={{ fontSize: 20 }}>
            {titulo}
          </h3>
          <button type="button" className="btn-icon" onClick={onClose} disabled={guardando} aria-label="Cerrar">
            <X className="w-4 h-4" />
          </button>
        </div>
        {cita && (
          <p className="text-[12px] text-ink3 mb-4">
            Ahora es el {antes}. Al moverla, la de antes queda cancelada en la ficha.
          </p>
        )}

        <div className="space-y-4 mt-3">
          {modo === "crear" && (
            <div>
              <label className="label">Paciente</label>
              {contacto ? (
                <div className="flex items-center gap-2 px-3 py-2 rounded-coro-sm border border-line bg-paper2">
                  <div className="flex-1 min-w-0">
                    <div className="text-sm text-ink truncate">
                      {maskName(contacto.nombre, priv) || "Sin nombre"}
                    </div>
                    <div className="text-[11px] text-ink3 font-mono truncate">
                      {maskPhone(contacto.telefono, priv)}
                    </div>
                  </div>
                  {!contactoInicial && (
                    <button type="button" className="btn-ghost btn-sm" onClick={() => setContacto(null)}>
                      Cambiar
                    </button>
                  )}
                </div>
              ) : (
                <>
                  <div className="relative">
                    <Search className="w-4 h-4 text-ink3 absolute left-2.5 top-1/2 -translate-y-1/2" />
                    <input
                      className="input pl-8"
                      value={busqueda}
                      onChange={(e) => setBusqueda(e.target.value)}
                      placeholder="Nombre, teléfono o correo"
                      autoFocus
                    />
                  </div>
                  <div className="mt-2 space-y-1 max-h-48 overflow-auto">
                    {busqueda.trim().length >= 2 && resultados.length === 0 && (
                      <div className="text-[12px] text-ink3 py-2 text-center">
                        Sin resultados. Da de alta a la persona en Clientes.
                      </div>
                    )}
                    {resultados.map((c) => (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => setContacto(c)}
                        className="w-full text-left px-3 py-2 rounded-coro-sm border border-line hover:bg-paper2"
                      >
                        <div className="text-sm text-ink truncate">{maskName(c.nombre, priv) || "Sin nombre"}</div>
                        <div className="text-[11px] text-ink3 font-mono">{maskPhone(c.telefono, priv)}</div>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          )}

          <div>
            <label className="label" htmlFor="cita-servicio">
              Servicio
            </label>
            {servicios.length > 0 ? (
              <select
                id="cita-servicio"
                className="input"
                value={servicioSel}
                onChange={(e) => setServicioSel(e.target.value)}
              >
                {servicios.map((s) => (
                  <option key={s.nombre} value={s.nombre}>
                    {s.nombre} · {s.duracion_min} min
                  </option>
                ))}
                <option value={OTRO}>Otro…</option>
              </select>
            ) : null}
            {(servicioSel === OTRO || servicios.length === 0) && (
              <input
                className={clsx("input", servicios.length > 0 && "mt-2")}
                value={servicioLibre}
                onChange={(e) => {
                  setServicioLibre(e.target.value);
                  if (servicios.length === 0) setServicioSel(OTRO);
                }}
                placeholder="Qué servicio es (dura 30 minutos)"
                maxLength={120}
              />
            )}
          </div>

          <div>
            <label className="label" htmlFor="cita-fecha">
              Día
            </label>
            <input
              id="cita-fecha"
              type="date"
              className="input"
              value={fecha}
              onChange={(e) => {
                setFecha(e.target.value);
                setHora("");
              }}
            />
          </div>

          <div>
            <div className="label">Horarios libres</div>
            {cargandoLibres ? (
              <div className="flex items-center gap-2 text-[12px] text-ink3 py-2">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Buscando horarios…
              </div>
            ) : libres?.cerrado ? (
              <div className="text-[12px] text-ink3 py-1">{libres.cerrado}</div>
            ) : libres && libres.horarios.length === 0 ? (
              <div className="text-[12px] text-ink3 py-1">Ese día ya no quedan horarios libres.</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {libres?.horarios.map((h) => (
                  <button
                    key={h}
                    type="button"
                    onClick={() => setHora(h)}
                    aria-pressed={hora === h}
                    className={clsx(
                      "numbers px-2.5 py-1 rounded-coro-sm text-[12px] font-medium border transition-colors",
                      hora === h
                        ? "bg-brand text-brand-on border-brand"
                        : "bg-card text-ink2 border-line hover:bg-paper2",
                    )}
                  >
                    {h}
                  </button>
                ))}
              </div>
            )}
            <div className="flex items-center gap-2 mt-2">
              <label className="text-[12px] text-ink3" htmlFor="cita-hora">
                Otra hora
              </label>
              <input
                id="cita-hora"
                type="time"
                step={300}
                className="input w-32 text-xs"
                value={hora}
                onChange={(e) => setHora(e.target.value)}
              />
            </div>
            {fueraDeLista && (
              <p className="text-[11px] text-state-warn mt-1">
                Fuera de los horarios libres (fuera de horario o en un bloqueo). Se puede, pero
                no encima de otra cita.
              </p>
            )}
            {libres && (
              <p className="text-[11px] text-ink3 mt-1">
                Hora de {libres.zona}. Dura {libres.duracion_min} minutos.
              </p>
            )}
          </div>

          {modo === "crear" && (
            <div>
              <label className="label" htmlFor="cita-notas">
                Notas para el equipo <span className="text-ink4">(opcional)</span>
              </label>
              <textarea
                id="cita-notas"
                rows={2}
                className="input"
                value={notas}
                onChange={(e) => setNotas(e.target.value)}
                maxLength={1000}
                placeholder="Motivo, síntomas, qué traer…"
              />
            </div>
          )}
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
            disabled={!listo || guardando}
          >
            {guardando ? <Loader2 className="w-4 h-4 animate-spin" /> : <CalendarPlus className="w-4 h-4" />}
            {modo === "crear" ? "Agendar" : "Mover"}
          </button>
        </div>
      </div>
    </div>
  );
}
