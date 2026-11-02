import { useEffect, useState } from "react";
import { Check, History, Info, Loader2, MessageCircle, Plug, Send, X } from "lucide-react";

import { errorDetail } from "@/lib/errors";
import {
  conectarZernio,
  estadoZernio,
  probarWebhookZernio,
  probarZernio,
  traerHistorialZernio,
  type ConectarZernioOut,
  type CuentaZernio,
  type EstadoZernio,
} from "@/services/zernio";
import { SetupUrlRow } from "./shared";

/**
 * Conectar Instagram y Messenger con la cuenta de Zernio de la clínica
 * (backend/app/api/zernio_admin.py). Sin app de Meta ni App Review: la clínica
 * conecta sus cuentas en Zernio y aquí solo pega la API key.
 *
 * Probar antes de guardar, como en el correo: sin cuentas detectadas no hay
 * nada que conectar, y guardar a ciegas dejaría un canal en el que no entra
 * nada. El aviso de mensajes (webhook) se registra solo; si Zernio no lo
 * acepta, se enseñan la URL y el secreto para pegarlos a mano.
 */

const PLATAFORMA: Record<string, string> = {
  instagram: "Instagram",
  facebook: "Facebook",
  whatsapp: "WhatsApp",
};

function nombreCuenta(c: CuentaZernio): string {
  const base = c.platform === "instagram" && c.username ? `@${c.username}` : c.display_name || c.username;
  return base || c.id;
}

function Cuentas({
  plataforma,
  cuentas,
  elegida,
  onElegir,
}: {
  plataforma: "instagram" | "facebook";
  cuentas: CuentaZernio[];
  elegida: string;
  onElegir: (id: string) => void;
}) {
  const de = cuentas.filter((c) => c.platform === plataforma);
  return (
    <div>
      <span className="label">{PLATAFORMA[plataforma]}</span>
      {de.length === 0 ? (
        <p className="text-xs text-ink3">
          Ninguna cuenta de {PLATAFORMA[plataforma]} conectada en Zernio.
        </p>
      ) : (
        <div className="space-y-1">
          {de.map((c) => (
            <label key={c.id} className="flex items-center gap-2 text-sm text-ink cursor-pointer">
              <input
                type="radio"
                name={`cuenta-${plataforma}`}
                checked={elegida === c.id}
                onChange={() => onElegir(c.id)}
              />
              <span className="flex-1 truncate">{nombreCuenta(c)}</span>
              {c.needs_reconnection ? (
                <span className="pill bg-state-bad/10 text-state-bad">Reconectar en Zernio</span>
              ) : (
                <Check className="w-3.5 h-3.5 text-state-ok" />
              )}
            </label>
          ))}
          {de.length > 0 && (
            <label className="flex items-center gap-2 text-xs text-ink3 cursor-pointer">
              <input
                type="radio"
                name={`cuenta-${plataforma}`}
                checked={elegida === ""}
                onChange={() => onElegir("")}
              />
              No conectar {PLATAFORMA[plataforma]}
            </label>
          )}
        </div>
      )}
    </div>
  );
}

export function ZernioModal({
  hayWhatsapp,
  onClose,
  onConectado,
}: {
  /** Si ya existe el canal de WhatsApp (para ofrecer mandarlo por Zernio). */
  hayWhatsapp: boolean;
  onClose: () => void;
  onConectado: () => void;
}) {
  const [estado, setEstado] = useState<EstadoZernio | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [cuentas, setCuentas] = useState<CuentaZernio[] | null>(null);
  const [ig, setIg] = useState("");
  const [fb, setFb] = useState("");
  const [waPorZernio, setWaPorZernio] = useState(false);
  const [probando, setProbando] = useState(false);
  const [guardando, setGuardando] = useState(false);
  const [probandoWebhook, setProbandoWebhook] = useState(false);
  const [webhookOk, setWebhookOk] = useState(false);
  const [trayendo, setTrayendo] = useState(false);
  const [historialPedido, setHistorialPedido] = useState(false);
  const [resultado, setResultado] = useState<ConectarZernioOut | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    estadoZernio()
      .then((e) => {
        setEstado(e);
        setWaPorZernio(e.wa_por_zernio);
      })
      .catch((e) => setError(errorDetail(e, "No se pudo leer el estado de Zernio.")));
  }, []);

  async function probar() {
    setProbando(true);
    setError(null);
    try {
      const lista = await probarZernio(apiKey.trim());
      setCuentas(lista);
      // Cuenta ya conectada o, si hay una sola por plataforma, esa.
      const actual = (tipo: string) => estado?.canales.find((c) => c.tipo === tipo)?.account_id ?? "";
      const unica = (p: string) => {
        const de = lista.filter((c) => c.platform === p);
        return de.length === 1 ? de[0].id : "";
      };
      setIg(actual("instagram_dm") || unica("instagram"));
      setFb(actual("messenger") || unica("facebook"));
    } catch (e) {
      setCuentas(null);
      setError(errorDetail(e, "No se pudo probar la API key."));
    } finally {
      setProbando(false);
    }
  }

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      const out = await conectarZernio({
        api_key: apiKey.trim() || undefined,
        instagram_account_id: ig || undefined,
        facebook_account_id: fb || undefined,
        wa_por_zernio: waPorZernio,
      });
      if (out.webhook_registrado) {
        onConectado();
        return;
      }
      // El registro automático falló: hay que pegarlo a mano. Se queda abierto.
      setResultado(out);
    } catch (e) {
      setError(errorDetail(e, "No se pudo conectar."));
    } finally {
      setGuardando(false);
    }
  }

  async function probarWebhook() {
    setProbandoWebhook(true);
    setError(null);
    try {
      await probarWebhookZernio();
      setWebhookOk(true);
    } catch (e) {
      setError(errorDetail(e, "Zernio no pudo mandar la prueba."));
    } finally {
      setProbandoWebhook(false);
    }
  }

  async function traerHistorial() {
    setTrayendo(true);
    setError(null);
    try {
      await traerHistorialZernio();
      setHistorialPedido(true);
    } catch (e) {
      setError(errorDetail(e, "No se pudo pedir el historial."));
    } finally {
      setTrayendo(false);
    }
  }

  const hayWaEnZernio = (cuentas ?? []).some((c) => c.platform === "whatsapp");
  const listo = cuentas !== null && Boolean(ig || fb);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 backdrop-blur-[1px] p-4">
      <div className="bg-card border border-line rounded-coro shadow-coro-3 w-full max-w-xl flex flex-col max-h-[92vh]">
        <header className="px-5 py-3 border-b border-line flex items-center gap-2 shrink-0">
          <MessageCircle className="w-4 h-4 text-[#0084FF]" />
          <h2 className="font-display text-xl text-ink flex-1">Instagram y Messenger (Zernio)</h2>
          <button type="button" onClick={onClose} className="text-ink3 hover:text-ink" aria-label="Cerrar">
            <X className="w-4 h-4" />
          </button>
        </header>

        <div className="p-5 space-y-4 overflow-auto">
          <p className="text-xs text-ink3 leading-relaxed">
            Conecta primero tu Instagram y tu página de Facebook en tu cuenta de Zernio
            (Connections: es entrar con tu usuario de Meta, sin crear ninguna app). Después crea una
            clave en Zernio (API keys → Create API key) y pégala aquí.
          </p>

          <div>
            <label className="label" htmlFor="zernio-key">API key de Zernio</label>
            <div className="flex gap-2">
              <input
                id="zernio-key"
                className="input font-mono flex-1"
                type="password"
                autoComplete="off"
                value={apiKey}
                placeholder={estado?.tiene_llave ? "(la guardada)" : "sk_..."}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setCuentas(null);
                }}
              />
              <button
                type="button"
                onClick={probar}
                disabled={probando || (!apiKey.trim() && !estado?.tiene_llave)}
                className="btn"
              >
                {probando ? <Loader2 className="animate-spin" /> : <Plug />}
                Probar
              </button>
            </div>
          </div>

          {cuentas !== null && (
            <div className="space-y-3 rounded-coro-sm border border-line p-3">
              <div className="text-xs font-medium text-ink2">Cuentas conectadas en Zernio</div>
              <Cuentas plataforma="instagram" cuentas={cuentas} elegida={ig} onElegir={setIg} />
              <Cuentas plataforma="facebook" cuentas={cuentas} elegida={fb} onElegir={setFb} />
              {hayWhatsapp && (
                <label className="flex items-start gap-2.5 text-sm text-ink cursor-pointer">
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={waPorZernio}
                    disabled={!hayWaEnZernio && !waPorZernio}
                    onChange={(e) => setWaPorZernio(e.target.checked)}
                  />
                  <span>
                    <b>WhatsApp por Zernio</b> (en vez de YCloud)
                    <span className="block text-[11px] text-ink3">
                      {hayWaEnZernio
                        ? "Los mensajes de WhatsApp entran y se contestan por Zernio. Las plantillas y los envíos masivos siguen por YCloud."
                        : "No hay ningún número de WhatsApp conectado en Zernio."}
                    </span>
                  </span>
                </label>
              )}
            </div>
          )}

          {estado && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-ink3">Aviso de mensajes:</span>
              {estado.webhook_id ? (
                <>
                  <span className="text-state-ok">registrado por API ✓</span>
                  <span className="font-mono text-ink3">id {estado.webhook_id.slice(0, 6)}…</span>
                  <button
                    type="button"
                    onClick={probarWebhook}
                    disabled={probandoWebhook}
                    className="btn-ghost text-xs ml-auto inline-flex items-center gap-1.5"
                  >
                    {probandoWebhook ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : webhookOk ? (
                      <Check className="w-3.5 h-3.5 text-state-ok" />
                    ) : (
                      <Send className="w-3.5 h-3.5" />
                    )}
                    {webhookOk ? "Prueba enviada" : "Probar aviso"}
                  </button>
                </>
              ) : (
                <span className="text-ink3">se registra solo al guardar</span>
              )}
            </div>
          )}

          {estado && estado.canales.length > 0 && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-ink3 flex-1">
                {historialPedido
                  ? "Trayendo el historial en segundo plano: tarda unos minutos si hay mucho."
                  : "Historial: se trae solo al conectar. Si se quedó a medias, vuelve a pedirlo; no duplica nada."}
              </span>
              <button
                type="button"
                onClick={traerHistorial}
                disabled={trayendo || historialPedido}
                className="btn-ghost text-xs inline-flex items-center gap-1.5"
              >
                {trayendo ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <History className="w-3.5 h-3.5" />}
                Volver a traer el historial
              </button>
            </div>
          )}

          <div className="text-[11px] text-ink2 bg-paper2 border border-line rounded-coro-sm px-3 py-2 space-y-1">
            <div className="flex gap-1.5">
              <Info className="w-3.5 h-3.5 shrink-0 mt-px text-ink3" />
              <span>
                Antes de conectar Instagram, <b>desconecta ManyChat u otro bot</b> de la cuenta: sus
                mensajes llegan igual que los de una persona y apartarían a Sofía.
              </span>
            </div>
            <div className="flex gap-1.5">
              <Info className="w-3.5 h-3.5 shrink-0 mt-px text-ink3" />
              <span>
                Messenger no entrega mensajes de cuentas del mismo portafolio de Meta: para probar,
                escribe desde una cuenta personal ajena a la página.
              </span>
            </div>
            <div className="flex gap-1.5">
              <Info className="w-3.5 h-3.5 shrink-0 mt-px text-ink3" />
              <span>
                Los mensajes de Instagram y Facebook pasan por Zernio (España). Queda dicho en tu
                aviso de privacidad.
              </span>
            </div>
          </div>

          {resultado && resultado.webhook_secret_manual && (
            <div className="space-y-2 rounded-coro-sm border border-state-warn/40 bg-state-warn/10 p-3">
              <div className="text-xs text-ink2">{resultado.aviso}</div>
              <SetupUrlRow
                label="URL del aviso de mensajes"
                url={resultado.webhook_url}
                donde="Zernio → Settings → Webhooks → Add. Eventos: message.received, message.sent, account.connected y account.disconnected."
              />
              <SetupUrlRow label="Secreto (Signing secret)" url={resultado.webhook_secret_manual} />
              <div className="flex justify-end">
                <button type="button" onClick={onConectado} className="btn-primary text-xs">
                  Ya lo pegué
                </button>
              </div>
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
          <button
            type="button"
            onClick={guardar}
            disabled={!listo || guardando || Boolean(resultado)}
            className="btn-primary"
            title={listo ? "" : "Primero prueba la API key y elige al menos una cuenta"}
          >
            {guardando ? <Loader2 className="animate-spin" /> : <Check />}
            Conectar
          </button>
        </footer>
      </div>
    </div>
  );
}
