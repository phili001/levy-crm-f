import { useEffect, useState } from "react";
import { Save, Clock, ToggleLeft, Building2, ShieldCheck } from "lucide-react";
import { Interruptor } from "@/components/Interruptor";
import { PageHeader } from "@/components/PageHeader";
import {
  getAppSettings,
  getFeatureFlags,
  getPromptComun,
  updateAppSettings,
  updateFeatureFlags,
  updatePromptComun,
  type FeatureFlag,
} from "@/services/admin";
import { useFlags } from "@/store/flags";
import { errorDetail } from "@/lib/errors";
import { getLegal, updateLegal, type LegalIn } from "@/services/legal";

/**
 * Ajustes generales de la app (tabla app_settings). Ajustes transversales
 * que no pertenecen a ningún agente: hoy la zona horaria del dashboard
 * (antes vivía dentro de Agentes → Modelo; se movió aquí el 15-jul).
 */
export default function SettingsPage() {
  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Ajustes"
        description="Ajustes generales de la aplicación. Afectan a todo el panel, no a un agente concreto."
      />
      <div className="flex-1 p-4 md:p-6 max-w-2xl space-y-4 overflow-auto">
        <TimezonePanel />
        <PromptComunPanel />
        <LegalPanel />
        <ModulosPanel />
      </div>
    </div>
  );
}

/**
 * Zona horaria del negocio para el dashboard/Home ("hoy/ayer", buckets por
 * hora). Es un ajuste global (tabla app_settings), no parte de la config
 * versionada del agente, por eso carga y guarda por su cuenta.
 */
function TimezonePanel() {
  const [tz, setTz] = useState<string>("");
  const [options, setOptions] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const s = await getAppSettings();
    setTz(s.dashboard_timezone);
    setOptions(s.timezone_options);
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function save() {
    if (!tz) return;
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const s = await updateAppSettings(tz);
      setTz(s.dashboard_timezone);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      // El "Guardado" verde vive dentro del try: si el guardado fallaba, el
      // desplegable se quedaba con la zona nueva en pantalla pero el
      // dashboard seguía calculando "hoy" con la vieja, y nada lo decía.
      setError(errorDetail(e, "No se pudo guardar la zona horaria. Vuelve a intentarlo."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card p-4">
      <label className="label flex items-center gap-1.5">
        <Clock className="w-3.5 h-3.5" /> Zona horaria del dashboard
      </label>
      <div className="flex flex-wrap items-center gap-2">
        <select
          className="input flex-1 min-w-[200px]"
          value={tz}
          onChange={(e) => setTz(e.target.value)}
          disabled={options.length === 0}
        >
          {/* Si la zona guardada no está en la lista común, la mostramos igual. */}
          {tz && !options.includes(tz) && <option value={tz}>{tz}</option>}
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
        <button
          onClick={save}
          className="btn-primary shrink-0"
          disabled={saving || !tz}
        >
          <Save />
          {saving ? "Guardando…" : saved ? "Guardado" : "Guardar"}
        </button>
      </div>
      {error && (
        <div className="mt-2 text-sm text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
          {error}
        </div>
      )}
      <p className="text-[11px] text-ink3 mt-2">
        Fija la hora local del negocio para los cálculos de "hoy/ayer" y las
        franjas horarias del Inicio. Si no la cambias, se usa America/Cancun.
      </p>
    </div>
  );
}


/**
 * Módulos que el curso no enseña y vienen apagados (tabla feature_flags):
 * agente interno, flujo en vivo, envío masivo y tarifas de OpenRouter. El
 * código está; aquí se encienden. Al cambiar uno se actualiza también el
 * store del panel para que el menú y las rutas reaccionen sin recargar.
 */
function ModulosPanel() {
  const [flags, setFlags] = useState<FeatureFlag[]>([]);
  const [saving, setSaving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const setLocal = useFlags((s) => s.setLocal);

  useEffect(() => {
    getFeatureFlags()
      .then(setFlags)
      .catch((e) => setError(errorDetail(e, "No se pudieron cargar los módulos.")));
  }, []);

  async function toggle(f: FeatureFlag) {
    setSaving(f.clave);
    setError(null);
    try {
      const updated = await updateFeatureFlags({ [f.clave]: !f.activa });
      setFlags(updated);
      setLocal(Object.fromEntries(updated.map((x) => [x.clave, x.activa])));
    } catch (e) {
      setError(errorDetail(e, "No se pudo cambiar el módulo. Vuelve a intentarlo."));
    } finally {
      setSaving(null);
    }
  }

  return (
    <div className="card p-4">
      <label className="label flex items-center gap-1.5">
        <ToggleLeft className="w-3.5 h-3.5" /> Módulos
      </label>
      <p className="text-[11px] text-ink3 mb-3">
        Funciones que vienen apagadas porque el curso no las usa. Enciende solo lo que vayas a
        operar: cada una suma pantallas y, en algún caso, llamadas a servicios externos.
      </p>
      <ul className="divide-y divide-line">
        {flags.map((f) => (
          <li key={f.clave} className="flex items-center justify-between gap-3 py-2">
            <div className="min-w-0">
              <div className="text-sm text-ink truncate">{f.descripcion}</div>
              <div className="text-[11px] text-ink3 font-mono">{f.clave}</div>
            </div>
            <Interruptor
              encendido={f.activa}
              onCambio={() => toggle(f)}
              etiqueta={f.descripcion}
              textoMientras={saving === f.clave ? "…" : undefined}
              disabled={saving === f.clave}
            />
          </li>
        ))}
      </ul>
      {error && (
        <div className="mt-2 text-sm text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
          {error}
        </div>
      )}
    </div>
  );
}

/**
 * Bloque común del prompt: quién es el negocio, qué vende, políticas. Se
 * escribe una vez y se antepone al prompt de TODOS los agentes (texto y voz),
 * así los dos parten de los mismos datos. El horario no va aquí: sale de la
 * agenda.
 */
function PromptComunPanel() {
  const [texto, setTexto] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPromptComun()
      .then(setTexto)
      .catch((e) => setError(errorDetail(e, "No se pudo cargar el bloque común.")));
  }, []);

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      setTexto(await updatePromptComun(texto));
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(errorDetail(e, "No se pudo guardar el bloque común. Vuelve a intentarlo."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card p-4">
      <label className="label flex items-center gap-1.5">
        <Building2 className="w-3.5 h-3.5" /> Sobre el negocio (común a todos los agentes)
      </label>
      <textarea
        className="input w-full min-h-[160px] font-mono text-xs"
        value={texto}
        onChange={(e) => setTexto(e.target.value)}
        placeholder={"Nombre del negocio, a qué se dedica, dónde está, qué vende, qué NO ofrece, políticas de pago y cancelación…"}
        maxLength={20000}
      />
      <div className="flex items-center justify-between gap-2 mt-2">
        <p className="text-[11px] text-ink3">
          Va delante del prompt de cada agente, en chats y en llamadas. El horario no se
          escribe aquí: Sofía lo toma de la agenda.
        </p>
        <button onClick={save} className="btn-primary shrink-0" disabled={saving}>
          <Save />
          {saving ? "Guardando…" : saved ? "Guardado" : "Guardar"}
        </button>
      </div>
      {error && (
        <div className="mt-2 text-sm text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
          {error}
        </div>
      )}
    </div>
  );
}


/**
 * Datos del aviso de privacidad y de los términos (páginas públicas
 * /privacidad y /terminos). El texto es una plantilla; aquí va lo que cambia de
 * una clínica a otra. Meta (vía Zernio) pide la URL de privacidad para
 * Instagram y Messenger.
 */
function LegalPanel() {
  const vacio: LegalIn = { responsable: "", domicilio: "", correo: "", telefono: "" };
  const [datos, setDatos] = useState<LegalIn>(vacio);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getLegal()
      .then((d) =>
        setDatos({ responsable: d.responsable, domicilio: d.domicilio, correo: d.correo, telefono: d.telefono }),
      )
      .catch((e) => setError(errorDetail(e, "No se pudieron cargar los datos del aviso.")));
  }, []);

  async function save() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      const d = await updateLegal(datos);
      setDatos({ responsable: d.responsable, domicilio: d.domicilio, correo: d.correo, telefono: d.telefono });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(errorDetail(e, "No se pudo guardar. Revisa que el correo sea válido."));
    } finally {
      setSaving(false);
    }
  }

  const campo = (clave: keyof LegalIn, etiqueta: string, placeholder: string) => (
    <div>
      <label className="label" htmlFor={`legal-${clave}`}>{etiqueta}</label>
      <input
        id={`legal-${clave}`}
        className="input"
        value={datos[clave]}
        placeholder={placeholder}
        onChange={(e) => setDatos({ ...datos, [clave]: e.target.value })}
      />
    </div>
  );

  return (
    <div className="card p-4 space-y-3">
      <label className="label flex items-center gap-1.5">
        <ShieldCheck className="w-3.5 h-3.5" /> Aviso de privacidad y términos
      </label>
      {campo("responsable", "Responsable (razón social o nombre)", "Clínica Sonrisa Perfecta S.A. de C.V.")}
      {campo("domicilio", "Domicilio", "Av. Tulum 123, SM 4, Cancún, Q. Roo, 77500")}
      <div className="grid gap-3 sm:grid-cols-2">
        {campo("correo", "Correo para solicitudes de datos (ARCO)", "privacidad@tuclinica.mx")}
        {campo("telefono", "Teléfono (opcional)", "+52 998 123 4567")}
      </div>
      {error && (
        <div className="text-sm text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
          {error}
        </div>
      )}
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-ink3">
          Se publican en <a href="/privacidad" target="_blank" rel="noreferrer" className="underline">/privacidad</a>{" "}
          y <a href="/terminos" target="_blank" rel="noreferrer" className="underline">/terminos</a>. El texto es
          una plantilla: haz que lo revise tu asesor legal.
        </p>
        <button onClick={save} className="btn-primary shrink-0" disabled={saving}>
          <Save />
          {saving ? "Guardando…" : saved ? "Guardado" : "Guardar"}
        </button>
      </div>
    </div>
  );
}
