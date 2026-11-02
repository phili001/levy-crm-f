import { useEffect, useState } from "react";
import { Send, X } from "lucide-react";

import {
  InlineTemplateFill,
  TemplatePreview,
  emptyVarNumbers,
} from "@/components/TemplateVarsFiller";
import { errorDetail } from "@/lib/errors";
import {
  CONTACT_FIELD_OPTIONS,
  getTemplateVars,
  type TemplateVar,
  type WhatsappTemplate,
} from "@/services/admin";
import type { Contact } from "@/types";

/**
 * Mandar una plantilla de WhatsApp a UNA persona. Lo usan la ficha del
 * contacto y el chat (cuando la ventana de 24 h se ha cerrado); cada uno pone
 * de dónde salen las plantillas y por dónde se envían.
 */

// Campos del contacto enlazables (debe coincidir con el set permitido del
// backend). Lee SOLO estos campos de la ficha para autorrellenar; nunca
// atributos arbitrarios del contacto.
const ALLOWED_AUTOFILL_FIELDS = new Set(
  CONTACT_FIELD_OPTIONS.map((o) => o.value).filter((v) => v),
);

// Lee un campo permitido del contacto y lo serializa a string visible.
// (Los enums ya llegan como su valor string en el tipo Contact del front.)
function contactFieldValue(contact: Partial<Contact> | null, field: string): string {
  if (!contact || !ALLOWED_AUTOFILL_FIELDS.has(field)) return "";
  const raw = (contact as unknown as Record<string, unknown>)[field];
  if (raw == null) return "";
  return String(raw).trim();
}

interface Props {
  // Para autorrellenar las variables enlazadas a un campo de la ficha.
  contact: Partial<Contact> | null;
  // A quién va, tal como se enseña arriba del formulario.
  destino: string;
  // De dónde salen las plantillas: la ficha (admin) ve todas; el chat solo
  // las que se pueden mandar sin fichero.
  cargar: () => Promise<WhatsappTemplate[]>;
  // Lanza con el motivo si no sale: el modal lo enseña tal cual.
  enviar: (plantilla: WhatsappTemplate, variables: string[]) => Promise<void>;
  onSent?: () => void;
  onClose: () => void;
}

export function TemplateSendModal({ contact, destino, cargar, enviar, onSent, onClose }: Props) {
  const [templates, setTemplates] = useState<WhatsappTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [sel, setSel] = useState<WhatsappTemplate | null>(null);
  // Array ORDENADO: vars[idx-1] -> {{idx}}. Es lo que se envía (sin cambios).
  const [vars, setVars] = useState<string[]>([]);
  // Config de etiquetas amigables + enlaces (para labels y autorrelleno).
  const [varConfig, setVarConfig] = useState<TemplateVar[]>([]);
  const [sending, setSending] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    cargar()
      .then(setTemplates)
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false));
  }, []);

  async function pick(name: string) {
    const t = templates.find((x) => x.name === name) || null;
    setSel(t);
    setResult(null);
    if (!t || t.variables < 1) {
      setVars(t ? Array(t.variables).fill("") : []);
      setVarConfig([]);
      return;
    }
    // Inicializa el array ordenado vacío y carga la config; luego autorrellena
    // desde el contacto ya cargado (cliente, sin llamar a /resolve).
    const base = Array(t.variables).fill("");
    setVars(base);
    setVarConfig([]);
    try {
      const cfg = await getTemplateVars(t.name, t.language);
      setVarConfig(cfg);
      const next = [...base];
      for (const v of cfg) {
        if (v.idx < 1 || v.idx > t.variables) continue;
        if (v.contact_field && ALLOWED_AUTOFILL_FIELDS.has(v.contact_field)) {
          const val = contactFieldValue(contact, v.contact_field);
          if (val) next[v.idx - 1] = val;
        }
      }
      setVars(next);
    } catch {
      // Sin config: se queda con labels "Variable n" y relleno manual.
      setVarConfig([]);
    }
  }

  function setVar(varNumber: number, value: string) {
    setVars((prev) => prev.map((v, i) => (i === varNumber - 1 ? value : v)));
  }

  async function send() {
    if (!sel) return;
    if (sel.variables > 0 && emptyVarNumbers(vars, sel.variables).length > 0) {
      setResult({ ok: false, text: "Rellena todas las variables de la plantilla." });
      return;
    }
    setSending(true);
    setResult(null);
    try {
      // CONTRATO: array ORDENADO por idx. NO cambia.
      await enviar(sel, vars);
      setResult({ ok: true, text: "Plantilla enviada." });
      onSent?.();
    } catch (e) {
      setResult({ ok: false, text: errorDetail(e, "No se pudo enviar la plantilla.") });
    } finally {
      setSending(false);
    }
  }

  const hasSlots = !!sel?.body && /\{\{\s*\d+\s*\}\}/.test(sel.body);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 backdrop-blur-[1px] p-4"
      onClick={onClose}
    >
      <div className="card w-full max-w-md flex flex-col max-h-[90vh]" onClick={(e) => e.stopPropagation()}>
        <header className="px-5 py-3 border-b border-line flex items-center gap-2 shrink-0">
          <Send className="w-4 h-4 text-brand-ink" />
          <h2 className="font-display text-lg text-ink flex-1">Enviar plantilla por WhatsApp</h2>
          <button type="button" onClick={onClose} className="text-ink3 hover:text-ink" aria-label="Cerrar">
            <X className="w-4 h-4" />
          </button>
        </header>
        <div className="p-5 space-y-3 overflow-auto">
          <div className="text-sm text-ink2">
            A <b className="text-ink font-mono">{destino}</b>
          </div>
          {loading ? (
            <div className="text-ink3 text-sm font-display">Cargando plantillas…</div>
          ) : templates.length === 0 ? (
            <div className="text-ink3 text-sm">
              No hay plantillas disponibles. Revisa las credenciales de YCloud y que tengas plantillas aprobadas.
            </div>
          ) : (
            <>
              <div>
                <label className="label">Plantilla</label>
                <select className="input" value={sel?.name || ""} onChange={(e) => pick(e.target.value)}>
                  <option value="">Elige una…</option>
                  {templates.map((t) => (
                    <option key={t.name + t.language} value={t.name}>
                      {t.name} ({t.language})
                    </option>
                  ))}
                </select>
              </div>
              {sel && sel.variables > 0 && (
                <div className="space-y-2">
                  {/* Relleno "huecos en la frase" con etiquetas amigables. */}
                  <InlineTemplateFill
                    body={sel.body}
                    values={vars}
                    vars={varConfig}
                    onChange={setVar}
                  />
                  {/* Fallback: body sin huecos pero con variables declaradas. */}
                  {!hasSlots &&
                    vars.map((v, i) => (
                      <div key={i}>
                        <label className="label">
                          {varConfig.find((x) => x.idx === i + 1)?.nombre?.trim() || `Variable ${i + 1}`}
                        </label>
                        <input
                          className="input"
                          value={v}
                          onChange={(e) => setVar(i + 1, e.target.value)}
                        />
                      </div>
                    ))}
                </div>
              )}
              {sel && sel.variables > 0 && (
                <TemplatePreview body={sel.body} values={vars} vars={varConfig} />
              )}
              {sel && sel.variables === 0 && sel.body && (
                <div className="rounded-coro-sm bg-paper2 p-3 text-sm text-ink2 whitespace-pre-wrap">
                  {sel.body}
                </div>
              )}
              {result && (
                <div className={result.ok ? "text-state-ok text-sm" : "text-state-bad text-sm"}>{result.text}</div>
              )}
            </>
          )}
        </div>
        <footer className="px-5 py-3 border-t border-line flex justify-end gap-2 bg-paper2">
          <button type="button" onClick={onClose} className="btn-ghost">
            Cerrar
          </button>
          <button type="button" onClick={send} disabled={sending || !sel} className="btn-primary">
            <Send className="w-4 h-4" /> {sending ? "Enviando…" : "Enviar"}
          </button>
        </footer>
      </div>
    </div>
  );
}
