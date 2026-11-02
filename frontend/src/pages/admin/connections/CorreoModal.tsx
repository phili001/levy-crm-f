import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, Loader2, Mail, Plug, X } from "lucide-react";

import { errorDetail } from "@/lib/errors";
import type { ChannelOut } from "@/services/admin";
import {
  conectarBuzon,
  listPresetsCorreo,
  probarBuzon,
  type BuzonIn,
  type PresetCorreo,
  type PruebaBuzon,
  type ServidorCorreo,
} from "@/services/correo";

/**
 * Conectar el buzón de correo de la clínica con usuario y contraseña de
 * aplicación (IMAP para leer, SMTP para responder). Guardar prueba antes: un
 * buzón que no entra no se guarda (backend/app/api/correo_imap.py).
 *
 * Al editar, la contraseña puede quedarse en blanco y se usa la guardada, pero
 * solo si no cambian el usuario ni los servidores: el backend lo exige para
 * que nadie pueda mandar la contraseña guardada a otro servidor.
 */

const SERVIDOR_VACIO: ServidorCorreo = { host: "", port: 993, seguridad: "ssl" };
const SMTP_VACIO: ServidorCorreo = { host: "", port: 587, seguridad: "starttls" };

function servidorDelCanal(cfg: Record<string, unknown>, tipo: "imap" | "smtp"): ServidorCorreo {
  const base = tipo === "imap" ? SERVIDOR_VACIO : SMTP_VACIO;
  return {
    host: String(cfg[`${tipo}_host`] ?? base.host),
    port: Number(cfg[`${tipo}_port`] ?? base.port),
    seguridad: (cfg[`${tipo}_seguridad`] === "starttls" ? "starttls" : "ssl") as ServidorCorreo["seguridad"],
  };
}

function Servidor({
  titulo,
  valor,
  onChange,
}: {
  titulo: string;
  valor: ServidorCorreo;
  onChange: (v: ServidorCorreo) => void;
}) {
  return (
    <div className="grid grid-cols-[1fr_80px_110px] gap-2 items-end">
      <div>
        <label className="label">{titulo}</label>
        <input
          className="input font-mono text-xs"
          value={valor.host}
          placeholder="mail.tudominio.com"
          onChange={(e) => onChange({ ...valor, host: e.target.value })}
        />
      </div>
      <div>
        <label className="label">Puerto</label>
        <input
          className="input font-mono text-xs"
          type="number"
          value={valor.port}
          onChange={(e) => onChange({ ...valor, port: Number(e.target.value) || 0 })}
        />
      </div>
      <div>
        <label className="label">Seguridad</label>
        <select
          className="input text-xs"
          value={valor.seguridad}
          onChange={(e) => onChange({ ...valor, seguridad: e.target.value as ServidorCorreo["seguridad"] })}
        >
          <option value="ssl">SSL</option>
          <option value="starttls">STARTTLS</option>
        </select>
      </div>
    </div>
  );
}

export function CorreoModal({
  canal,
  onClose,
  onConectado,
}: {
  // El canal de correo actual, si ya había uno por IMAP (para editar).
  canal: ChannelOut | null;
  onClose: () => void;
  onConectado: () => void;
}) {
  const cfg = canal?.config ?? {};
  const esImap = cfg.transport === "imap";
  const [presets, setPresets] = useState<PresetCorreo[]>([]);
  const [preset, setPreset] = useState<string>(esImap ? String(cfg.preset ?? "gmail") : "gmail");
  const [usuario, setUsuario] = useState(esImap ? String(cfg.usuario ?? "") : "");
  const [password, setPassword] = useState("");
  const [imap, setImap] = useState<ServidorCorreo>(esImap ? servidorDelCanal(cfg, "imap") : SERVIDOR_VACIO);
  const [smtp, setSmtp] = useState<ServidorCorreo>(esImap ? servidorDelCanal(cfg, "smtp") : SMTP_VACIO);
  const [carpeta, setCarpeta] = useState(esImap ? String(cfg.carpeta ?? "INBOX") : "INBOX");
  const [autoSend, setAutoSend] = useState(esImap ? Boolean(cfg.auto_send) : false);
  const [noResponder, setNoResponder] = useState(
    esImap && Array.isArray(cfg.no_responder) ? (cfg.no_responder as string[]).join("\n") : "",
  );
  const [prueba, setPrueba] = useState<PruebaBuzon | null>(null);
  const [probando, setProbando] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // El resultado de «Probar» queda al final del formulario: se lleva a la vista
  // para que no pase desapercibido debajo del pliegue.
  const resultadoRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (prueba || error) resultadoRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [prueba, error]);

  useEffect(() => {
    listPresetsCorreo()
      .then(setPresets)
      .catch((e) => setError(errorDetail(e, "No se pudieron cargar los proveedores.")));
  }, []);

  const elegido = presets.find((p) => p.id === preset) ?? null;
  const dominio = usuario.includes("@") ? usuario.split("@")[1].toLowerCase() : "";
  const pareceMicrosoft = /^(outlook|hotmail|live)\.|^msn\.com$|^outlook\.com$/.test(dominio);
  const servidores = useMemo(() => {
    if (preset === "otro" || !elegido) return { imap, smtp };
    const poner = (s: ServidorCorreo) => ({ ...s, host: s.host.replace("{dominio}", dominio || "tudominio.com") });
    return { imap: poner(elegido.imap), smtp: poner(elegido.smtp) };
  }, [preset, elegido, imap, smtp, dominio]);

  function cuerpo(): BuzonIn {
    return {
      preset,
      usuario: usuario.trim(),
      password: password || undefined,
      carpeta: carpeta.trim() || "INBOX",
      ...(preset === "otro" ? { imap, smtp } : {}),
    };
  }

  function cambio() {
    setPrueba(null);
    setError(null);
  }

  async function probar() {
    setProbando(true);
    setError(null);
    try {
      setPrueba(await probarBuzon(cuerpo()));
    } catch (e) {
      setError(errorDetail(e, "No se pudo probar la conexión."));
    } finally {
      setProbando(false);
    }
  }

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      await conectarBuzon({
        ...cuerpo(),
        auto_send: autoSend,
        no_responder: noResponder.split(/[\n,]/).map((s) => s.trim()).filter(Boolean),
      });
      onConectado();
    } catch (e) {
      setError(errorDetail(e, "No se pudo conectar el buzón."));
    } finally {
      setGuardando(false);
    }
  }

  const listo = prueba?.imap_ok && prueba?.smtp_ok;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 backdrop-blur-[1px] p-4">
      <div className="bg-card border border-line rounded-coro shadow-coro-3 w-full max-w-xl flex flex-col max-h-[92vh]">
        <header className="px-5 py-3 border-b border-line flex items-center gap-2 shrink-0">
          <Mail className="w-4 h-4 text-[#EA4335]" />
          <h2 className="font-display text-xl text-ink flex-1">{esImap ? "Editar el buzón" : "Conectar el buzón de correo"}</h2>
          <button type="button" onClick={onClose} className="text-ink3 hover:text-ink" aria-label="Cerrar">
            <X className="w-4 h-4" />
          </button>
        </header>

        <div className="p-5 space-y-4 overflow-auto">
          <div>
            <span className="label">Proveedor</span>
            <div className="flex flex-wrap gap-1.5">
              {[...presets.map((p) => ({ id: p.id, nombre: p.nombre })), { id: "otro", nombre: "Otro" }].map((p) => (
                <button
                  key={p.id}
                  type="button"
                  aria-pressed={preset === p.id}
                  onClick={() => {
                    setPreset(p.id);
                    cambio();
                  }}
                  className={
                    "px-3 py-1.5 rounded-coro-sm border text-xs transition-colors " +
                    (preset === p.id ? "border-brand-ink bg-brand-soft text-brand-ink font-medium" : "border-line text-ink2 hover:bg-paper2")
                  }
                >
                  {p.nombre}
                </button>
              ))}
            </div>
            {elegido && <p className="text-[11px] text-ink3 mt-1.5">{elegido.ayuda}</p>}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor="correo-usuario">Correo</label>
              <input
                id="correo-usuario"
                className="input"
                type="email"
                autoComplete="off"
                value={usuario}
                placeholder="recepcion@tuclinica.mx"
                onChange={(e) => {
                  setUsuario(e.target.value);
                  cambio();
                }}
              />
            </div>
            <div>
              <label className="label" htmlFor="correo-password">Contraseña de aplicación</label>
              <input
                id="correo-password"
                className="input font-mono"
                type="password"
                autoComplete="new-password"
                value={password}
                placeholder={esImap ? "(la guardada)" : ""}
                onChange={(e) => {
                  setPassword(e.target.value);
                  cambio();
                }}
              />
            </div>
          </div>

          {pareceMicrosoft && (
            <div className="flex gap-2 text-xs text-ink2 bg-state-warn/10 border border-state-warn/40 rounded-coro-sm px-3 py-2">
              <AlertTriangle className="w-4 h-4 shrink-0 text-state-warn" />
              <span>
                Outlook y Microsoft 365 no aceptan contraseña de aplicación: Microsoft apagó ese acceso.
                Usa un buzón de tu hosting (o de Gmail o Zoho) para el CRM.
              </span>
            </div>
          )}

          {preset === "otro" ? (
            <div className="space-y-2">
              <Servidor titulo="Servidor de entrada (IMAP)" valor={imap} onChange={(v) => { setImap(v); cambio(); }} />
              <Servidor titulo="Servidor de salida (SMTP)" valor={smtp} onChange={(v) => { setSmtp(v); cambio(); }} />
            </div>
          ) : (
            <p className="text-[11px] text-ink3 font-mono">
              IMAP {servidores.imap.host}:{servidores.imap.port} ({servidores.imap.seguridad.toUpperCase()}) · SMTP{" "}
              {servidores.smtp.host}:{servidores.smtp.port} ({servidores.smtp.seguridad.toUpperCase()})
            </p>
          )}

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="label" htmlFor="correo-carpeta">Carpeta de entrada</label>
              <input
                id="correo-carpeta"
                className="input font-mono text-xs"
                value={carpeta}
                onChange={(e) => {
                  setCarpeta(e.target.value);
                  cambio();
                }}
              />
            </div>
            <div>
              <span className="label">Enviados</span>
              <div className="text-xs text-ink2 py-2">
                {prueba?.carpeta_enviados ?? (esImap && cfg.carpeta_enviados ? String(cfg.carpeta_enviados) : "Se detecta al probar")}
              </div>
            </div>
          </div>

          <label className="flex items-start gap-2.5 text-sm text-ink cursor-pointer">
            <input
              type="checkbox"
              className="mt-0.5"
              checked={autoSend}
              onChange={(e) => setAutoSend(e.target.checked)}
            />
            <span>
              <b>Sofía envía sola</b>
              <span className="block text-[11px] text-ink3">
                Apagado, Sofía deja un borrador en el chat y tú decides si lo mandas. En Entrenamiento
                nunca envía, aunque esté encendido.
              </span>
            </span>
          </label>

          <div>
            <label className="label" htmlFor="correo-no-responder">No responder nunca a</label>
            <textarea
              id="correo-no-responder"
              className="input font-mono text-xs min-h-[64px]"
              value={noResponder}
              placeholder={"facturas@proveedor.com\n@banco.mx"}
              onChange={(e) => setNoResponder(e.target.value)}
            />
            <p className="text-[11px] text-ink3 mt-1">
              Una dirección o un dominio por línea. Las listas de correo, las respuestas automáticas y
              los remitentes «no-reply» ya se apartan solos.
            </p>
          </div>

          <p className="text-[11px] text-ink3 bg-paper2 border border-line rounded-coro-sm px-3 py-2">
            Límites de envío de Gmail: 500 correos al día en cuentas personales; 2,000 al día y 100
            destinatarios por correo en Google Workspace. Microsoft 365 apaga el envío con contraseña a
            finales de 2026.
          </p>

          <div ref={resultadoRef} />
          {prueba && (
            <div className="space-y-1 text-xs">
              <div className="flex gap-4">
                <span className={prueba.imap_ok ? "text-state-ok" : "text-state-bad"}>
                  {prueba.imap_ok ? <Check className="inline w-3.5 h-3.5" /> : <X className="inline w-3.5 h-3.5" />} Entrada (IMAP)
                </span>
                <span className={prueba.smtp_ok ? "text-state-ok" : "text-state-bad"}>
                  {prueba.smtp_ok ? <Check className="inline w-3.5 h-3.5" /> : <X className="inline w-3.5 h-3.5" />} Salida (SMTP)
                </span>
              </div>
              {prueba.errores.map((e) => (
                <div key={e} className="text-state-bad">{e}</div>
              ))}
            </div>
          )}
          {error && (
            <div className="text-sm text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
              {error}
            </div>
          )}
        </div>

        <footer className="px-5 py-3 border-t border-line flex flex-wrap justify-end gap-2 bg-paper2 shrink-0">
          <button type="button" onClick={onClose} className="btn-ghost">
            Cancelar
          </button>
          <button type="button" onClick={probar} disabled={probando || !usuario.trim()} className="btn">
            {probando ? <Loader2 className="animate-spin" /> : <Plug />}
            Probar conexión
          </button>
          <button type="button" onClick={guardar} disabled={!listo || guardando} className="btn-primary" title={listo ? "" : "Primero prueba la conexión"}>
            {guardando ? <Loader2 className="animate-spin" /> : <Check />}
            Guardar
          </button>
        </footer>
      </div>
    </div>
  );
}
