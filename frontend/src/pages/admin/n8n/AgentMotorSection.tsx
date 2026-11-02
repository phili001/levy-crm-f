// Agentes → editar → «Motor» (Fase 11, SPEC-07): quién decide qué contestar.
//
// Con «Un flujo de n8n», cada turno sale al webhook de n8n y lo que conteste
// sigue el camino de una respuesta de Sofía (formato del canal, Entrenamiento,
// envío). El secreto lo genera el CRM al guardar y se pega en n8n (Webhook →
// Header Auth). «Probar» manda un turno de ejemplo con `prueba: true` a la URL
// GUARDADA: si la del formulario es otra, primero hay que guardar.

import { useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Bot, Check, Copy, FlaskConical, Loader2, RefreshCw, Workflow } from "lucide-react";
import { ConfirmModal } from "@/components/ConfirmModal";
import { errorDetail } from "@/lib/errors";
import {
  getAgentN8nSecret,
  rotateAgentN8nSecret,
  tryAgentN8n,
  type AgentMotor,
  type AgentN8nTryOut,
  type AgentOut,
} from "@/services/admin";

const MOTORES: { value: AgentMotor; label: string; hint: string }[] = [
  { value: "crm", label: "Sofía en el CRM", hint: "El prompt, el modelo y las herramientas de abajo." },
  { value: "n8n", label: "Un flujo de n8n", hint: "Tu flujo decide qué contestar; el CRM entrega y muestra." },
];

export interface MotorValor {
  motor: AgentMotor;
  n8n_url: string;
  n8n_espera_s: number;
}

export function AgentMotorSection({
  guardado,
  valor,
  onChange,
  onSecretoRegenerado,
}: {
  // El agente tal como está en el servidor (null si todavía no se ha creado).
  guardado: AgentOut | null;
  valor: MotorValor;
  onChange: (v: MotorValor) => void;
  onSecretoRegenerado: (a: AgentOut) => void;
}) {
  const esN8n = valor.motor === "n8n";
  return (
    <div>
      <label className="block text-xs uppercase tracking-wider text-ink3 font-medium mb-1">Motor</label>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {MOTORES.map((m) => {
          const elegido = valor.motor === m.value;
          return (
            <label
              key={m.value}
              className={
                "flex items-start gap-2 p-2.5 rounded-coro-sm border cursor-pointer transition " +
                (elegido ? "border-brand-ink bg-brand-soft/40" : "border-line hover:bg-paper2")
              }
            >
              <input
                type="radio"
                name="agent-motor"
                className="mt-0.5"
                checked={elegido}
                onChange={() => onChange({ ...valor, motor: m.value })}
              />
              <div>
                <div className="text-sm text-ink2 inline-flex items-center gap-1.5">
                  {m.value === "n8n" ? <Workflow className="w-3.5 h-3.5" /> : <Bot className="w-3.5 h-3.5" />}
                  {m.label}
                </div>
                <div className="text-[11px] text-ink3">{m.hint}</div>
              </div>
            </label>
          );
        })}
      </div>
      {esN8n && (
        <DetalleN8n guardado={guardado} valor={valor} onChange={onChange} onSecretoRegenerado={onSecretoRegenerado} />
      )}
    </div>
  );
}

function DetalleN8n({
  guardado,
  valor,
  onChange,
  onSecretoRegenerado,
}: {
  guardado: AgentOut | null;
  valor: MotorValor;
  onChange: (v: MotorValor) => void;
  onSecretoRegenerado: (a: AgentOut) => void;
}) {
  const [copiado, setCopiado] = useState(false);
  const [regenerar, setRegenerar] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const secreto = guardado?.n8n_secreto_enmascarado ?? "";
  // «Probar» llama a lo GUARDADO: con la URL cambiada en el formulario probaría
  // la vieja y el resultado engañaría.
  const listoParaProbar =
    !!guardado && guardado.motor === "n8n" && (guardado.n8n_url ?? "") === valor.n8n_url.trim();

  async function copiar() {
    if (!guardado) return;
    setError(null);
    try {
      const { secreto: completo } = await getAgentN8nSecret(guardado.id);
      await navigator.clipboard.writeText(completo);
      setCopiado(true);
      window.setTimeout(() => setCopiado(false), 1500);
    } catch (e) {
      setError(errorDetail(e, "No se pudo copiar el secreto."));
    }
  }

  async function confirmarRegenerar() {
    setRegenerar(false);
    if (!guardado) return;
    setError(null);
    try {
      onSecretoRegenerado(await rotateAgentN8nSecret(guardado.id));
    } catch (e) {
      setError(errorDetail(e, "No se pudo regenerar el secreto."));
    }
  }

  return (
    <div className="mt-2 rounded-coro-sm border border-line bg-paper2 p-3 space-y-3">
      <label className="block">
        <span className="block text-[11px] text-ink3 mb-0.5">URL del webhook (la «Production URL» del nodo Webhook)</span>
        <input
          type="url"
          className="input w-full font-mono text-xs"
          value={valor.n8n_url}
          onChange={(e) => onChange({ ...valor, n8n_url: e.target.value })}
          placeholder="https://n8n.miclinica.com/webhook/sofia"
        />
      </label>

      <div>
        <span className="block text-[11px] text-ink3 mb-0.5">
          Secreto (en n8n: Header Auth, Name <code>X-Omnicanal-Token</code>)
        </span>
        {secreto ? (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-mono text-xs text-ink2">{secreto}</span>
            <button type="button" className="btn-ghost btn-sm" onClick={() => void copiar()}>
              {copiado ? <Check className="w-3.5 h-3.5 text-state-ok" /> : <Copy className="w-3.5 h-3.5" />}
              {copiado ? "Copiado" : "Copiar"}
            </button>
            <button type="button" className="btn-ghost btn-sm" onClick={() => setRegenerar(true)}>
              <RefreshCw className="w-3.5 h-3.5" /> Regenerar
            </button>
          </div>
        ) : (
          <p className="text-[11px] text-ink3 italic">Se genera al guardar el agente con este motor.</p>
        )}
      </div>

      <label className="block max-w-[12rem]">
        <span className="block text-[11px] text-ink3 mb-0.5">Espera máxima (s)</span>
        <input
          type="number"
          min={5}
          max={45}
          className="input w-full font-numbers"
          value={valor.n8n_espera_s}
          onChange={(e) => onChange({ ...valor, n8n_espera_s: Number(e.target.value) })}
        />
      </label>

      <p className="text-[11px] text-ink3">
        Para contestar más tarde, pasar la conversación a una persona o cambiar la ficha, n8n necesita
        un token:{" "}
        <Link to="/admin/connections?tab=agent-tokens" className="text-brand-ink underline">
          Conexiones → API / MCP → «Para n8n»
        </Link>
        .
      </p>

      {guardado && listoParaProbar ? (
        <ProbarMotor agenteId={guardado.id} />
      ) : (
        <p className="text-[11px] text-ink3 italic">Guarda el agente para probar el flujo.</p>
      )}

      <p className="text-[11px] text-state-warn flex items-start gap-1.5">
        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" />
        <span>
          Con n8n, el prompt, el modelo, las herramientas y el tope de gasto de este agente{" "}
          <b>no se usan</b>: los decide tu flujo. Tampoco corre la moderación del CRM, y lo que gaste
          n8n no cuenta en Tarifas y límites.
        </span>
      </p>

      {error && <p className="text-xs text-state-bad">{error}</p>}

      <ConfirmModal
        open={regenerar}
        title="¿Regenerar el secreto?"
        description="El secreto actual deja de valer al momento: hasta que pegues el nuevo en n8n, este agente no podrá contestar y las conversaciones pasarán a una persona."
        confirmLabel="Regenerar"
        onConfirm={() => void confirmarRegenerar()}
        onCancel={() => setRegenerar(false)}
      />
    </div>
  );
}

function ProbarMotor({ agenteId }: { agenteId: string }) {
  const [texto, setTexto] = useState("Hola, ¿tienen cita mañana?");
  const [resultado, setResultado] = useState<AgentN8nTryOut | null>(null);
  const [enCurso, setEnCurso] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function probar() {
    setEnCurso(true);
    setError(null);
    try {
      setResultado(await tryAgentN8n(agenteId, texto));
    } catch (e) {
      setError(errorDetail(e, "No se pudo probar."));
    } finally {
      setEnCurso(false);
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          className="input text-xs flex-1"
          value={texto}
          onChange={(e) => setTexto(e.target.value)}
          aria-label="Mensaje de prueba"
        />
        <button type="button" className="btn-primary btn-sm" onClick={() => void probar()} disabled={enCurso}>
          {enCurso ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FlaskConical className="w-3.5 h-3.5" />}{" "}
          Probar
        </button>
      </div>
      <p className="text-[11px] text-ink3">
        Se manda a tu flujo con <code>prueba: true</code>. No se le escribe a ningún paciente.
      </p>
      {error && <p className="text-xs text-state-bad">{error}</p>}
      {resultado && <ResultadoPrueba r={resultado} />}
    </div>
  );
}

// Lo que suele estar mal cuando n8n contesta con error (ver docs/n8n.md).
const PISTAS: Record<string, string> = {
  "403": "El secreto de n8n no es el del CRM: cópialo otra vez a la credencial Header Auth.",
  "404": "El flujo no está publicado o pegaste la «Test URL»: dale Publish y usa la «Production URL».",
  "405": "El webhook no acepta POST: en el nodo Webhook, HTTP Method = POST.",
};

function ResultadoPrueba({ r }: { r: AgentN8nTryOut }) {
  const pista = r.ok ? null : PISTAS[(r.error ?? "").match(/HTTP (\d{3})/)?.[1] ?? ""];
  const datos = r.respuesta;
  const respuesta =
    typeof datos === "string"
      ? datos
      : datos && typeof datos === "object" && "respuesta" in datos
        ? String((datos as { respuesta: unknown }).respuesta ?? "")
        : "";
  return (
    <div
      className={`text-xs rounded-coro-sm border p-2 ${r.ok ? "border-state-ok/40 bg-state-ok/10" : "border-state-bad/40 bg-state-bad/10"}`}
    >
      <div className="font-medium text-ink mb-1">
        {r.ok ? `Contestó en ${r.ms} ms (HTTP ${r.estado})` : r.error}
      </div>
      {pista && <div className="text-ink2 mb-1">{pista}</div>}
      {r.ok && (
        <div className="text-ink2 mb-1">
          {respuesta
            ? `Al paciente le llegaría: «${respuesta}»`
            : "Contestó sin `respuesta`: el CRM esperaría el mensaje por la API. Si tu flujo siempre contesta, en n8n → Executions mira qué nodo quedó en rojo: cuando un flujo falla, n8n devuelve 200 vacío."}
        </div>
      )}
      {datos !== null && datos !== undefined && typeof datos !== "string" && (
        <pre className="font-mono text-[11px] whitespace-pre-wrap break-words max-h-48 overflow-auto text-ink2">
          {JSON.stringify(datos, null, 2)}
        </pre>
      )}
    </div>
  );
}
