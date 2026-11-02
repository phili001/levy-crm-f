// Conexiones → n8n: las herramientas de n8n que Sofía puede usar (Fase 11).
//
// El secreto lo genera el CRM y se pega en n8n (Webhook → Authentication →
// Header Auth). No sale entero en el listado: «Copiar» lo pide aparte y queda
// apuntado. «Probar» llama al flujo con `prueba: true` y no le escribe a nadie.

import { useEffect, useState } from "react";
import { Check, Copy, FlaskConical, Loader2, Pencil, Plus, RefreshCw, Trash2, Workflow } from "lucide-react";
import { ConfirmModal } from "@/components/ConfirmModal";
import { errorDetail } from "@/lib/errors";
import {
  createN8nTool,
  deleteN8nTool,
  getN8nToolSecret,
  listN8nTools,
  rotateN8nToolSecret,
  tryN8nTool,
  updateN8nTool,
  type N8nToolIn,
  type N8nToolOut,
  type ProbarOut,
} from "@/services/n8n";
import { N8nToolForm } from "./N8nToolForm";

export function N8nToolsPanel() {
  const [tools, setTools] = useState<N8nToolOut[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editando, setEditando] = useState<N8nToolOut | "nueva" | null>(null);
  const [copiado, setCopiado] = useState<string | null>(null);
  const [recienCreada, setRecienCreada] = useState<N8nToolOut | null>(null);
  const [borrar, setBorrar] = useState<N8nToolOut | null>(null);
  const [regenerar, setRegenerar] = useState<N8nToolOut | null>(null);
  const [probando, setProbando] = useState<N8nToolOut | null>(null);

  async function refrescar() {
    setError(null);
    try {
      setTools(await listN8nTools());
    } catch (e) {
      setError(errorDetail(e, "No se pudieron cargar las herramientas."));
    } finally {
      setCargando(false);
    }
  }

  useEffect(() => {
    void refrescar();
  }, []);

  async function guardar(datos: N8nToolIn) {
    if (editando && editando !== "nueva") {
      await updateN8nTool(editando.id, datos);
    } else {
      setRecienCreada(await createN8nTool(datos));
    }
    setEditando(null);
    await refrescar();
  }

  async function copiarSecreto(t: N8nToolOut) {
    try {
      const { secreto } = await getN8nToolSecret(t.id);
      await navigator.clipboard.writeText(secreto);
      setCopiado(t.id);
      window.setTimeout(() => setCopiado(null), 1500);
    } catch (e) {
      setError(errorDetail(e, "No se pudo copiar el secreto."));
    }
  }

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-ink3 leading-relaxed flex-1">
          Herramientas que son <span className="font-medium text-ink2">flujos de n8n</span>: Sofía las
          usa como las suyas (buscar un pedido, mandar la foto de un tratamiento…). El CRM llama a tu
          webhook con un secreto en la cabecera <code>X-Omnicanal-Token</code>, y lo que contesta el
          flujo le llega a Sofía como dato; si devuelve archivos, el CRM se los manda al paciente. Para
          que un agente la use, actívala en Asistentes → el agente → Tools disponibles.
        </p>
        <button type="button" className="btn-primary shrink-0" onClick={() => setEditando("nueva")}>
          <Plus className="w-4 h-4" /> Nueva
        </button>
      </div>

      {error && <div className="text-xs text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">{error}</div>}

      {recienCreada && (
        <div className="card p-4 border-brand/50 space-y-2">
          <div className="text-sm font-medium text-ink">Ahora pega el secreto en n8n</div>
          <ol className="text-xs text-ink2 list-decimal pl-4 space-y-1">
            <li>En el nodo Webhook de tu flujo: Authentication → <b>Header Auth</b> → crear credencial.</li>
            <li>
              Name: <code>X-Omnicanal-Token</code> · Value: el secreto (botón de abajo).
            </li>
            <li>Publica el flujo (botón Publish) y vuelve aquí a darle «Probar».</li>
          </ol>
          <div className="flex gap-2">
            <button type="button" className="btn" onClick={() => void copiarSecreto(recienCreada)}>
              {copiado === recienCreada.id ? <Check className="w-4 h-4 text-state-ok" /> : <Copy className="w-4 h-4" />}
              {copiado === recienCreada.id ? "Copiado" : "Copiar el secreto"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setRecienCreada(null)}>
              Hecho
            </button>
          </div>
        </div>
      )}

      {editando && (
        <N8nToolForm
          inicial={editando === "nueva" ? null : editando}
          onGuardar={guardar}
          onCancelar={() => setEditando(null)}
        />
      )}

      {cargando ? (
        <div className="flex items-center gap-2 text-ink3 text-sm py-6 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> Cargando…
        </div>
      ) : tools.length === 0 && !editando ? (
        <div className="card p-6 text-center text-sm text-ink3">
          <Workflow className="w-5 h-5 mx-auto mb-2 text-ink3" />
          Todavía no hay herramientas de n8n. La guía para armar el flujo está en <code>docs/n8n.md</code>.
        </div>
      ) : (
        <ul className="space-y-2">
          {tools.map((t) => (
            <li key={t.id} className="card p-3">
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-mono text-sm text-ink">{t.nombre}</span>
                    <span className="text-[10px] uppercase tracking-wider text-ink3 border border-line rounded px-1">
                      {t.accion === "lectura" ? "consulta" : "hace algo"}
                    </span>
                    {!t.activa && (
                      <span className="text-[10px] uppercase tracking-wider text-state-warn border border-state-warn/40 rounded px-1">
                        apagada
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-ink3 line-clamp-2 mt-0.5">{t.descripcion}</div>
                  <div className="text-[11px] text-ink3 font-mono truncate mt-0.5">{t.url}</div>
                  <div className="text-[11px] text-ink3 mt-0.5">Secreto {t.secreto_enmascarado}</div>
                </div>
                <div className="flex flex-col gap-1 shrink-0">
                  <button type="button" className="btn-ghost btn-sm" onClick={() => setProbando(t)}>
                    <FlaskConical className="w-3.5 h-3.5" /> Probar
                  </button>
                  <button type="button" className="btn-ghost btn-sm" onClick={() => setEditando(t)}>
                    <Pencil className="w-3.5 h-3.5" /> Editar
                  </button>
                  <button type="button" className="btn-ghost btn-sm" onClick={() => void copiarSecreto(t)}>
                    {copiado === t.id ? <Check className="w-3.5 h-3.5 text-state-ok" /> : <Copy className="w-3.5 h-3.5" />}
                    Secreto
                  </button>
                  <button type="button" className="btn-ghost btn-sm" onClick={() => setRegenerar(t)}>
                    <RefreshCw className="w-3.5 h-3.5" /> Regenerar
                  </button>
                  <button type="button" className="btn-ghost btn-sm text-state-bad" onClick={() => setBorrar(t)}>
                    <Trash2 className="w-3.5 h-3.5" /> Borrar
                  </button>
                </div>
              </div>
              {probando?.id === t.id && <Probar tool={t} onCerrar={() => setProbando(null)} />}
            </li>
          ))}
        </ul>
      )}

      <ConfirmModal
        open={borrar !== null}
        title="¿Borrar la herramienta?"
        tone="danger"
        description={borrar ? `Sofía deja de poder usar «${borrar.nombre}» desde el siguiente mensaje.` : ""}
        confirmLabel="Borrar"
        onConfirm={() => {
          const t = borrar;
          setBorrar(null);
          if (t) void deleteN8nTool(t.id).then(refrescar).catch((e) => setError(errorDetail(e, "No se pudo borrar.")));
        }}
        onCancel={() => setBorrar(null)}
      />
      <ConfirmModal
        open={regenerar !== null}
        title="¿Regenerar el secreto?"
        description="El secreto actual deja de valer al momento: hasta que pegues el nuevo en n8n, la herramienta fallará."
        confirmLabel="Regenerar"
        onConfirm={() => {
          const t = regenerar;
          setRegenerar(null);
          if (t)
            void rotateN8nToolSecret(t.id)
              .then((nueva) => {
                setRecienCreada(nueva);
                return refrescar();
              })
              .catch((e) => setError(errorDetail(e, "No se pudo regenerar.")));
        }}
        onCancel={() => setRegenerar(null)}
      />
    </div>
  );
}

function Probar({ tool, onCerrar }: { tool: N8nToolOut; onCerrar: () => void }) {
  const [args, setArgs] = useState<Record<string, string>>({});
  const [resultado, setResultado] = useState<ProbarOut | null>(null);
  const [enCurso, setEnCurso] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function probar() {
    setEnCurso(true);
    setError(null);
    try {
      const valores: Record<string, unknown> = {};
      for (const p of tool.parametros) {
        const v = args[p.clave];
        if (v === undefined || v === "") continue;
        valores[p.clave] = p.tipo === "numero" ? Number(v) : p.tipo === "si_no" ? v === "si" : v;
      }
      setResultado(await tryN8nTool(tool.id, valores));
    } catch (e) {
      setError(errorDetail(e, "No se pudo probar."));
    } finally {
      setEnCurso(false);
    }
  }

  const archivos =
    resultado?.respuesta && typeof resultado.respuesta === "object" && "archivos" in resultado.respuesta
      ? ((resultado.respuesta as { archivos?: unknown[] }).archivos ?? [])
      : [];

  return (
    <div className="mt-3 border-t border-line pt-3 space-y-2">
      <p className="text-[11px] text-ink3">
        Se llama a tu flujo con <code>prueba: true</code>. No se le manda nada a ningún paciente.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {tool.parametros.map((p) => (
          <label key={p.clave} className="block">
            <span className="block text-[11px] text-ink3 font-mono">{p.clave}</span>
            {p.tipo === "opciones" ? (
              <select className="input text-xs w-full" value={args[p.clave] ?? ""} onChange={(e) => setArgs({ ...args, [p.clave]: e.target.value })}>
                <option value="">—</option>
                {(p.opciones ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            ) : p.tipo === "si_no" ? (
              <select className="input text-xs w-full" value={args[p.clave] ?? ""} onChange={(e) => setArgs({ ...args, [p.clave]: e.target.value })}>
                <option value="">—</option>
                <option value="si">Sí</option>
                <option value="no">No</option>
              </select>
            ) : (
              <input className="input text-xs w-full" value={args[p.clave] ?? ""} onChange={(e) => setArgs({ ...args, [p.clave]: e.target.value })} />
            )}
          </label>
        ))}
      </div>
      <div className="flex gap-2">
        <button type="button" className="btn-primary btn-sm" onClick={() => void probar()} disabled={enCurso}>
          {enCurso ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FlaskConical className="w-3.5 h-3.5" />} Probar
        </button>
        <button type="button" className="btn-ghost btn-sm" onClick={onCerrar}>
          Cerrar
        </button>
      </div>
      {error && <p className="text-xs text-state-bad">{error}</p>}
      {resultado && (
        <div className={`text-xs rounded-coro-sm border p-2 ${resultado.ok ? "border-state-ok/40 bg-state-ok/10" : "border-state-bad/40 bg-state-bad/10"}`}>
          <div className="font-medium text-ink mb-1">
            {resultado.ok ? `Contestó en ${resultado.ms} ms (HTTP ${resultado.estado})` : resultado.error}
          </div>
          {archivos.length > 0 && (
            <div className="text-ink2 mb-1">
              Devolvió {archivos.length} archivo(s): en una conversación real se le mandarían al paciente.
            </div>
          )}
          {resultado.respuesta !== null && resultado.respuesta !== undefined && (
            <pre className="font-mono text-[11px] whitespace-pre-wrap break-words max-h-48 overflow-auto text-ink2">
              {typeof resultado.respuesta === "string" ? resultado.respuesta : JSON.stringify(resultado.respuesta, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}
