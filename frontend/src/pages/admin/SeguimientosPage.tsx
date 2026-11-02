// Asistente → Seguimientos (SPEC-18): lo que Sofía le manda sola al paciente.
// Recordatorio antes de la cita y «no llegó» después de marcarla en la Agenda.
//
// Vienen apagados. Al encender uno, la pantalla dice en el momento si va a
// salir o por qué no (plantilla en revisión, una variable sin dato…): un aviso
// encendido que nunca sale y nadie sabe por qué es peor que uno apagado.

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, BellRing, CheckCircle2, Loader2, Save } from "lucide-react";
import { Interruptor } from "@/components/Interruptor";
import { PageHeader } from "@/components/PageHeader";
import { errorDetail } from "@/lib/errors";
import { maskName } from "@/lib/mask";
import { usePrivacy } from "@/store/privacy";
import {
  getRegistroSeguimientos,
  getSeguimientos,
  guardarSeguimientos,
  type AvisoConfig,
  type RetomarConfig,
  type RegistroAviso,
  type Seguimientos,
  type SeguimientosConfig,
  type TipoAviso,
} from "@/services/seguimientos";

const TEXTOS: Record<TipoAviso, { titulo: string; que: string; horas: string; sugerida: string; minHoras: number }> = {
  recordatorio: {
    titulo: "Recordatorio de cita",
    que: "Le recuerda la cita al paciente. Con los botones de la plantilla confirma o pide cambiarla, y Sofía sigue la conversación (si confirma, la Agenda lo enseña con «✓ Confirmó»).",
    horas: "horas antes de la cita",
    sugerida: "Recordatorio de cita",
    minHoras: 1,
  },
  inasistencia: {
    titulo: "Si no llegó",
    que: "Cuando alguien del equipo marca «No asistió» en la Agenda, Sofía le escribe para buscarle otro horario. El CRM no sabe solo quién llegó: hay que marcarlo.",
    horas: "horas después de marcar «No asistió»",
    sugerida: "Cita perdida",
    minHoras: 0,
  },
};

const REGISTRO: Record<string, { texto: string; ok: boolean }> = {
  recordatorio_enviado: { texto: "Recordatorio enviado", ok: true },
  recordatorio_omitido: { texto: "Recordatorio sin enviar", ok: false },
  inasistencia_enviada: { texto: "«No llegó» enviado", ok: true },
  inasistencia_omitida: { texto: "«No llegó» sin enviar", ok: false },
  seguimiento_enviado: { texto: "Seguimiento enviado", ok: true },
};

const CANALES: Record<string, string> = {
  whatsapp: "WhatsApp",
  instagram_dm: "Instagram",
  messenger: "Messenger",
  email: "correo",
};

function vistaPrevia(cuerpo: string, mapa: Record<string, string>, datos: Seguimientos["datos"]): string {
  return cuerpo.replace(/\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g, (todo, n: string) => {
    const dato = datos.find((d) => d.clave === mapa[n]);
    return dato ? `[${dato.etiqueta.replace(/\s*\(.*\)$/, "")}]` : todo;
  });
}

function TarjetaAviso({
  tipo,
  aviso,
  onChange,
  info,
  estado,
  guardado,
}: {
  tipo: TipoAviso;
  aviso: AvisoConfig;
  onChange: (a: AvisoConfig) => void;
  info: Seguimientos;
  estado: string | null | undefined;
  guardado: AvisoConfig;
}) {
  const t = TEXTOS[tipo];
  const elegida = info.plantillas.find((p) => p.name === aviso.plantilla && p.language === aviso.idioma);
  const sinGuardar = JSON.stringify(aviso) !== JSON.stringify(guardado);

  function elegir(clave: string) {
    const p = info.plantillas.find((x) => `${x.name}|${x.language}` === clave);
    if (!p) {
      onChange({ ...aviso, plantilla: null, variables: {} });
      return;
    }
    // Lo que se deduce de las etiquetas de la plantilla, sin pisar lo que ya
    // se había elegido a mano para esa misma plantilla.
    const mismas = p.name === aviso.plantilla && p.language === aviso.idioma;
    onChange({
      ...aviso,
      plantilla: p.name,
      idioma: p.language,
      variables: mismas ? { ...p.sugerencia, ...aviso.variables } : { ...p.sugerencia },
    });
  }

  return (
    <section className="card p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-ink">{t.titulo}</h2>
          <p className="text-xs text-ink3 mt-0.5 leading-relaxed">{t.que}</p>
        </div>
        <Interruptor
          encendido={aviso.activo}
          onCambio={(activo) => onChange({ ...aviso, activo })}
          etiqueta={t.titulo}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-[120px_minmax(0,1fr)] items-end">
        <label className="block">
          <span className="block text-xs text-ink3 mb-1">Cuándo</span>
          <input
            type="number"
            className="input w-full text-sm"
            min={t.minHoras}
            max={72}
            value={aviso.horas}
            onChange={(e) => onChange({ ...aviso, horas: Number(e.target.value) })}
          />
        </label>
        <span className="text-xs text-ink3 pb-2">{t.horas}</span>
      </div>

      <label className="block">
        <span className="block text-xs text-ink3 mb-1">Plantilla de WhatsApp</span>
        <select
          className="input w-full text-sm"
          value={aviso.plantilla ? `${aviso.plantilla}|${aviso.idioma}` : ""}
          onChange={(e) => elegir(e.target.value)}
        >
          <option value="">Elige una plantilla…</option>
          {info.plantillas.map((p) => (
            <option key={`${p.name}|${p.language}`} value={`${p.name}|${p.language}`}>
              {p.name} ({p.language}){p.problema ? ` · ${(p.status || "").toLowerCase()}` : ""}
            </option>
          ))}
        </select>
        {info.plantillas.length === 0 && !info.error_plantillas && (
          <span className="text-[11px] text-ink3">
            Todavía no tienes plantillas. Créala en{" "}
            <Link to="/admin/connections?tab=templates" className="text-brand-ink hover:underline">
              Canales → Plantillas
            </Link>{" "}
            → Sugeridas → «{t.sugerida}».
          </span>
        )}
      </label>

      {elegida && elegida.variable_names.length > 0 && (
        <div className="space-y-2">
          <span className="block text-xs text-ink3">De dónde sale cada variable</span>
          {elegida.variable_names.map((n) => (
            <div key={n} className="grid gap-2 grid-cols-[48px_minmax(0,1fr)] items-center">
              <span className="font-mono text-xs text-ink2">{`{{${n}}}`}</span>
              <select
                className="input text-xs w-full"
                aria-label={`De dónde sale {{${n}}}`}
                value={aviso.variables[n] ?? ""}
                onChange={(e) => onChange({ ...aviso, variables: { ...aviso.variables, [n]: e.target.value } })}
              >
                <option value="">Elige el dato…</option>
                {info.datos.map((d) => (
                  <option key={d.clave} value={d.clave}>{d.etiqueta}</option>
                ))}
              </select>
            </div>
          ))}
        </div>
      )}
      {elegida?.body && (
        <div className="rounded-coro-sm bg-paper2 p-3 text-xs text-ink2 whitespace-pre-wrap">
          {vistaPrevia(elegida.body, aviso.variables, info.datos)}
        </div>
      )}

      {aviso.activo && !sinGuardar && (
        estado ? (
          <div className="flex items-start gap-2 text-xs text-state-warn">
            <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>Encendido, pero todavía no sale nada: {estado}.</span>
          </div>
        ) : (
          <div className="flex items-start gap-2 text-xs text-state-ok">
            <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>
              Listo: sale solo, {aviso.horas} {t.horas}, entre las {info.config.hora_desde}:00 y las{" "}
              {info.config.hora_hasta}:00 ({info.zona}).
            </span>
          </div>
        )
      )}
      {sinGuardar && <div className="text-[11px] text-ink3">Cambios sin guardar.</div>}
    </section>
  );
}

function TarjetaRetomar({
  retomar,
  guardado,
  onChange,
}: {
  retomar: RetomarConfig;
  guardado: RetomarConfig;
  onChange: (r: RetomarConfig) => void;
}) {
  const sinGuardar = JSON.stringify(retomar) !== JSON.stringify(guardado);
  return (
    <section className="card p-4 space-y-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-ink">Retomar a quien preguntó y no agendó</h2>
          <p className="text-xs text-ink3 mt-0.5 leading-relaxed">
            Si alguien preguntó por un precio, un tratamiento o un horario, Sofía le contestó y no volvió a
            escribir, Sofía lee la conversación y decide si vale la pena retomarla con un mensaje corto. A quien
            solo pidió la dirección o dijo que no le interesa no le escribe. Una sola vez por conversación, por
            WhatsApp, Instagram o Messenger, y siempre antes de que se cierre la ventana de 24 h, así que no
            necesita plantilla.
          </p>
        </div>
        <Interruptor
          encendido={retomar.activo}
          onCambio={(activo) => onChange({ ...retomar, activo })}
          etiqueta="Retomar a quien preguntó y no agendó"
        />
      </div>
      <div className="grid gap-3 sm:grid-cols-[120px_minmax(0,1fr)] items-end">
        <label className="block">
          <span className="block text-xs text-ink3 mb-1">Cuándo</span>
          <input
            type="number"
            className="input w-full text-sm"
            min={1}
            max={22}
            value={retomar.horas}
            onChange={(e) => onChange({ ...retomar, horas: Number(e.target.value) })}
          />
        </label>
        <span className="text-xs text-ink3 pb-2">horas después del último mensaje del paciente (de 1 a 22)</span>
      </div>
      {retomar.activo && !sinGuardar && (
        <div className="flex items-start gap-2 text-xs text-state-ok">
          <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>Listo: cada vez que una conversación se quede así, Sofía decide si escribirle.</span>
        </div>
      )}
      {sinGuardar && <div className="text-[11px] text-ink3">Cambios sin guardar.</div>}
    </section>
  );
}

function Registro({ filas, zona }: { filas: RegistroAviso[]; zona: string }) {
  const priv = usePrivacy((s) => s.enabled);
  if (filas.length === 0) {
    return <p className="text-xs text-ink3">Todavía no ha salido ningún aviso.</p>;
  }
  return (
    <ul className="divide-y divide-line card">
      {filas.map((f, i) => {
        const r = REGISTRO[f.tipo] ?? { texto: f.tipo, ok: true };
        return (
          <li key={i} className="px-3 py-2 text-xs flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <span className="numbers text-ink3 w-32 shrink-0">
              {new Date(f.cuando).toLocaleString("es-MX", { timeZone: zona, dateStyle: "short", timeStyle: "short" })}
            </span>
            <span className={r.ok ? "text-ink" : "text-state-warn"}>{r.texto}</span>
            <Link to={`/contacts/${f.contact_id}`} className="text-brand-ink hover:underline">
              {maskName(f.contacto, priv) || "Sin nombre"}
            </Link>
            <span className="text-ink3 min-w-0">
              {f.motivo ? f.motivo : f.canal ? `por ${CANALES[f.canal] ?? f.canal}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

export default function SeguimientosPage() {
  const [info, setInfo] = useState<Seguimientos | null>(null);
  const [cfg, setCfg] = useState<SeguimientosConfig | null>(null);
  const [registro, setRegistro] = useState<RegistroAviso[]>([]);
  const [cargando, setCargando] = useState(true);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function aplicar(datos: Seguimientos) {
    setInfo(datos);
    setCfg({
      ...datos.config,
      // El nombre del negocio, si nadie lo ha escrito, sale del de Apariencia.
      negocio: datos.config.negocio || datos.negocio_sugerido,
    });
  }

  useEffect(() => {
    Promise.all([getSeguimientos(), getRegistroSeguimientos()])
      .then(([datos, filas]) => {
        aplicar(datos);
        setRegistro(filas);
      })
      .catch((e) => setError(errorDetail(e, "No se pudo cargar la configuración.")))
      .finally(() => setCargando(false));
  }, []);

  async function guardar() {
    if (!cfg) return;
    setGuardando(true);
    setError(null);
    try {
      aplicar(await guardarSeguimientos(cfg));
    } catch (e) {
      setError(errorDetail(e, "No se pudo guardar."));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Seguimientos"
        description="Lo que Sofía le manda sola a tus pacientes: recordarles la cita, escribirles si no llegaron y retomar a quien preguntó y no agendó."
      />
      <div className="flex-1 overflow-auto p-4">
        {cargando || !info || !cfg ? (
          <div className="flex items-center gap-2 text-ink3 text-sm py-10 justify-center">
            {cargando ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
            {cargando ? "Cargando…" : error}
          </div>
        ) : (
          <div className="max-w-3xl space-y-4">
            <div className="card p-4 flex gap-3">
              <BellRing className="w-5 h-5 text-brand-ink shrink-0 mt-0.5" />
              <p className="text-xs text-ink2 leading-relaxed">
                Salen por <span className="font-medium">WhatsApp</span> con una plantilla aprobada, porque casi
                siempre la ventana de 24 h ya se cerró. Si la persona no tiene WhatsApp, salen por el canal donde
                agendó (Instagram, Messenger o correo) solo si su ventana sigue abierta. Nunca a quien pidió la
                baja o está bloqueado, nunca con el canal en pausa y nunca de noche.
              </p>
            </div>
            {info.error_plantillas && (
              <div className="text-xs text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
                No se pudieron leer tus plantillas de WhatsApp: {info.error_plantillas}
              </div>
            )}

            <section className="card p-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_90px_90px] items-end">
              <label className="block min-w-0">
                <span className="block text-xs text-ink3 mb-1">Nombre del negocio (para la plantilla)</span>
                <input
                  className="input w-full text-sm"
                  maxLength={80}
                  placeholder="Clínica Sonrisa Perfecta"
                  value={cfg.negocio}
                  onChange={(e) => setCfg({ ...cfg, negocio: e.target.value })}
                />
              </label>
              <label className="block">
                <span className="block text-xs text-ink3 mb-1">Desde las</span>
                <input
                  type="number"
                  className="input w-full text-sm"
                  min={0}
                  max={23}
                  value={cfg.hora_desde}
                  onChange={(e) => setCfg({ ...cfg, hora_desde: Number(e.target.value) })}
                />
              </label>
              <label className="block">
                <span className="block text-xs text-ink3 mb-1">Hasta las</span>
                <input
                  type="number"
                  className="input w-full text-sm"
                  min={1}
                  max={24}
                  value={cfg.hora_hasta}
                  onChange={(e) => setCfg({ ...cfg, hora_hasta: Number(e.target.value) })}
                />
              </label>
            </section>

            {(["recordatorio", "inasistencia"] as TipoAviso[]).map((tipo) => (
              <TarjetaAviso
                key={tipo}
                tipo={tipo}
                aviso={cfg[tipo]}
                guardado={info.config[tipo]}
                onChange={(a) => setCfg({ ...cfg, [tipo]: a })}
                info={info}
                estado={info.estado[tipo]}
              />
            ))}

            <TarjetaRetomar
              retomar={cfg.retomar}
              guardado={info.config.retomar}
              onChange={(r) => setCfg({ ...cfg, retomar: r })}
            />

            {error && (
              <div className="text-xs text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
                {error}
              </div>
            )}
            <button type="button" className="btn-primary" onClick={() => void guardar()} disabled={guardando}>
              {guardando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />} Guardar
            </button>

            <section className="space-y-2 pt-2">
              <h2 className="text-sm font-semibold text-ink">Últimos avisos</h2>
              <Registro filas={registro} zona={info.zona} />
            </section>
          </div>
        )}
      </div>
    </div>
  );
}

