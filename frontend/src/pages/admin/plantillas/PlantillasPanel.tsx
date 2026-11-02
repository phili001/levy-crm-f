// Canales → Plantillas (SPEC-18): las plantillas de WhatsApp de la cuenta, con
// su estado en Meta, y crearlas desde aquí (con YCloud).
//
// El listado es en vivo contra el proveedor, como siempre: no hay copia en la
// base que se quede vieja. Una rechazada no se edita (Meta pone topes a las
// ediciones): «Corregir» abre el editor con su contenido y el nombre `_v2`.

import { useEffect, useState } from "react";
import { Lightbulb, Loader2, MessageSquareText, Pencil, Plus, RefreshCw } from "lucide-react";
import { errorDetail } from "@/lib/errors";
import { getTemplateVars, listWhatsappTemplates, type WhatsappTemplate } from "@/services/admin";
import { getPlantillasInfo, type PlantillaIn, type PlantillasInfo, type TipoBoton } from "@/services/plantillas";
import { PlantillaEditor, numerosDelCuerpo } from "./PlantillaEditor";

type Filtro = "todas" | "aprobadas" | "pendientes" | "rechazadas";

function grupo(t: WhatsappTemplate): Exclude<Filtro, "todas"> | null {
  const s = (t.status || "").toLowerCase();
  if (["approved", "active", "enabled"].includes(s)) return "aprobadas";
  if (["pending", "in_appeal"].includes(s)) return "pendientes";
  if (["rejected", "disabled", "paused"].includes(s)) return "rechazadas";
  return null;
}

const ESTADO: Record<string, { texto: string; clase: string }> = {
  approved: { texto: "Aprobada", clase: "text-state-ok border-state-ok/40" },
  pending: { texto: "En revisión", clase: "text-state-warn border-state-warn/40" },
  in_appeal: { texto: "En apelación", clase: "text-state-warn border-state-warn/40" },
  rejected: { texto: "Rechazada", clase: "text-state-bad border-state-bad/40" },
  paused: { texto: "Pausada por calidad", clase: "text-state-bad border-state-bad/40" },
  disabled: { texto: "Deshabilitada", clase: "text-state-bad border-state-bad/40" },
};

// Meta da el motivo del rechazo como un código. Los que más salen, en palabras;
// cualquier otro se enseña tal cual.
const MOTIVOS: Record<string, string> = {
  INVALID_FORMAT:
    "el formato no es válido: variables mal puestas, ejemplos que no cuadran con el texto o un mensaje repetido",
  INCORRECT_CATEGORY: "la categoría no corresponde al contenido (un aviso con tono de promoción marcado como Utilidad)",
  TAG_CONTENT_MISMATCH: "la categoría no corresponde al contenido",
  PROMOTIONAL: "tiene contenido promocional y está marcada como Utilidad",
  ABUSIVE_CONTENT: "Meta consideró el contenido abusivo o engañoso",
  SCAM: "Meta lo marcó como posible fraude",
};

function motivoLegible(reason: string): string {
  const legible = MOTIVOS[reason.trim().toUpperCase()];
  return legible ? `${legible} (${reason})` : reason;
}

const VACIA: PlantillaIn = {
  nombre: "",
  categoria: "UTILITY",
  idioma: "es_MX",
  cabecera: "",
  cuerpo: "",
  variables: [],
  pie: "",
  botones: [],
};

// recordatorio_cita_v1 → _v2; un nombre sin versión gana `_v2`. Salta los que
// ya existen en la cuenta, que Meta no deja repetir en el mismo idioma.
function siguienteNombre(nombre: string, existentes: Set<string>): string {
  const m = nombre.match(/^(.*)_v(\d+)$/);
  const base = m ? m[1] : nombre;
  let n = m ? Number(m[2]) + 1 : 2;
  while (existentes.has(`${base}_v${n}`)) n += 1;
  return `${base}_v${n}`;
}

const TIPO_DE_META: Record<string, TipoBoton> = { QUICK_REPLY: "respuesta", URL: "enlace", PHONE_NUMBER: "telefono" };

async function paraCorregir(t: WhatsappTemplate, existentes: Set<string>): Promise<PlantillaIn> {
  const cuerpo = t.body || "";
  // Las etiquetas y los enlaces a la ficha se guardaron al crearla; los
  // ejemplos no (Meta no los devuelve), así que hay que volver a ponerlos.
  const guardadas = await getTemplateVars(t.name, t.language).catch(() => []);
  return {
    nombre: siguienteNombre(t.name, existentes),
    categoria: t.category === "MARKETING" ? "MARKETING" : "UTILITY",
    idioma: t.language || "es_MX",
    cabecera: t.header_format === "text" ? t.header_text || "" : "",
    cuerpo,
    variables: numerosDelCuerpo(cuerpo).map((n) => {
      const g = guardadas.find((v) => v.idx === n);
      return { n, etiqueta: g?.nombre ?? "", ejemplo: "", campo: g?.contact_field ?? null };
    }),
    pie: t.footer || "",
    botones: (t.botones || [])
      .filter((b) => TIPO_DE_META[b.tipo])
      .slice(0, 3)
      .map((b) => ({ tipo: TIPO_DE_META[b.tipo], texto: b.texto, valor: b.valor })),
  };
}

interface Edicion {
  inicial: PlantillaIn;
  titulo: string;
  motivo?: string | null;
  // Cambia con cada apertura para que el editor arranque de cero.
  clave: number;
}

export function PlantillasPanel() {
  const [info, setInfo] = useState<PlantillasInfo | null>(null);
  const [lista, setLista] = useState<WhatsappTemplate[]>([]);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filtro, setFiltro] = useState<Filtro>("todas");
  const [verSugeridas, setVerSugeridas] = useState(false);
  const [edicion, setEdicion] = useState<Edicion | null>(null);
  const [enviada, setEnviada] = useState<string | null>(null);

  async function refrescar() {
    setCargando(true);
    setError(null);
    // Por separado: sin WhatsApp conectado el listado falla con el motivo, y
    // las sugeridas y «Nueva» tienen que seguir ahí.
    try {
      setInfo(await getPlantillasInfo());
    } catch (e) {
      setError(errorDetail(e, "No se pudo cargar la pestaña."));
    }
    try {
      setLista(await listWhatsappTemplates());
    } catch (e) {
      setError(errorDetail(e, "No se pudieron cargar las plantillas de tu cuenta."));
    } finally {
      setCargando(false);
    }
  }

  useEffect(() => {
    void refrescar();
  }, []);

  const existentes = new Set(lista.map((t) => t.name));
  const visibles = filtro === "todas" ? lista : lista.filter((t) => grupo(t) === filtro);
  const cuenta = (f: Exclude<Filtro, "todas">) => lista.filter((t) => grupo(t) === f).length;

  function abrir(inicial: PlantillaIn, titulo: string, motivo?: string | null) {
    setEnviada(null);
    setVerSugeridas(false);
    setEdicion({ inicial, titulo, motivo, clave: Date.now() });
  }

  async function corregir(t: WhatsappTemplate) {
    abrir(await paraCorregir(t, existentes), `Corregir ${t.name}`, t.reason ? motivoLegible(t.reason) : "Meta no dijo el motivo");
  }

  return (
    <div className="max-w-4xl space-y-4">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <p className="text-xs text-ink3 leading-relaxed flex-1 min-w-[240px]">
          Las plantillas son los mensajes que Meta deja mandar por WhatsApp cuando la{" "}
          <span className="font-medium text-ink2">ventana de 24 h</span> está cerrada: recordatorios, avisos,
          campañas. Las escribes aquí, Meta las revisa (de minutos a 24 h) y quedan listas para el chat, la ficha
          y Campañas.
        </p>
        <div className="flex gap-2 shrink-0">
          <button
            type="button"
            className="btn-ghost btn-sm"
            onClick={() => {
              // El aviso de «se mandó» ya cumplió: lo que importa ahora es el estado.
              setEnviada(null);
              void refrescar();
            }}
            disabled={cargando}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${cargando ? "animate-spin" : ""}`} /> Actualizar
          </button>
          {info?.puede_crear && (
            <>
              <button type="button" className="btn btn-sm" onClick={() => setVerSugeridas((v) => !v)}>
                <Lightbulb className="w-3.5 h-3.5" /> Sugeridas
              </button>
              <button type="button" className="btn-primary btn-sm" onClick={() => abrir(VACIA, "Nueva plantilla")}>
                <Plus className="w-3.5 h-3.5" /> Nueva
              </button>
            </>
          )}
        </div>
      </div>

      {info && !info.puede_crear && (
        <div className="card p-3 text-xs text-ink2">
          Tu WhatsApp va por la API de Meta: las plantillas se crean en el Administrador de WhatsApp de Meta. Aquí ves
          su estado.
        </div>
      )}
      {error && (
        <div className="text-xs text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
          {error}
        </div>
      )}
      {enviada && (
        <div className="card p-3 border-brand/50 text-sm text-ink2">
          <span className="font-medium text-ink">«{enviada}» se mandó a revisión.</span> Meta tarda de minutos a 24 h:
          vuelve a esta pestaña y dale «Actualizar» para ver si ya quedó aprobada.
        </div>
      )}

      {verSugeridas && info && (
        <div className="grid gap-2 sm:grid-cols-2">
          {info.sugeridas.map((s) => {
            const ya = lista.find((t) => t.name === s.plantilla.nombre);
            return (
              <div key={s.clave} className="card p-3 space-y-1.5">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-ink">{s.titulo}</span>
                  <span className="text-[10px] uppercase tracking-wider text-ink3 border border-line rounded px-1">
                    {s.plantilla.categoria === "MARKETING" ? "marketing" : "utilidad"}
                  </span>
                </div>
                <p className="text-xs text-ink3">{s.para_que}</p>
                {ya ? (
                  <p className="text-xs text-ink2">
                    Ya la tienes: {ESTADO[(ya.status || "").toLowerCase()]?.texto ?? ya.status}.
                  </p>
                ) : (
                  <button type="button" className="btn btn-sm" onClick={() => abrir(s.plantilla, s.titulo)}>
                    Usar esta
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {edicion && info && (
        <PlantillaEditor
          key={edicion.clave}
          inicial={edicion.inicial}
          titulo={edicion.titulo}
          motivo={edicion.motivo}
          info={info}
          onEnviada={(nombre) => {
            setEdicion(null);
            setEnviada(nombre);
            void refrescar();
          }}
          onCancelar={() => setEdicion(null)}
        />
      )}

      <div className="flex gap-1.5 flex-wrap">
        {(
          [
            ["todas", `Todas (${lista.length})`],
            ["aprobadas", `Aprobadas (${cuenta("aprobadas")})`],
            ["pendientes", `En revisión (${cuenta("pendientes")})`],
            ["rechazadas", `Rechazadas (${cuenta("rechazadas")})`],
          ] as Array<[Filtro, string]>
        ).map(([valor, texto]) => (
          <button
            key={valor}
            type="button"
            aria-pressed={filtro === valor}
            className={`btn-sm rounded-coro-sm border px-2.5 ${
              filtro === valor ? "border-brand-ink text-ink bg-card" : "border-line text-ink3"
            }`}
            onClick={() => setFiltro(valor)}
          >
            {texto}
          </button>
        ))}
      </div>

      {cargando && lista.length === 0 ? (
        <div className="flex items-center gap-2 text-ink3 text-sm py-6 justify-center">
          <Loader2 className="w-4 h-4 animate-spin" /> Cargando…
        </div>
      ) : visibles.length === 0 ? (
        <div className="card p-6 text-center text-sm text-ink3">
          <MessageSquareText className="w-5 h-5 mx-auto mb-2 text-ink3" />
          {lista.length === 0 ? "Todavía no hay plantillas en tu cuenta." : "Ninguna en este filtro."}
        </div>
      ) : (
        <ul className="space-y-2">
          {visibles.map((t) => {
            const estado = ESTADO[(t.status || "").toLowerCase()];
            return (
              <li key={`${t.name}:${t.language}`} className="card p-3">
                <div className="flex items-start gap-3">
                  <div className="flex-1 min-w-0 space-y-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-sm text-ink">{t.name}</span>
                      <span className="text-[11px] text-ink3">{t.language}</span>
                      {t.category && (
                        <span className="text-[10px] uppercase tracking-wider text-ink3 border border-line rounded px-1">
                          {t.category === "MARKETING" ? "marketing" : t.category === "UTILITY" ? "utilidad" : t.category.toLowerCase()}
                        </span>
                      )}
                      <span className={`text-[10px] uppercase tracking-wider border rounded px-1 ${estado?.clase ?? "text-ink3 border-line"}`}>
                        {estado?.texto ?? t.status ?? "sin estado"}
                      </span>
                    </div>
                    {t.body && <div className="text-xs text-ink2 line-clamp-3 whitespace-pre-wrap">{t.body}</div>}
                    {t.reason && grupo(t) === "rechazadas" && (
                      <div className="text-xs text-state-bad">Por qué: {motivoLegible(t.reason)}.</div>
                    )}
                  </div>
                  {info?.puede_crear && grupo(t) === "rechazadas" && (
                    <button type="button" className="btn-ghost btn-sm shrink-0" onClick={() => void corregir(t)}>
                      <Pencil className="w-3.5 h-3.5" /> Corregir
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
