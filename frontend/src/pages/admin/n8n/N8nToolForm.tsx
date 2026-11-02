// Alta y edición de una herramienta de n8n. Lo que se escribe en «Para qué
// sirve» y en cada dato es lo que lee Sofía para decidir cuándo usarla y qué
// pedirle al paciente: se explica en la propia pantalla porque es lo que más
// cambia cómo se comporta.

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { errorDetail } from "@/lib/errors";
import type { N8nToolIn, N8nToolOut, ParametroN8n, TipoParametro } from "@/services/n8n";

const TIPOS: Array<{ value: TipoParametro; label: string }> = [
  { value: "texto", label: "Texto" },
  { value: "numero", label: "Número" },
  { value: "si_no", label: "Sí / no" },
  { value: "opciones", label: "Opciones" },
];

function separarOpciones(texto: string): string[] {
  return texto.split(",").map((o) => o.trim()).filter(Boolean);
}

function mismaLista(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, k) => x === b[k]);
}

// El campo enseña lo que se teclea, no la lista ya separada. Si enseñara la
// lista unida con «, », la coma recién escrita desaparecería al momento: «chica,»
// se separa en ["chica"] y vuelve a pintarse como «chica», y no había forma de
// escribir la segunda opción (pegada sí entraba, porque cada coma ya llevaba algo
// detrás). Si la lista cambia desde fuera (se quitó otro dato y este ocupa su
// lugar), manda la lista.
function CampoOpciones({ opciones, onChange }: { opciones: string[]; onChange: (o: string[]) => void }) {
  const [texto, setTexto] = useState(() => opciones.join(", "));
  const visible = mismaLista(separarOpciones(texto), opciones) ? texto : opciones.join(", ");
  return (
    <input
      className="input text-xs w-full"
      placeholder="Opciones separadas por comas: chica, mediana, grande"
      aria-label="Opciones"
      value={visible}
      onChange={(e) => {
        setTexto(e.target.value);
        onChange(separarOpciones(e.target.value));
      }}
    />
  );
}

const VACIA: N8nToolIn = {
  nombre: "",
  descripcion: "",
  parametros: [],
  url: "",
  accion: "escritura",
  espera_s: 8,
  datos_contacto: false,
  activa: true,
};

export function N8nToolForm({
  inicial,
  onGuardar,
  onCancelar,
}: {
  inicial: N8nToolOut | null;
  onGuardar: (datos: N8nToolIn) => Promise<void>;
  onCancelar: () => void;
}) {
  const [f, setF] = useState<N8nToolIn>(() =>
    inicial
      ? {
          nombre: inicial.nombre,
          descripcion: inicial.descripcion,
          parametros: inicial.parametros,
          url: inicial.url,
          accion: inicial.accion,
          espera_s: inicial.espera_s,
          datos_contacto: inicial.datos_contacto,
          activa: inicial.activa,
        }
      : VACIA,
  );
  const [guardando, setGuardando] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setParam(i: number, cambios: Partial<ParametroN8n>) {
    setF((x) => ({
      ...x,
      parametros: x.parametros.map((p, j) => (j === i ? { ...p, ...cambios } : p)),
    }));
  }

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      await onGuardar(f);
    } catch (e) {
      setError(errorDetail(e, "No se pudo guardar la herramienta."));
    } finally {
      setGuardando(false);
    }
  }

  return (
    <div className="card p-4 space-y-3">
      <div className="text-sm font-medium text-ink">
        {inicial ? `Editar ${inicial.nombre}` : "Nueva herramienta de n8n"}
      </div>

      <label className="block">
        <span className="block text-xs text-ink3 mb-1">Nombre</span>
        <input
          className="input w-full font-mono"
          placeholder="buscar_pedido"
          maxLength={40}
          value={f.nombre}
          onChange={(e) => setF({ ...f, nombre: e.target.value.toLowerCase().replace(/\s+/g, "_") })}
        />
        <span className="text-[11px] text-ink3">Minúsculas, números y guion bajo. Es como Sofía la llama.</span>
      </label>

      <label className="block">
        <span className="block text-xs text-ink3 mb-1">Para qué sirve</span>
        <textarea
          className="input w-full h-20 resize-y text-sm"
          placeholder="Busca un pedido por su número y devuelve en qué estado está. Úsala cuando el paciente pregunte por su pedido."
          maxLength={500}
          value={f.descripcion}
          onChange={(e) => setF({ ...f, descripcion: e.target.value })}
        />
        <span className="text-[11px] text-ink3">
          Sofía lo lee para decidir cuándo usarla: di qué hace y en qué momento.
        </span>
      </label>

      <div>
        <div className="flex items-center justify-between mb-1">
          <span className="text-xs text-ink3">Datos que pide</span>
          <button
            type="button"
            className="btn-ghost btn-sm"
            disabled={f.parametros.length >= 10}
            onClick={() =>
              setF({
                ...f,
                parametros: [...f.parametros, { clave: "", tipo: "texto", descripcion: "", obligatorio: true }],
              })
            }
          >
            <Plus className="w-3.5 h-3.5" /> Dato
          </button>
        </div>
        {f.parametros.length === 0 && (
          <p className="text-[11px] text-ink3 italic">Ninguno: Sofía la usa sin pedirle nada al paciente.</p>
        )}
        <div className="space-y-2">
          {f.parametros.map((p, i) => (
            <div key={i} className="border border-line rounded-coro-sm p-2 space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <input
                  className="input text-xs font-mono w-36"
                  placeholder="numero"
                  aria-label="Clave del dato"
                  value={p.clave}
                  onChange={(e) => setParam(i, { clave: e.target.value.toLowerCase().replace(/\s+/g, "_") })}
                />
                <select
                  className="input text-xs w-28"
                  aria-label="Tipo del dato"
                  value={p.tipo}
                  onChange={(e) => setParam(i, { tipo: e.target.value as TipoParametro })}
                >
                  {TIPOS.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
                <label className="text-[11px] text-ink2 flex items-center gap-1">
                  <input type="checkbox" checked={p.obligatorio} onChange={(e) => setParam(i, { obligatorio: e.target.checked })} />
                  obligatorio
                </label>
                <label className="text-[11px] text-ink2 flex items-center gap-1" title="No se guarda en los registros">
                  <input type="checkbox" checked={!!p.sensible} onChange={(e) => setParam(i, { sensible: e.target.checked })} />
                  sensible
                </label>
                <button
                  type="button"
                  className="btn-ghost btn-sm ml-auto"
                  aria-label="Quitar este dato"
                  onClick={() => setF({ ...f, parametros: f.parametros.filter((_, j) => j !== i) })}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
              <input
                className="input text-xs w-full"
                placeholder="Qué es (lo lee Sofía para pedírselo al paciente)"
                aria-label="Descripción del dato"
                value={p.descripcion}
                onChange={(e) => setParam(i, { descripcion: e.target.value })}
              />
              {p.tipo === "opciones" && (
                <CampoOpciones opciones={p.opciones ?? []} onChange={(opciones) => setParam(i, { opciones })} />
              )}
            </div>
          ))}
        </div>
      </div>

      <label className="block">
        <span className="block text-xs text-ink3 mb-1">URL del webhook</span>
        <input
          className="input w-full font-mono text-xs"
          placeholder="https://n8n.miclinica.com/webhook/pedidos"
          value={f.url}
          onChange={(e) => setF({ ...f, url: e.target.value.trim() })}
        />
        <span className="text-[11px] text-ink3">La «Production URL» del nodo Webhook de n8n, con https.</span>
      </label>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <fieldset>
          <legend className="text-xs text-ink3 mb-1">Tipo</legend>
          <label className="flex items-start gap-2 text-xs text-ink2 mb-1">
            <input type="radio" checked={f.accion === "escritura"} onChange={() => setF({ ...f, accion: "escritura" })} />
            <span>
              <b>Hace algo</b> (aparta, registra, manda): no se repite, y si n8n no contesta la
              conversación pasa a una persona.
            </span>
          </label>
          <label className="flex items-start gap-2 text-xs text-ink2">
            <input type="radio" checked={f.accion === "lectura"} onChange={() => setF({ ...f, accion: "lectura" })} />
            <span>
              <b>Solo consulta</b>: si falla se reintenta una vez.
            </span>
          </label>
        </fieldset>
        <div className="space-y-2">
          <label className="flex items-center gap-2 text-xs text-ink2">
            Espera máxima
            <input
              className="input w-16 text-xs"
              type="number"
              min={1}
              max={15}
              value={f.espera_s}
              onChange={(e) => setF({ ...f, espera_s: Number(e.target.value) || 8 })}
            />
            s
          </label>
          <label className="flex items-center gap-2 text-xs text-ink2">
            <input type="checkbox" checked={f.datos_contacto} onChange={(e) => setF({ ...f, datos_contacto: e.target.checked })} />
            Mandar a n8n el nombre, el teléfono y el correo del paciente
          </label>
          <label className="flex items-center gap-2 text-xs text-ink2">
            <input type="checkbox" checked={f.activa} onChange={(e) => setF({ ...f, activa: e.target.checked })} />
            Activa
          </label>
        </div>
      </div>

      {error && <p className="text-xs text-state-bad">{error}</p>}
      <div className="flex justify-end gap-2">
        <button type="button" className="btn" onClick={onCancelar} disabled={guardando}>
          Cancelar
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={() => void guardar()}
          disabled={guardando || !f.nombre.trim() || !f.descripcion.trim() || !f.url.trim()}
        >
          {guardando ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </div>
  );
}
