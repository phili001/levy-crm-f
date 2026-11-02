import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, ImageUp, RotateCcw, Save, Trash2, Undo2 } from "lucide-react";

import { ConfirmModal } from "@/components/ConfirmModal";
import { PageHeader } from "@/components/PageHeader";
import {
  COLOR_DE_FABRICA,
  PALETAS,
  TIPOGRAFIA_DE_FABRICA,
  TRAZO_PX,
  derivarTonos,
  hexARgb,
  type Apariencia,
} from "@/lib/apariencia";
import { nombreDeInstalacion } from "@/lib/appName";
import { errorDetail } from "@/lib/errors";
import {
  guardarApariencia,
  quitarLogo,
  restablecerApariencia,
  subirLogo,
  type AparienciaEditable,
} from "@/services/apariencia";
import { useApariencia, useMarca } from "@/store/apariencia";
import { VistaPrevia } from "@/pages/admin/apariencia/VistaPrevia";

const LOGO_MAX_BYTES = 256 * 1024;
const TRAZOS: Apariencia["trazo_iconos"][] = ["fino", "normal", "grueso"];
const TIPOGRAFIAS_DE_RESPALDO = [TIPOGRAFIA_DE_FABRICA];

const DE_FABRICA: AparienciaEditable = {
  nombre: null,
  paleta: "cobalto",
  color_marca: COLOR_DE_FABRICA,
  tipografia: TIPOGRAFIA_DE_FABRICA,
  trazo_iconos: "normal",
};

function editable(ap: Apariencia | null): AparienciaEditable {
  if (!ap) return DE_FABRICA;
  const { nombre, paleta, color_marca, tipografia, trazo_iconos } = ap;
  return { nombre, paleta, color_marca, tipografia, trazo_iconos };
}

const iguales = (a: AparienciaEditable, b: AparienciaEditable) =>
  (a.nombre || null) === (b.nombre || null) &&
  a.paleta === b.paleta &&
  a.color_marca.toUpperCase() === b.color_marca.toUpperCase() &&
  a.tipografia === b.tipografia &&
  a.trazo_iconos === b.trazo_iconos;

// Hoja de Google Fonts SOLO con las letras de las muestras (`text=`): así se
// ven las ocho tipografías en su propia letra sin descargar ocho familias
// completas cada vez que alguien abre esta pantalla.
function useMuestrasDeTipografia(tipografias: string[]) {
  useEffect(() => {
    const letras = Array.from(new Set(("Aa" + tipografias.join("")).split(""))).join("");
    const familias = tipografias
      .map((t) => `family=${encodeURIComponent(t).replace(/%20/g, "+")}:wght@700`)
      .join("&");
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = `https://fonts.googleapis.com/css2?${familias}&text=${encodeURIComponent(letras)}&display=swap`;
    document.head.appendChild(link);
    return () => link.remove();
  }, [tipografias]);
}

export default function AparienciaPage() {
  const guardada = useApariencia((s) => s.guardada);
  const fijar = useApariencia((s) => s.fijar);
  const previsualizar = useApariencia((s) => s.previsualizar);
  const { logoSrc } = useMarca();

  const [form, setForm] = useState<AparienciaEditable>(() => editable(guardada));
  const [hex, setHex] = useState(form.color_marca);
  const [guardando, setGuardando] = useState(false);
  const [guardado, setGuardado] = useState(false);
  const [subiendo, setSubiendo] = useState(false);
  const [confirmarReset, setConfirmarReset] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ficheroRef = useRef<HTMLInputElement>(null);

  const tipografias = guardada?.tipografias?.length ? guardada.tipografias : TIPOGRAFIAS_DE_RESPALDO;
  useMuestrasDeTipografia(tipografias);

  // Si la guardada llega después de montar (primera visita, sin caché), el
  // formulario se pone al día, salvo que ya se haya empezado a editar.
  const base = editable(guardada);
  const baseRef = useRef(base);
  useEffect(() => {
    if (iguales(form, baseRef.current)) {
      setForm(base);
      setHex(base.color_marca);
    }
    baseRef.current = base;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guardada]);

  const cambios = !iguales(form, base);

  // La vista previa es el panel entero: cada cambio se aplica al documento.
  // Al salir de la página sin guardar, vuelve la apariencia guardada.
  useEffect(() => {
    previsualizar(form);
  }, [form, previsualizar]);
  useEffect(() => () => previsualizar(null), [previsualizar]);

  const tonos = useMemo(() => derivarTonos(form.color_marca), [form.color_marca]);
  const poco = tonos && tonos.contrasteSobrePapel < 3;

  function cambiar(parcial: Partial<AparienciaEditable>) {
    setGuardado(false);
    setForm((f) => ({ ...f, ...parcial }));
  }

  function elegirColor(valor: string, paleta = "personalizada") {
    setHex(valor);
    if (hexARgb(valor)) {
      const normal = valor.startsWith("#") ? valor : `#${valor}`;
      cambiar({ color_marca: normal.toUpperCase(), paleta });
    }
  }

  async function guardar() {
    setGuardando(true);
    setError(null);
    try {
      fijar(await guardarApariencia({ ...form, nombre: form.nombre?.trim() || null }));
      setGuardado(true);
    } catch (e) {
      setError(errorDetail(e, "No se pudo guardar la apariencia. Vuelve a intentarlo."));
    } finally {
      setGuardando(false);
    }
  }

  async function restablecer() {
    setGuardando(true);
    setError(null);
    try {
      const ap = await restablecerApariencia();
      fijar(ap);
      setForm(editable(ap));
      setHex(ap.color_marca);
      setConfirmarReset(false);
    } catch (e) {
      setError(errorDetail(e, "No se pudo restablecer. Vuelve a intentarlo."));
    } finally {
      setGuardando(false);
    }
  }

  async function alElegirLogo(fichero: File | undefined) {
    if (ficheroRef.current) ficheroRef.current.value = "";
    if (!fichero) return;
    setError(null);
    if (fichero.size > LOGO_MAX_BYTES) {
      setError("El logotipo no puede pasar de 256 KB. Exporta una versión más ligera.");
      return;
    }
    setSubiendo(true);
    try {
      // El logotipo se guarda al momento, aparte del resto: el servidor
      // devuelve la apariencia GUARDADA, así que se reaplica lo que se esté
      // editando para no perder la vista previa.
      fijar(await subirLogo(fichero));
      previsualizar(form);
    } catch (e) {
      setError(errorDetail(e, "No se pudo subir el logotipo."));
    } finally {
      setSubiendo(false);
    }
  }

  async function alQuitarLogo() {
    setSubiendo(true);
    setError(null);
    try {
      fijar(await quitarLogo());
      previsualizar(form);
    } catch (e) {
      setError(errorDetail(e, "No se pudo quitar el logotipo."));
    } finally {
      setSubiendo(false);
    }
  }

  const nombreVisible = form.nombre?.trim() || nombreDeInstalacion();

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="Apariencia"
        description="Nombre, logotipo, color y letra del panel. Los ve todo tu equipo, también en la pantalla de entrada."
        actions={
          <>
            {cambios && (
              <button type="button" className="btn-ghost" onClick={() => { setForm(base); setHex(base.color_marca); }}>
                <Undo2 /> Descartar
              </button>
            )}
            <button type="button" className="btn-primary" onClick={guardar} disabled={guardando || !cambios}>
              {guardado && !cambios ? <Check /> : <Save />}
              {guardando ? "Guardando…" : guardado && !cambios ? "Guardado" : "Guardar"}
            </button>
          </>
        }
      />

      <div className="flex-1 overflow-auto p-4 md:p-6">
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,440px)] max-w-6xl">
          <div className="space-y-4 min-w-0">
            {error && (
              <div className="text-sm text-state-bad bg-state-bad/10 border border-state-bad/30 rounded-coro-sm px-3 py-2">
                {error}
              </div>
            )}

            <section className="card p-4 space-y-4">
              <div>
                <label className="label" htmlFor="ap-nombre">Nombre</label>
                <input
                  id="ap-nombre"
                  className="input"
                  value={form.nombre ?? ""}
                  maxLength={60}
                  placeholder={nombreDeInstalacion()}
                  onChange={(e) => cambiar({ nombre: e.target.value })}
                />
                <p className="text-[11px] text-ink3 mt-1">
                  Sale en el menú, en la entrada y en la pestaña del navegador. La primera palabra va
                  grande y el resto debajo, en pequeño.
                </p>
              </div>

              <div>
                <span className="label">Logotipo</span>
                <div className="flex flex-wrap items-center gap-3">
                  <div className="w-14 h-14 rounded-coro-sm border border-line bg-paper2 flex items-center justify-center p-1.5">
                    {logoSrc ? (
                      <img src={logoSrc} alt="" className="max-w-full max-h-full object-contain" />
                    ) : (
                      <span className="text-[10px] text-ink4 text-center leading-tight">De fábrica</span>
                    )}
                  </div>
                  <input
                    ref={ficheroRef}
                    type="file"
                    accept="image/png,image/webp,image/jpeg,image/svg+xml"
                    className="hidden"
                    onChange={(e) => void alElegirLogo(e.target.files?.[0])}
                  />
                  <button type="button" className="btn btn-sm" disabled={subiendo} onClick={() => ficheroRef.current?.click()}>
                    <ImageUp /> {subiendo ? "Subiendo…" : logoSrc ? "Cambiar" : "Subir logotipo"}
                  </button>
                  {logoSrc && (
                    <button type="button" className="btn-ghost btn-sm" disabled={subiendo} onClick={() => void alQuitarLogo()}>
                      <Trash2 /> Quitar
                    </button>
                  )}
                </div>
                <p className="text-[11px] text-ink3 mt-1">
                  Mejor el símbolo solo (cuadrado) que el logotipo con letras: se ve en un hueco
                  pequeño. PNG, WebP, JPG o SVG, hasta 256 KB. Se guarda al subirlo.
                </p>
              </div>
            </section>

            <section className="card p-4">
              <span className="label">Color de marca</span>
              <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
                {PALETAS.map((p) => {
                  const activa = form.paleta === p.id && form.color_marca.toUpperCase() === p.color;
                  return (
                    <button
                      key={p.id}
                      type="button"
                      aria-pressed={activa}
                      onClick={() => elegirColor(p.color, p.id)}
                      className={
                        "rounded-coro-sm border p-2 text-left transition-colors " +
                        (activa ? "border-brand-ink ring-2 ring-brand/30" : "border-line hover:bg-paper2")
                      }
                    >
                      <span className="block h-7 rounded-[4px]" style={{ background: p.color }} />
                      <span className="block text-[11px] text-ink2 mt-1.5">{p.nombre}</span>
                    </button>
                  );
                })}
              </div>

              <div className="flex flex-wrap items-center gap-2 mt-3">
                <input
                  type="color"
                  aria-label="Elegir un color propio"
                  value={form.color_marca.toLowerCase()}
                  onChange={(e) => elegirColor(e.target.value)}
                  className="h-10 w-12 cursor-pointer rounded-coro-sm border border-line bg-card p-1"
                />
                <input
                  className="input font-mono w-32"
                  aria-label="Color en hexadecimal"
                  value={hex}
                  maxLength={7}
                  onChange={(e) => elegirColor(e.target.value)}
                  onBlur={() => setHex(form.color_marca)}
                />
                <span className="text-[11px] text-ink3">O escribe el de tu marca, como #2350E6.</span>
              </div>

              {poco && tonos && (
                <div className="mt-3 flex gap-2 text-xs text-ink2 bg-state-warn/10 border border-state-warn/40 rounded-coro-sm px-3 py-2">
                  <AlertTriangle className="w-4 h-4 shrink-0 text-state-warn" />
                  <span>
                    Con este color los botones se distinguen poco del fondo claro (
                    {tonos.contrasteSobrePapel.toFixed(1)}:1; lo recomendable es 3:1). Los textos en
                    color de marca se oscurecen solos para leerse bien, pero los botones usan el color
                    tal cual.
                  </span>
                </div>
              )}
            </section>

            <section className="card p-4">
              <span className="label">Letra</span>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {tipografias.map((t) => (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={form.tipografia === t}
                    onClick={() => cambiar({ tipografia: t })}
                    className={
                      "rounded-coro-sm border px-3 py-2.5 text-left transition-colors " +
                      (form.tipografia === t ? "border-brand-ink ring-2 ring-brand/30" : "border-line hover:bg-paper2")
                    }
                  >
                    <span className="block text-2xl font-bold text-ink leading-none" style={{ fontFamily: `"${t}", system-ui` }}>
                      Aa
                    </span>
                    {/* El nombre, en la letra del panel: de cada muestra solo se
                        descargan las letras de "Aa" y del nombre en negrita. */}
                    <span className="block text-[11px] text-ink2 mt-1.5 truncate">
                      {t}
                    </span>
                  </button>
                ))}
              </div>
            </section>

            <section className="card p-4">
              <span className="label">Grosor de los iconos</span>
              <div className="grid grid-cols-3 gap-2">
                {TRAZOS.map((t) => (
                  <button
                    key={t}
                    type="button"
                    aria-pressed={form.trazo_iconos === t}
                    onClick={() => cambiar({ trazo_iconos: t })}
                    className={
                      "rounded-coro-sm border px-3 py-2.5 transition-colors " +
                      (form.trazo_iconos === t ? "border-brand-ink ring-2 ring-brand/30" : "border-line hover:bg-paper2")
                    }
                  >
                    {/* Trazo en `style` y no en el atributo: el CSS global de
                        --icono-trazo pisa el atributo, y aquí cada muestra
                        tiene que enseñar SU opción, no la aplicada. */}
                    <span className="flex justify-center gap-2 text-ink">
                      <Save className="w-5 h-5" style={{ strokeWidth: TRAZO_PX[t] }} />
                      <ImageUp className="w-5 h-5" style={{ strokeWidth: TRAZO_PX[t] }} />
                      <RotateCcw className="w-5 h-5" style={{ strokeWidth: TRAZO_PX[t] }} />
                    </span>
                    <span className="block text-[11px] text-ink2 mt-1.5 capitalize">{t}</span>
                  </button>
                ))}
              </div>
            </section>

            <div className="flex justify-end">
              <button type="button" className="btn-ghost btn-sm" onClick={() => setConfirmarReset(true)}>
                <RotateCcw /> Volver a la de fábrica
              </button>
            </div>
          </div>

          <div className="lg:sticky lg:top-0 self-start">
            <VistaPrevia nombre={nombreVisible} />
            <p className="text-[11px] text-ink3 mt-2">
              Todo el panel cambia mientras editas. Si sales sin guardar, vuelve como estaba.
            </p>
          </div>
        </div>
      </div>

      <ConfirmModal
        open={confirmarReset}
        title="Volver a la apariencia de fábrica"
        description="Se quitan el nombre, el logotipo, el color, la letra y el grosor de los iconos que hayas puesto. El panel vuelve a como venía."
        confirmLabel="Restablecer"
        tone="danger"
        busy={guardando}
        onConfirm={() => void restablecer()}
        onCancel={() => setConfirmarReset(false)}
      />
    </div>
  );
}
