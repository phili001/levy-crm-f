// Editor de una plantilla de WhatsApp (Canales → Plantillas, SPEC-18).
//
// Lo que se escribe se queda aquí hasta «Enviar a aprobación»: no hay
// borradores en la base, YCloud es donde vive la plantilla. Los avisos de abajo
// son las reglas que Meta rechaza siempre; el servidor las vuelve a comprobar
// antes de mandarla, y es el que tiene la última palabra.

import { useRef, useState } from "react";
import { Loader2, Plus, Send, Sparkles, Trash2 } from "lucide-react";
import { errorDetail } from "@/lib/errors";
import { CONTACT_FIELD_OPTIONS } from "@/services/admin";
import {
  crearPlantilla,
  generarPlantilla,
  type BotonPlantilla,
  type CategoriaPlantilla,
  type PlantillaIn,
  type PlantillasInfo,
  type TipoBoton,
  type VariablePlantilla,
} from "@/services/plantillas";
import { VistaPrevia } from "./VistaPrevia";

const VARIABLE_RE = /\{\{\s*(\d+)\s*\}\}/g;

export function numerosDelCuerpo(cuerpo: string): number[] {
  const nums = [...cuerpo.matchAll(VARIABLE_RE)].map((m) => Number(m[1]));
  return Array.from(new Set(nums)).sort((a, b) => a - b);
}

// Lo que acepta Meta en el nombre. No recorta los guiones bajos de los bordes
// mientras se escribe: «recordatorio_» es el paso previo a «recordatorio_cita».
function limpiarNombre(texto: string): string {
  return texto
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9_]+/g, "_")
    .replace(/_+/g, "_");
}

// Las variables siguen al cuerpo: una fila por cada {{n}} que aparece, y se
// conserva lo que ya se había escrito de cada una.
function conVariables(p: PlantillaIn, cuerpo: string): PlantillaIn {
  const variables: VariablePlantilla[] = numerosDelCuerpo(cuerpo).map(
    (n) => p.variables.find((v) => v.n === n) ?? { n, etiqueta: "", ejemplo: "", campo: null },
  );
  return { ...p, cuerpo, variables };
}

function avisosLocales(p: PlantillaIn): string[] {
  const avisos: string[] = [];
  const cuerpo = p.cuerpo.trim();
  if (!/^[a-z0-9_]+$/.test(p.nombre)) avisos.push("Ponle nombre: minúsculas, números y guion bajo.");
  if (!cuerpo) avisos.push("Escribe el mensaje.");
  const nums = numerosDelCuerpo(cuerpo);
  if (nums.some((n, i) => n !== i + 1)) avisos.push("Las variables van seguidas desde {{1}}.");
  if (cuerpo.startsWith("{{") || cuerpo.endsWith("}}"))
    avisos.push("El mensaje no puede empezar ni terminar con una variable (Meta la rechaza).");
  if (/\}\}\s*\{\{/.test(cuerpo)) avisos.push("Dos variables no pueden ir pegadas.");
  for (const v of p.variables) {
    if (!v.ejemplo.trim()) avisos.push(`Falta el ejemplo de {{${v.n}}}: Meta lo pide para revisarla.`);
  }
  if (/\{\{/.test(p.cabecera) || /\{\{/.test(p.pie)) avisos.push("La cabecera y el pie no llevan variables.");
  for (const b of p.botones) {
    if (!b.texto.trim()) avisos.push("Cada botón necesita su texto.");
    if (b.tipo === "enlace" && !/^https:\/\/\S+$/.test(b.valor)) avisos.push(`El enlace de «${b.texto}» empieza con https://.`);
    if (b.tipo === "telefono" && !/^\+\d{8,15}$/.test(b.valor.replace(/\s/g, "")))
      avisos.push(`El teléfono de «${b.texto}» va con lada: +52…`);
  }
  return avisos;
}

const TIPOS_BOTON: Array<{ value: TipoBoton; label: string }> = [
  { value: "respuesta", label: "Respuesta rápida" },
  { value: "enlace", label: "Enlace" },
  { value: "telefono", label: "Llamar" },
];

const AYUDA_CATEGORIA: Record<CategoriaPlantilla, string> = {
  UTILITY:
    "Avisos de algo que la persona pidió o acordó: una cita, un pedido. Es la más barata y, con la ventana de 24 h abierta, no se cobra.",
  MARKETING:
    "Ofertas, promociones o «¿sigues interesado?». Meta la cobra más y conviene ofrecer la baja en el pie.",
};

export function PlantillaEditor({
  inicial,
  titulo,
  motivo,
  info,
  onEnviada,
  onCancelar,
}: {
  inicial: PlantillaIn;
  titulo: string;
  // Por qué Meta rechazó la versión anterior, si se está corrigiendo una.
  motivo?: string | null;
  info: PlantillasInfo;
  onEnviada: (nombre: string) => void;
  onCancelar: () => void;
}) {
  const [f, setF] = useState<PlantillaIn>(inicial);
  const [descripcion, setDescripcion] = useState("");
  const [generando, setGenerando] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const cuerpoRef = useRef<HTMLTextAreaElement>(null);

  const avisos = avisosLocales(f);
  const camposEnlazables = CONTACT_FIELD_OPTIONS.filter((o) => !o.value || info.campos.includes(o.value));

  function setVariable(n: number, cambios: Partial<VariablePlantilla>) {
    setF((x) => ({ ...x, variables: x.variables.map((v) => (v.n === n ? { ...v, ...cambios } : v)) }));
  }

  function setBoton(i: number, cambios: Partial<BotonPlantilla>) {
    setF((x) => ({ ...x, botones: x.botones.map((b, j) => (j === i ? { ...b, ...cambios } : b)) }));
  }

  function anadirVariable() {
    const n = (numerosDelCuerpo(f.cuerpo).at(-1) ?? 0) + 1;
    const el = cuerpoRef.current;
    const pos = el ? el.selectionStart : f.cuerpo.length;
    const cuerpo = `${f.cuerpo.slice(0, pos)}{{${n}}}${f.cuerpo.slice(pos)}`;
    setF((x) => conVariables(x, cuerpo));
    window.setTimeout(() => {
      el?.focus();
      el?.setSelectionRange(pos + `{{${n}}}`.length, pos + `{{${n}}}`.length);
    }, 0);
  }

  async function generar() {
    setGenerando(true);
    setError(null);
    try {
      const { plantilla } = await generarPlantilla(descripcion.trim(), null, f.idioma);
      setF(plantilla);
    } catch (e) {
      setError(errorDetail(e, "No se pudo generar la plantilla."));
    } finally {
      setGenerando(false);
    }
  }

  async function enviar() {
    setEnviando(true);
    setError(null);
    try {
      const creada = await crearPlantilla(f);
      onEnviada(creada.nombre);
    } catch (e) {
      setError(errorDetail(e, "No se pudo enviar la plantilla."));
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="card p-4 space-y-4">
      <div className="text-sm font-medium text-ink">{titulo}</div>
      {motivo && (
        <div className="text-xs text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
          Meta rechazó la anterior: <span className="font-medium">{motivo}</span>. Corrígela y se manda con otro nombre.
        </div>
      )}

      <div className="rounded-coro-sm border border-line p-3 space-y-2">
        <label className="block">
          <span className="flex items-center gap-1.5 text-xs text-ink2 mb-1">
            <Sparkles className="w-3.5 h-3.5" /> Generar con IA
          </span>
          <textarea
            className="input w-full text-sm"
            rows={2}
            maxLength={500}
            placeholder="Recordarle al paciente su cita un día antes y que confirme o pida cambiarla"
            value={descripcion}
            onChange={(e) => setDescripcion(e.target.value)}
          />
        </label>
        <button
          type="button"
          className="btn btn-sm"
          onClick={() => void generar()}
          disabled={generando || descripcion.trim().length < 10}
        >
          {generando ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Sparkles className="w-3.5 h-3.5" />}
          {generando ? "Escribiendo…" : "Generar"}
        </button>
        <span className="text-[11px] text-ink3 ml-2">Rellena todo lo de abajo; luego lo ajustas a mano.</span>
      </div>

      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_260px]">
        <div className="space-y-3 min-w-0">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_130px_110px]">
            <label className="block min-w-0">
              <span className="block text-xs text-ink3 mb-1">Nombre</span>
              <input
                className="input w-full font-mono text-sm"
                placeholder="recordatorio_cita_v1"
                maxLength={512}
                value={f.nombre}
                onChange={(e) => setF({ ...f, nombre: limpiarNombre(e.target.value) })}
              />
            </label>
            <label className="block">
              <span className="block text-xs text-ink3 mb-1">Categoría</span>
              <select
                className="input w-full text-sm"
                value={f.categoria}
                onChange={(e) => setF({ ...f, categoria: e.target.value as CategoriaPlantilla })}
              >
                <option value="UTILITY">Utilidad</option>
                <option value="MARKETING">Marketing</option>
              </select>
            </label>
            <label className="block">
              <span className="block text-xs text-ink3 mb-1">Idioma</span>
              <select className="input w-full text-sm" value={f.idioma} onChange={(e) => setF({ ...f, idioma: e.target.value })}>
                {info.idiomas.map((i) => (
                  <option key={i} value={i}>{i}</option>
                ))}
              </select>
            </label>
          </div>
          <p className="text-[11px] text-ink3 -mt-1">{AYUDA_CATEGORIA[f.categoria]}</p>

          <label className="block">
            <span className="block text-xs text-ink3 mb-1">Cabecera (opcional)</span>
            <input
              className="input w-full text-sm"
              maxLength={60}
              placeholder="Tu cita en Clínica Sonrisa"
              value={f.cabecera}
              onChange={(e) => setF({ ...f, cabecera: e.target.value })}
            />
          </label>

          <div>
            <div className="flex items-end justify-between mb-1">
              <span className="text-xs text-ink3">Mensaje</span>
              <button type="button" className="btn-ghost btn-sm" onClick={anadirVariable}>
                <Plus className="w-3.5 h-3.5" /> Añadir variable
              </button>
            </div>
            <textarea
              ref={cuerpoRef}
              className="input w-full text-sm"
              rows={5}
              maxLength={1024}
              placeholder="Hola {{1}}, te recordamos tu cita el {{2}}. ¿Nos confirmas?"
              value={f.cuerpo}
              onChange={(e) => setF(conVariables(f, e.target.value))}
            />
            <span className="text-[11px] text-ink3">{f.cuerpo.length}/1024</span>
          </div>

          {f.variables.length > 0 && (
            <div className="space-y-2">
              <span className="block text-xs text-ink3">Variables</span>
              {f.variables.map((v) => (
                <div key={v.n} className="grid gap-2 sm:grid-cols-[40px_minmax(0,1fr)_minmax(0,1fr)_140px] items-center">
                  <span className="font-mono text-xs text-ink2">{`{{${v.n}}}`}</span>
                  <input
                    className="input text-xs w-full"
                    placeholder="Qué es (Nombre del paciente)"
                    maxLength={60}
                    value={v.etiqueta}
                    onChange={(e) => setVariable(v.n, { etiqueta: e.target.value })}
                  />
                  <input
                    className="input text-xs w-full"
                    placeholder="Ejemplo para Meta (Ana)"
                    maxLength={200}
                    value={v.ejemplo}
                    onChange={(e) => setVariable(v.n, { ejemplo: e.target.value })}
                  />
                  <select
                    className="input text-xs w-full"
                    aria-label="Se rellena con"
                    value={v.campo ?? ""}
                    onChange={(e) => setVariable(v.n, { campo: e.target.value || null })}
                  >
                    {camposEnlazables.map((o) => (
                      <option key={o.value} value={o.value}>{o.value ? `Ficha: ${o.label}` : "A mano"}</option>
                    ))}
                  </select>
                </div>
              ))}
            </div>
          )}

          <label className="block">
            <span className="block text-xs text-ink3 mb-1">Pie (opcional)</span>
            <input
              className="input w-full text-sm"
              maxLength={60}
              placeholder="Responde BAJA si no quieres más mensajes."
              value={f.pie}
              onChange={(e) => setF({ ...f, pie: e.target.value })}
            />
          </label>

          <div className="space-y-2">
            <div className="flex items-end justify-between">
              <span className="text-xs text-ink3">Botones (hasta 3)</span>
              {f.botones.length < 3 && (
                <button
                  type="button"
                  className="btn-ghost btn-sm"
                  onClick={() => setF({ ...f, botones: [...f.botones, { tipo: "respuesta", texto: "", valor: "" }] })}
                >
                  <Plus className="w-3.5 h-3.5" /> Botón
                </button>
              )}
            </div>
            {f.botones.map((b, i) => (
              <div key={i} className="grid gap-2 sm:grid-cols-[140px_minmax(0,1fr)_minmax(0,1fr)_32px] items-center">
                <select className="input text-xs w-full" value={b.tipo} onChange={(e) => setBoton(i, { tipo: e.target.value as TipoBoton })}>
                  {TIPOS_BOTON.map((t) => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
                <input
                  className="input text-xs w-full"
                  placeholder="Texto del botón"
                  maxLength={25}
                  value={b.texto}
                  onChange={(e) => setBoton(i, { texto: e.target.value })}
                />
                {b.tipo === "respuesta" ? (
                  <span className="text-[11px] text-ink3">Lo que toque le llega a Sofía.</span>
                ) : (
                  <input
                    className="input text-xs w-full"
                    placeholder={b.tipo === "enlace" ? "https://…" : "+52 998 123 4567"}
                    value={b.valor}
                    onChange={(e) => setBoton(i, { valor: e.target.value })}
                  />
                )}
                <button
                  type="button"
                  className="btn-ghost btn-sm text-state-bad"
                  aria-label="Quitar el botón"
                  onClick={() => setF({ ...f, botones: f.botones.filter((_, j) => j !== i) })}
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        </div>

        <div className="md:sticky md:top-0 self-start">
          <span className="block text-xs text-ink3 mb-1">Así la ve el paciente</span>
          <VistaPrevia p={f} />
        </div>
      </div>

      {avisos.length > 0 && (
        <ul className="text-xs text-state-warn space-y-0.5 list-disc pl-4">
          {avisos.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
      {error && <div className="text-xs text-state-bad">{error}</div>}

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary" onClick={() => void enviar()} disabled={enviando || avisos.length > 0}>
          {enviando ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          Enviar a aprobación
        </button>
        <button type="button" className="btn-ghost" onClick={onCancelar}>
          Cancelar
        </button>
        <span className="text-[11px] text-ink3">Meta la revisa en minutos o hasta 24 h. Una vez enviada ya no se edita.</span>
      </div>
    </div>
  );
}
